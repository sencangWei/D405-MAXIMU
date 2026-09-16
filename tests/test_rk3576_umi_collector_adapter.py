from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
import types

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


@pytest.fixture()
def umi_preview_module(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.syspath_prepend(str(ADAPTER))
    monkeypatch.setenv("UMI_D405_SDK_SERIAL", "sdk-serial-161")
    monkeypatch.setenv("UMI_D405_USB_SERIAL", "usb-serial-161")
    monkeypatch.setenv("UMI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("UMI_RECORDING_ROOT", str(tmp_path / "recordings"))
    sys.modules.pop("umi_preview_web", None)
    return importlib.import_module("umi_preview_web")


def test_preview_never_starts_owned_camera_while_capture_is_active(
    monkeypatch: pytest.MonkeyPatch,
    umi_preview_module,
) -> None:
    state = umi_preview_module.State()
    state.session_id = "preview-session"
    monkeypatch.setattr(state, "capture_active", lambda: True)
    monkeypatch.setattr(state, "source_listens", lambda: False)
    started = []
    monkeypatch.setattr(state, "ensure_source", lambda: started.append(True))

    state.request_owned_source_start()

    assert started == []
    assert state.source_starting is False


def test_recording_handoff_blocks_idle_preview_even_without_session(
    monkeypatch: pytest.MonkeyPatch,
    umi_preview_module,
) -> None:
    state = umi_preview_module.State()
    stopped = []
    monkeypatch.setattr(state, "stop_owned_source", lambda: stopped.append(True))

    result = state.prepare_recording_handoff("umi-rk3576-161")

    assert result == {"released": True, "session_id": None}
    assert state.handed_off is True
    assert stopped == [True]


def test_preview_resumes_after_recording_handoff_finishes(
    monkeypatch: pytest.MonkeyPatch,
    umi_preview_module,
) -> None:
    state = umi_preview_module.State()
    state.session_id = "preview-session"
    state.expires_at = umi_preview_module.time.time() + 30
    state.handed_off = True
    state.external_source_expected_until = 0.0
    monkeypatch.setattr(state, "capture_active", lambda: False)
    monkeypatch.setattr(state, "source_listens", lambda: False)
    started = []
    monkeypatch.setattr(state, "ensure_source", lambda: started.append(True))

    class InlineThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self) -> None:
            self.target()

    monkeypatch.setattr(umi_preview_module.threading, "Thread", InlineThread)

    state.request_owned_source_start()

    assert state.handed_off is False
    assert started == [True]
    assert state.source_starting is False


def test_preview_handoff_accepts_session_expiring_between_health_and_post(
    monkeypatch: pytest.MonkeyPatch,
    umi_modules,
) -> None:
    recorderctl, _ = umi_modules
    monkeypatch.setattr(
        recorderctl,
        "preview_health",
        lambda: {"session_id": "expired-preview-session"},
    )

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read(self) -> bytes:
            return b'{"released":true,"session_id":null}'

    monkeypatch.setattr(recorderctl, "urlopen", lambda *_args, **_kwargs: Response())

    recorderctl.prepare_preview_handoff(
        types.SimpleNamespace(device_id="umi-rk3576-161")
    )


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
    assert identity["controller_version"] == "0.2.5-umi"
    assert identity["capabilities"] == [
        "capture_timing_v1",
        "preview_v1",
        "preview_shared_media_v1",
        "catalog_v1",
        "resumable_transfer_v1",
        "recording_delete_v1",
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


def test_missing_worker_stops_native_orphan_before_releasing_state(
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
    state = {
        "job_id": "umi-orphan",
        "boot_id": config.boot_id,
        "state": "recording",
        "worker_pid": 100,
        "worker_start_ticks": 1,
        "record_pid": 101,
        "record_start_ticks": 2,
    }
    monkeypatch.setattr(recorderctl, "worker_matches", lambda cfg, value: False)
    stopped = []
    monkeypatch.setattr(recorderctl, "_stop_orphan_recorder", lambda value: stopped.append(value) or True)

    recovered = recorderctl.reconcile_stale_state(config, state)

    assert stopped == [state]
    assert recovered["state"] == "interrupted"
    assert recovered["recovery_reason"] == "STALE_PROCESS_IDENTITY"


def test_publication_recovery_updates_failed_job_and_current(
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
    state = {"job_id": "umi-job", "state": "incomplete", "local_data_present": False}
    recorderctl.atomic_json(config.jobs / "umi-job.json", state)
    recorderctl.atomic_json(config.current, state)
    publication = {
        "job_id": "umi-job",
        "recording_id": "recording_test",
        "output_dir": str(tmp_path / "recordings" / "recordings-v2" / "completed" / "recording_test"),
        "manifest_sha256": "a" * 64,
        "stereo_pairs": 30,
        "imu_samples": 400,
    }

    recorderctl.reconcile_published_job(config, publication)

    recovered = recorderctl.read_json(config.current)
    assert recovered["state"] == "complete_local"
    assert recovered["local_data_present"] is True
    assert recovered["imu_quality_status"] == "PASSED"
    assert recovered["recovery_reason"] == "PUBLICATION_LEDGER_REPLAYED"


def test_idempotent_start_reconciles_stale_request_state(
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
    request_id = "2f1713ab-e535-4ded-9c58-bbc7f9c39c84"
    state = {
        "job_id": "umi-stale",
        "request_id": request_id,
        "boot_id": "earlier-boot",
        "state": "recording",
    }
    recorderctl.atomic_json(config.jobs / "umi-stale.json", state)
    recorderctl.atomic_json(config.requests / f"{request_id}.json", state)
    recorderctl.atomic_json(config.current, state)

    response = recorderctl.start(config, request_id, 60)

    assert response == {
        "accepted": True,
        "idempotent": True,
        "job_id": "umi-stale",
        "state": "interrupted",
    }


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
    manifest_sha256 = "a" * 64
    result = {
        "job_id": "umi-job",
        "recording_id": recording_id,
        "output_dir": str(root / "recordings-v2" / "completed" / recording_id),
        "manifest_sha256": manifest_sha256,
        "stereo_pairs": 30,
        "imu_samples": 400,
    }
    publish._atomic_json(
        ledger_path,
        {
            "schema_version": 2,
            "state": "PREPARED",
            "device_id": "umi-rk3576-161",
            "recording_id": recording_id,
            "job_id": "umi-job",
            "source_relpath": "incoming/native-session",
            "prepared_relpath": f"recordings-v2/completed/.{recording_id}.publishing",
            "final_relpath": f"recordings-v2/completed/{recording_id}",
            "catalog": payload,
            "result": result,
            "manifest_sha256": manifest_sha256,
        },
    )
    published = []
    monkeypatch.setattr(publish, "_publish_catalog", lambda path, value: published.append((path, value)))

    recovered = publish.recover_pending_publications(
        recording_root=root,
        catalog_db_path=tmp_path / "catalog.sqlite3",
        device_id="umi-rk3576-161",
    )

    assert recovered == [result]
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
    recovered = publish.recover_pending_publications(
        recording_root=root,
        catalog_db_path=tmp_path / "catalog.sqlite3",
        device_id="umi-rk3576-161",
    )
    assert recovered[0]["recording_id"] == recording_id
    assert calls[0]["recording_id"] == recording_id
    assert publish._json(ledger_path)["state"] == "PUBLISHED"


def test_catalog_recovery_restores_json_assets_to_required_tuple(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    umi_modules,
) -> None:
    _, publish = umi_modules
    observed = []

    class StrictCatalog:
        def __init__(self, path: Path) -> None:
            observed.append(path)

        def migrate(self) -> None:
            return None

        def publish_recording(self, **payload) -> None:
            assert isinstance(payload["assets"], tuple)
            observed.append(payload)

    monkeypatch.setitem(sys.modules, "catalog_db", types.SimpleNamespace(CatalogDb=StrictCatalog))
    publish._publish_catalog(
        tmp_path / "catalog.sqlite3",
        {"recording_id": "recording_test", "assets": [{"relative_path": "rgb.h265"}]},
    )

    assert observed[1]["assets"] == ({"relative_path": "rgb.h265"},)


def test_legacy_published_ledger_does_not_block_upgrade(tmp_path: Path, umi_modules) -> None:
    _, publish = umi_modules
    root = tmp_path / "recordings"
    ledger = root / "recordings-v2" / ".publication-ledger" / "recording_legacy.json"
    publish._atomic_json(ledger, {"schema_version": 1, "state": "PUBLISHED"})

    assert publish.recover_pending_publications(
        recording_root=root,
        catalog_db_path=tmp_path / "catalog.sqlite3",
        device_id="umi-rk3576-161",
    ) == []


def test_recovery_retires_published_ledger_when_catalog_tombstoned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, umi_modules
) -> None:
    """recording_delete_v1 removes the payload on purpose; recovery must retire
    the stale PUBLISHED ledger instead of blocking every later capture start."""
    _, publish = umi_modules
    root = tmp_path / "recordings"
    recording_id = "recording_rk3576-rsusb-20260916T095534Z-770a2d09"
    ledger_path = root / "recordings-v2" / ".publication-ledger" / f"{recording_id}.json"
    publish._atomic_json(
        ledger_path,
        {
            "schema_version": 2,
            "state": "PUBLISHED",
            "device_id": "umi-rk3576-161",
            "recording_id": recording_id,
            "job_id": "umi-job",
            "source_relpath": "incoming/native-session",
            "prepared_relpath": f"recordings-v2/completed/.{recording_id}.publishing",
            "final_relpath": f"recordings-v2/completed/{recording_id}",
        },
    )
    monkeypatch.setattr(publish, "_catalog_tombstoned", lambda path, rid: rid == recording_id)

    assert publish.recover_pending_publications(
        recording_root=root,
        catalog_db_path=tmp_path / "catalog.sqlite3",
        device_id="umi-rk3576-161",
    ) == []
    assert publish._json(ledger_path)["state"] == "DELETED"


def test_recovery_raises_for_missing_payload_without_catalog_tombstone(
    tmp_path: Path, umi_modules
) -> None:
    """A payload that vanished without a confirmed deletion is still an anomaly."""
    _, publish = umi_modules
    root = tmp_path / "recordings"
    recording_id = "recording_rk3576-rsusb-20260916T095534Z-770a2d09"
    ledger_path = root / "recordings-v2" / ".publication-ledger" / f"{recording_id}.json"
    publish._atomic_json(
        ledger_path,
        {
            "schema_version": 2,
            "state": "PUBLISHED",
            "device_id": "umi-rk3576-161",
            "recording_id": recording_id,
            "job_id": "umi-job",
            "source_relpath": "incoming/native-session",
            "prepared_relpath": f"recordings-v2/completed/.{recording_id}.publishing",
            "final_relpath": f"recordings-v2/completed/{recording_id}",
        },
    )

    with pytest.raises(ValueError, match="published recording payload is unavailable"):
        publish.recover_pending_publications(
            recording_root=root,
            catalog_db_path=tmp_path / "catalog.sqlite3",
            device_id="umi-rk3576-161",
        )


def test_recovery_skips_retired_deleted_ledger(tmp_path: Path, umi_modules) -> None:
    _, publish = umi_modules
    root = tmp_path / "recordings"
    ledger = root / "recordings-v2" / ".publication-ledger" / "recording_retired.json"
    publish._atomic_json(ledger, {"schema_version": 2, "state": "DELETED"})

    assert publish.recover_pending_publications(
        recording_root=root,
        catalog_db_path=tmp_path / "catalog.sqlite3",
        device_id="umi-rk3576-161",
    ) == []


def test_catalog_tombstone_helper_queries_save_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, umi_modules
) -> None:
    _, publish = umi_modules
    rows = {"recording-a": {"save_state": "SOURCE_DELETED"}}

    class Catalog:
        def __init__(self, path):
            self.path = path

        def get_recording(self, recording_id):
            return rows.get(recording_id)

    monkeypatch.setitem(
        sys.modules, "catalog_db", types.SimpleNamespace(CatalogDb=Catalog)
    )
    assert publish._catalog_tombstoned(tmp_path / "catalog.sqlite3", "recording-a") is True
    assert publish._catalog_tombstoned(tmp_path / "catalog.sqlite3", "recording-b") is False
    assert publish._catalog_tombstoned(tmp_path / "catalog.sqlite3", "../escape") is False


def test_release_provenance_binds_the_arm_artifact() -> None:
    provenance = json.loads(
        (COLLECTOR / "RELEASE_PROVENANCE.json").read_text(encoding="utf-8")
    )

    assert provenance["artifact"]["sha256"] == "d15445127fd8927da9ceba206dd74e0a683a326312a974c7626282e9e44b8850"
    assert provenance["artifact"]["source_commit"] == "d17b2c2b6dca9ac967d7b9a0d02573e18b01ce0d"
    assert provenance["artifact"]["base_release"] == "0.2.3-fix1"
    assert provenance["collector"]["adapter_version"] == "0.2.5-umi"
    assert provenance["status"] == "DEPLOYED_AND_ACCEPTED_ON_IP161_20260916"


def test_repository_does_not_track_generated_runtime_or_private_keys() -> None:
    tracked_source = {path.relative_to(COLLECTOR).as_posix() for path in COLLECTOR.rglob("*") if path.is_file()}

    assert not any(path.startswith("runtime/") for path in tracked_source)
    assert not any(path.startswith("vendor/") for path in tracked_source)
    assert not any(path.startswith("native/bin/") for path in tracked_source)
    assert not any(path.endswith((".pem", ".key")) for path in tracked_source)


def test_admin_service_bootstraps_empty_completed_recording_root() -> None:
    launcher = (COLLECTOR / "bin" / "umi-admin-service").read_text(encoding="utf-8")

    assert '"$UMI_RECORDING_ROOT/recordings-v2/completed"' in launcher
    assert 'chmod 700 --' in launcher


def test_host_check_does_not_mutate_hashed_release_with_bytecode() -> None:
    host_check = (COLLECTOR / "check-host.sh").read_text(encoding="utf-8")

    assert "export PYTHONDONTWRITEBYTECODE=1" in host_check


def test_all_python_launchers_keep_immutable_release_free_of_bytecode() -> None:
    for relative in ("bin/recorderctl", "bin/umi-admin-service", "bin/umi-preview-service"):
        launcher = (COLLECTOR / relative).read_text(encoding="utf-8")
        assert "export PYTHONDONTWRITEBYTECODE=1" in launcher


def test_d405_udev_rule_disables_runtime_autosuspend() -> None:
    rules = (COLLECTOR / "99-umi-devices.rules").read_text(encoding="utf-8")

    d405_rule = next(line for line in rules.splitlines() if 'ATTR{idVendor}=="8086"' in line)
    assert 'ATTR{power/control}="on"' in d405_rule


def test_preflight_reports_contract_complete_unsampled_stm32_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    umi_modules,
) -> None:
    recorderctl, _ = umi_modules
    stm32 = tmp_path / "serial-by-id"
    stm32.touch()
    monkeypatch.setenv("UMI_DEVICE_ID", "umi-rk3576-161")
    monkeypatch.setenv("UMI_D405_SDK_SERIAL", "sdk-serial-161")
    monkeypatch.setenv("UMI_D405_USB_SERIAL", "usb-serial-161")
    monkeypatch.setenv("UMI_STM32_PORT", str(stm32))
    monkeypatch.setenv("UMI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("UMI_RECORDING_ROOT", str(tmp_path / "recordings"))
    monkeypatch.setattr(recorderctl, "recover_pending_publications", lambda **_kwargs: [])
    cfg = recorderctl.Config()
    cfg.prepare()

    imu = recorderctl._preflight_locked(cfg)["imu"]

    assert imu == {
        "state": "unknown",
        "sample_count": 0,
        "error_code": "IMU_LIVE_PROBE_UNAVAILABLE",
        "detail": "Sensor packets are checked during capture warmup",
    }
