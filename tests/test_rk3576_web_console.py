from __future__ import annotations

import importlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "rk3576" / "collector" / "web-console"
ADAPTER = ROOT / "rk3576" / "collector" / "adapter"


@pytest.fixture()
def web_server(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(WEB))
    sys.modules.pop("server", None)
    return importlib.import_module("server")


def test_running_clock_is_freshened_from_snapshot_monotonic_time(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    web_server,
) -> None:
    app = web_server.Application(object(), tmp_path / "state", tmp_path / "downloads")
    app.snapshot = {
        "connected": True,
        "updated_at": 1_000.0,
        "status": {
            "job_id": "umi-job",
            "capture_running": True,
            "capture_elapsed_s": 10.0,
            "elapsed_s": 10.0,
        },
    }
    app.snapshot_monotonic = 100.0
    monkeypatch.setattr(web_server.time, "monotonic", lambda: 102.25)

    first = app.view()
    monkeypatch.setattr(web_server.time, "monotonic", lambda: 103.75)
    second = app.view()

    assert first["status"]["capture_elapsed_s"] == pytest.approx(12.25)
    assert second["status"]["capture_elapsed_s"] == pytest.approx(13.75)
    assert second["status"]["elapsed_s"] == pytest.approx(13.75)
    assert app.snapshot["status"]["capture_elapsed_s"] == 10.0


def test_finished_clock_is_not_extrapolated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    web_server,
) -> None:
    app = web_server.Application(object(), tmp_path / "state", tmp_path / "downloads")
    app.snapshot = {
        "connected": True,
        "updated_at": 1_000.0,
        "status": {
            "job_id": "umi-job",
            "capture_running": False,
            "capture_elapsed_s": 10.0,
            "elapsed_s": 10.0,
        },
    }
    app.snapshot_monotonic = 100.0
    monkeypatch.setattr(web_server.time, "monotonic", lambda: 150.0)

    assert app.view()["status"]["capture_elapsed_s"] == 10.0


def test_browser_clock_never_regresses_for_repeated_render_of_same_job() -> None:
    clock = WEB / "static" / "clock.js"
    script = f"""
const clock = require({json.dumps(str(clock))});
let base = clock.reconcile(null, {{job_id:'job-1', capture_running:true,
  capture_elapsed_s:10}}, true, 1000);
base = clock.reconcile(base, {{job_id:'job-1', capture_running:true,
  capture_elapsed_s:10}}, true, 3250);
const afterDuplicate = clock.value(base, 3250);
base = clock.reconcile(base, {{job_id:'job-1', capture_running:true,
  capture_elapsed_s:11}}, true, 3500);
const afterStaleSample = clock.value(base, 3500);
const stopped = clock.reconcile(base, {{job_id:'job-1', capture_running:false,
  capture_elapsed_s:12}}, true, 4000);
process.stdout.write(JSON.stringify({{afterDuplicate, afterStaleSample,
  stopped: clock.value(stopped, 9000)}}));
"""
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )

    assert json.loads(result.stdout) == {
        "afterDuplicate": 12.25,
        "afterStaleSample": 12.5,
        "stopped": 12,
    }


def test_dynamic_local_ip_accepts_matching_origin_and_rejects_mismatch(web_server) -> None:
    handler = object.__new__(web_server.Handler)
    handler.connection = types.SimpleNamespace(
        getsockname=lambda: ("192.168.50.37", 8766)
    )
    handler.server = types.SimpleNamespace(
        server_port=8766,
        allowed_hosts={"umi-device:8766", "127.0.0.1:8766"},
    )
    handler.headers = {
        "Host": "192.168.50.37:8766",
        "Origin": "http://192.168.50.37:8766",
    }

    assert handler.allowed_host() is True
    assert handler.allowed_origin() is True

    handler.headers["Origin"] = "http://192.168.50.99:8766"
    assert handler.allowed_origin() is False


def test_delete_button_contract_starts_exact_recording_operation(
    tmp_path: Path,
    web_server,
) -> None:
    calls = []

    class Bridge:
        def rpc(self, action, **args):
            calls.append((action, args))
            return {"accepted": True, "recording_id": args["recording_id"]}

    app = web_server.Application(Bridge(), tmp_path / "state", tmp_path / "downloads")
    request_id = "7f4eddb2-633f-4423-ad82-06cc48299087"

    result = app.delete_recording("recording_rk3576-test", request_id)

    assert result == {"accepted": True, "recording_id": "recording_rk3576-test"}
    assert calls == [
        (
            "delete_recording",
            {"recording_id": "recording_rk3576-test", "request_id": request_id},
        )
    ]


