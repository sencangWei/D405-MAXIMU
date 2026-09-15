from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "rk3576" / "collector" / "adapter"
COLLECTOR = ROOT / "rk3576" / "collector"


@pytest.fixture()
def umi_modules(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(ADAPTER))
    for name in ("umi_recorderctl", "umi_publish"):
        sys.modules.pop(name, None)
    publish = importlib.import_module("umi_publish")
    recorderctl = importlib.import_module("umi_recorderctl")
    return recorderctl, publish


def test_explicit_unit_identity_reaches_existing_app_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    umi_modules,
) -> None:
    recorderctl, _ = umi_modules
    stm32 = tmp_path / "serial-by-id"
    monkeypatch.setenv("UMI_DEVICE_ID", "umi-rk3576-161")
    monkeypatch.setenv("UMI_D405_SDK_SERIAL", "sdk-serial-161")
    monkeypatch.setenv("UMI_D405_USB_SERIAL", "usb-serial-161")
    monkeypatch.setenv("UMI_STM32_PORT", str(stm32))
    monkeypatch.setenv("UMI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("UMI_RECORDING_ROOT", str(tmp_path / "recordings"))

    config = recorderctl.Config()
    identity = recorderctl.identity(config)

    assert config.sdk_serial == "sdk-serial-161"
    assert config.usb_serial == "usb-serial-161"
    assert config.stm32_port == str(stm32)
    assert identity["device_id"] == "umi-rk3576-161"
    assert identity["model"] == "UMI-D405-RK3576"
    assert identity["capabilities"] == [
        "capture_timing_v1",
        "preview_v1",
        "preview_shared_media_v1",
        "catalog_v1",
        "resumable_transfer_v1",
    ]


def test_hardware_identity_configuration_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    umi_modules,
) -> None:
    recorderctl, _ = umi_modules
    for name in (
        "UMI_DEVICE_ID",
        "UMI_D405_SDK_SERIAL",
        "UMI_D405_USB_SERIAL",
        "UMI_STM32_PORT",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(recorderctl.ControllerError, match="must be explicitly configured"):
        recorderctl.Config()


def test_stale_active_job_is_recovered_without_overwriting_another_current_job(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    umi_modules,
) -> None:
    recorderctl, _ = umi_modules
    monkeypatch.setenv("UMI_DEVICE_ID", "umi-rk3576-161")
    monkeypatch.setenv("UMI_D405_SDK_SERIAL", "sdk-serial-161")
    monkeypatch.setenv("UMI_D405_USB_SERIAL", "usb-serial-161")
    monkeypatch.setenv("UMI_STM32_PORT", str(tmp_path / "stm32"))
    monkeypatch.setenv("UMI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("UMI_RECORDING_ROOT", str(tmp_path / "recordings"))
    config = recorderctl.Config()
    config.prepare()
    stale = {
        "job_id": "umi-stale",
        "boot_id": "earlier-boot",
        "state": "recording",
        "record_pid": 999999,
        "record_start_ticks": 1,
    }
    current = {"job_id": "umi-current", "boot_id": config.boot_id, "state": "complete_local"}
    recorderctl.atomic_json(config.jobs / "umi-stale.json", stale)
    recorderctl.atomic_json(config.current, current)

    recovered = recorderctl.reconcile_stale_state(config, stale)

    assert recovered["state"] == "interrupted"
    assert recovered["recovery_reason"] == "STALE_PROCESS_IDENTITY"
    assert recorderctl.read_json(config.jobs / "umi-stale.json")["state"] == "interrupted"
    assert recorderctl.read_json(config.current) == current


def test_process_identity_uses_pid_start_ticks(monkeypatch: pytest.MonkeyPatch, umi_modules) -> None:
    recorderctl, _ = umi_modules
    monkeypatch.setattr(recorderctl, "proc_ticks", lambda pid: 1234 if pid == 77 else 0)

    assert recorderctl.process_matches({"record_pid": 77, "record_start_ticks": 1234}) is True
    assert recorderctl.process_matches({"record_pid": 77, "record_start_ticks": 1235}) is False


def test_recording_root_cannot_change_device_owner(tmp_path: Path, umi_modules) -> None:
    _, publish = umi_modules
    root = tmp_path / "recordings"

    publish.ensure_recording_root(root, "umi-rk3576-161")
    publish.ensure_recording_root(root, "umi-rk3576-161")

    with pytest.raises(ValueError, match="belongs to another device"):
        publish.ensure_recording_root(root, "umi-rk3576-other")


def test_publish_rejects_any_stm32_device_loss_flag(tmp_path: Path, umi_modules) -> None:
    _, publish = umi_modules
    recording_root = tmp_path / "recordings"
    session = recording_root / "incoming" / "rk3576-rsusb-20260915T010203Z-deadbeef"
    session.mkdir(parents=True)
    stm32_metrics = {name: 0 for name in publish.STM32_ZERO_METRICS}
    stm32_metrics.update(observed_rate_hz=400.0, imu_queue_overflow_flags=1)
    (session / "manifest.json").write_text(
        json.dumps(
            {
                "status": "SEALED",
                "session_id": session.name,
                "counts": {"ir_frames": 30, "rgb_input_frames": 30, "stm32_packets": 400},
                "metrics": {"formal_host_span_s": 1.0, "stm32": stm32_metrics},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="cannot prove STEREO_IMU completion"):
        publish.publish_session(
            session=session,
            recording_root=recording_root,
            catalog_db_path=tmp_path / "catalog.sqlite3",
            device_id="umi-rk3576-161",
            job_id="umi-job",
            request_id="request-id",
            boot_id="boot-id",
        )


def test_prepared_publication_is_recovered_into_catalog(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    umi_modules,
) -> None:
    _, publish = umi_modules
    root = tmp_path / "recordings"
    recording_id = "recording_rk3576-rsusb-20260915T010203Z-deadbeef"
    prepared = root / "recordings-v2" / "completed" / f".{recording_id}.publishing"
    prepared.mkdir(parents=True)
    ledger_path = root / "recordings-v2" / ".publication-ledger" / f"{recording_id}.json"
    payload = {
        "recording_id": recording_id,
        "device_id": "umi-rk3576-161",
        "final_relpath": f"recordings-v2/completed/{recording_id}",
    }
    publish._atomic_json(
        ledger_path,
        {
            "schema_version": 2,
            "state": "PREPARED",
            "device_id": "umi-rk3576-161",
            "recording_id": recording_id,
            "source_relpath": "incoming/native-session",
            "prepared_relpath": f"recordings-v2/completed/.{recording_id}.publishing",
            "final_relpath": f"recordings-v2/completed/{recording_id}",
            "catalog": payload,
        },
    )
    published = []
    monkeypatch.setattr(publish, "_publish_catalog", lambda path, value: published.append((path, value)))

    recovered = publish.recover_pending_publications(
        recording_root=root,
        catalog_db_path=tmp_path / "catalog.sqlite3",
        device_id="umi-rk3576-161",
    )

    assert recovered == [recording_id]
    assert published == [(tmp_path / "catalog.sqlite3", payload)]
    assert (root / "recordings-v2" / "completed" / recording_id).is_dir()
    assert publish._json(ledger_path)["state"] == "PUBLISHED"


def test_catalog_failure_after_final_rename_remains_durably_retryable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    umi_modules,
) -> None:
    _, publish = umi_modules
    root = tmp_path / "recordings"
    session = root / "incoming" / "rk3576-rsusb-20260915T010203Z-feedface"
    session.mkdir(parents=True)
    (session / "rgb.h265").write_bytes(b"\x00\x00\x00\x01")
    stm32_metrics = {name: 0 for name in publish.STM32_ZERO_METRICS}
    stm32_metrics["observed_rate_hz"] = 400.0
    (session / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "rk3576_umi_rsusb_v4",
                "status": "SEALED",
                "session_id": session.name,
                "counts": {"ir_frames": 30, "rgb_input_frames": 30, "stm32_packets": 400},
                "metrics": {"formal_host_span_s": 1.0, "stm32": stm32_metrics},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        publish,
        "_publish_catalog",
        lambda path, payload: (_ for _ in ()).throw(OSError("simulated catalog outage")),
    )

    with pytest.raises(OSError, match="simulated catalog outage"):
        publish.publish_session(
            session=session,
            recording_root=root,
            catalog_db_path=tmp_path / "catalog.sqlite3",
            device_id="umi-rk3576-161",
            job_id="umi-job",
            request_id="request-id",
            boot_id="boot-id",
        )

    recording_id = f"recording_{session.name}"
    final = root / "recordings-v2" / "completed" / recording_id
    ledger_path = root / "recordings-v2" / ".publication-ledger" / f"{recording_id}.json"
    assert final.is_dir()
    assert publish._json(ledger_path)["state"] == "PREPARED"

    calls = []
    monkeypatch.setattr(publish, "_publish_catalog", lambda path, payload: calls.append(payload))
    assert publish.recover_pending_publications(
        recording_root=root,
        catalog_db_path=tmp_path / "catalog.sqlite3",
        device_id="umi-rk3576-161",
    ) == [recording_id]
    assert calls[0]["recording_id"] == recording_id
    assert publish._json(ledger_path)["state"] == "PUBLISHED"


def test_release_provenance_describes_source_until_new_arm_artifact_is_built() -> None:
    provenance = json.loads(
        (COLLECTOR / "RELEASE_PROVENANCE.json").read_text(encoding="utf-8")
    )

    assert provenance["artifact"] is None
    assert provenance["collector"]["native_binary_sha256"] is None
    assert provenance["collector"]["adapter_version"] == "0.2.1-umi"
    assert provenance["status"] == "SOURCE_VALIDATED"


def test_repository_does_not_track_generated_runtime_or_private_keys() -> None:
    tracked_source = {path.relative_to(COLLECTOR).as_posix() for path in COLLECTOR.rglob("*") if path.is_file()}

    assert not any(path.startswith("runtime/") for path in tracked_source)
    assert not any(path.startswith("vendor/") for path in tracked_source)
    assert not any(path.startswith("native/bin/") for path in tracked_source)
    assert not any(path.endswith((".pem", ".key")) for path in tracked_source)