def test_delete_is_blocked_during_transfer(tmp_path: Path, web_server) -> None:
    app = web_server.Application(object(), tmp_path / "state", tmp_path / "downloads")
    app.transfers["recording_rk3576-test"] = {
        "recording_id": "recording_rk3576-test",
        "state": "copying",
    }

    with pytest.raises(web_server.AppError, match="转存"):
        app.delete_recording(
            "recording_rk3576-test",
            "7f4eddb2-633f-4423-ad82-06cc48299087",
        )


@pytest.fixture()
def recorderctl(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(ADAPTER))
    for name in ("umi_recorderctl", "umi_publish"):
        sys.modules.pop(name, None)
    return importlib.import_module("umi_recorderctl")


def _configure_recorder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, recorderctl
):
    monkeypatch.setenv("UMI_DEVICE_ID", "umi-rk3576-161")
    monkeypatch.setenv("UMI_D405_SDK_SERIAL", "sdk-serial-161")
    monkeypatch.setenv("UMI_D405_USB_SERIAL", "usb-serial-161")
    monkeypatch.setenv("UMI_STM32_PORT", str(tmp_path / "stm32"))
    monkeypatch.setenv("UMI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("UMI_RECORDING_ROOT", str(tmp_path / "recordings"))
    return recorderctl.Config()


def _published_recording(config, recording_id: str) -> tuple[Path, str]:
    root = config.recording_root / "recordings-v2" / "completed" / recording_id
    root.mkdir(parents=True)
    payload = b"recorded-data"
    digest = __import__("hashlib").sha256(payload).hexdigest()
    (root / "rgb.h265").write_bytes(payload)
    manifest = f"{digest}  rgb.h265\n".encode()
    (root / "MANIFEST.sha256").write_bytes(manifest)
    manifest_sha256 = __import__("hashlib").sha256(manifest).hexdigest()
    config.catalog_db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(config.catalog_db) as db:
        db.execute(
            "CREATE TABLE recordings (recording_id TEXT PRIMARY KEY, device_id TEXT, "
            "state TEXT, manifest_sha256 TEXT, save_state TEXT)"
        )
        db.execute(
            "CREATE TABLE catalog_recording_assets (recording_id TEXT, position INTEGER, "
            "relative_path TEXT, size_bytes INTEGER, sha256 TEXT)"
        )
        db.execute(
            "INSERT INTO recordings VALUES (?, ?, 'COMPLETE_LOCAL', ?, 'LOCAL_ONLY')",
            (recording_id, config.device_id, manifest_sha256),
        )
        db.execute(
            "INSERT INTO catalog_recording_assets VALUES (?, 0, 'rgb.h265', ?, ?)",
            (recording_id, len(payload), digest),
        )
    return root, manifest_sha256


def test_secure_recording_delete_tombstones_catalog_and_current_job(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    recorderctl,
) -> None:
    config = _configure_recorder(monkeypatch, tmp_path, recorderctl)
    config.prepare()
    recording_id = "recording_rk3576-test"
    root, manifest_sha256 = _published_recording(config, recording_id)
    state = {
        "job_id": "umi-job",
        "state": "complete_local",
        "recording_id": recording_id,
        "output_dir": str(root),
        "local_data_present": True,
    }
    recorderctl.atomic_json(config.jobs / "umi-job.json", state)
    recorderctl.atomic_json(config.current, state)
    deleted = []

    def secure_delete(path, identity, assets):
        assert Path(path) == root
        assert {"dev", "ino", "mode"} <= set(identity)
        assert [item["path"] for item in assets] == ["rgb.h265", "MANIFEST.sha256"]
        deleted.append(path)
        __import__("shutil").rmtree(path)

    class Catalog:
        def __init__(self, path):
            assert Path(path) == config.catalog_db

        def mark_recording_deleted(self, **identity):
            assert identity == {
                "recording_id": recording_id,
                "device_id": config.device_id,
                "manifest_sha256": manifest_sha256,
            }
            return {**identity, "save_state": "SOURCE_DELETED"}

    monkeypatch.setitem(
        sys.modules,
        "transfer_commit",
        types.SimpleNamespace(
            delete_catalog_tree=secure_delete,
            TransferCommitError=RuntimeError,
        ),
    )
    monkeypatch.setitem(sys.modules, "catalog_db", types.SimpleNamespace(CatalogDb=Catalog))
    operation = {
        "recording_id": recording_id,
        "request_id": "0e9d63a4-4fe2-41ac-b735-73a11b5ca2e1",
    }

    result = recorderctl.delete_recording_sync(config, operation)

    assert result["state"] == "complete"
    assert result["recording_id"] == recording_id
    assert deleted == [root]
    assert not root.exists()
    assert recorderctl.read_json(config.current)["local_data_present"] is False
    assert recorderctl.read_json(config.jobs / "umi-job.json")["local_data_present"] is False


def test_secure_recording_delete_retires_publication_ledger(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    recorderctl,
) -> None:
    """The 0.2.5 delete flow removed payloads but left PUBLISHED ledgers behind,
    which bricked later capture starts; deletion must retire the ledger."""
    config = _configure_recorder(monkeypatch, tmp_path, recorderctl)
    config.prepare()
    recording_id = "recording_rk3576-test"
    root, manifest_sha256 = _published_recording(config, recording_id)
    ledger_path = (
        config.recording_root
        / "recordings-v2"
        / ".publication-ledger"
        / f"{recording_id}.json"
    )
    recorderctl.atomic_json(
        ledger_path,
        {
            "schema_version": 2,
            "state": "PUBLISHED",
            "device_id": config.device_id,
            "recording_id": recording_id,
            "job_id": "umi-job",
            "final_relpath": f"recordings-v2/completed/{recording_id}",
        },
    )

    def secure_delete(path, identity, assets):
        __import__("shutil").rmtree(path)

    class Catalog:
        def __init__(self, path):
            pass

        def mark_recording_deleted(self, **identity):
            return {**identity, "save_state": "SOURCE_DELETED"}

    monkeypatch.setitem(
        sys.modules,
        "transfer_commit",
        types.SimpleNamespace(
            delete_catalog_tree=secure_delete,
            TransferCommitError=RuntimeError,
        ),
    )
    monkeypatch.setitem(sys.modules, "catalog_db", types.SimpleNamespace(CatalogDb=Catalog))

    result = recorderctl.delete_recording_sync(
        config,
        {
            "recording_id": recording_id,
            "request_id": "0e9d63a4-4fe2-41ac-b735-73a11b5ca2e1",
        },
    )

    assert result["state"] == "complete"
    ledger = recorderctl.read_json(ledger_path)
    assert ledger["state"] == "DELETED"
    assert ledger["recording_id"] == recording_id


def test_recording_delete_rejects_unpublished_or_unsafe_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    recorderctl,
) -> None:
    config = _configure_recorder(monkeypatch, tmp_path, recorderctl)
    config.prepare()

    with pytest.raises(recorderctl.ControllerError, match="recording_id"):
        recorderctl.delete_recording_sync(
            config,
            {
                "recording_id": "../outside",
                "request_id": "0e9d63a4-4fe2-41ac-b735-73a11b5ca2e1",
            },
        )


def test_delete_request_is_rejected_while_capture_is_active(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    recorderctl,
) -> None:
    config = _configure_recorder(monkeypatch, tmp_path, recorderctl)
    config.prepare()
    active = {"job_id": "umi-live", "state": "recording", "boot_id": config.boot_id}
    recorderctl.atomic_json(config.current, active)
    monkeypatch.setattr(recorderctl, "worker_matches", lambda cfg, state: True)

    with pytest.raises(recorderctl.ControllerError) as error:
        recorderctl.start_delete_recording(
            config,
            "recording_rk3576-test",
            "0e9d63a4-4fe2-41ac-b735-73a11b5ca2e1",
        )

    assert error.value.code == "ACTIVE_JOB"


def test_delete_status_recovers_a_missing_worker_as_retryable_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    recorderctl,
) -> None:
    config = _configure_recorder(monkeypatch, tmp_path, recorderctl)
    config.prepare()
    recording_id = "recording_rk3576-test"
    recorderctl.atomic_json(
        config.deletions / f"{recording_id}.json",
        {
            "recording_id": recording_id,
            "request_id": "0e9d63a4-4fe2-41ac-b735-73a11b5ca2e1",
            "state": "deleting",
            "phase": "delete_tree",
            "boot_id": config.boot_id,
            "worker_pid": 999999,
            "worker_start_ticks": 1,
        },
    )
    monkeypatch.setattr(recorderctl, "worker_matches", lambda cfg, value: False)

    result = recorderctl.delete_status(config, recording_id)

    assert result["state"] == "failed"
    assert result["phase"] == "worker_missing"
    assert "worker disappeared" in result["error"]
