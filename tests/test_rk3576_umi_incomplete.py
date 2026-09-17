"""Tests for incomplete-recording listing, rescue and deletion.

A field failure motivates these: a power cut leaves an unsealed staging
directory in incoming/, which no catalog row describes, so the Web console
cannot show or delete it. The tests build realistic orphans (real Rockchip
parameter sets, monotonic JSONL indexes, a truncated stm32 payload) and pin the
safety rules: never touch the directory of a live capture, never delete the
source before its MP4 verifies, and never publish a rescued session as if it
had passed the normal capture gate.
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "rk3576" / "collector" / "adapter"
FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "umi_hevc_parameter_sets.json").read_text()
)
VPS = bytes.fromhex(FIXTURE["rgb"]["vps"])
SPS = bytes.fromhex(FIXTURE["rgb"]["sps"])
PPS = bytes.fromhex(FIXTURE["rgb"]["pps"])
SESSION = "rk3576-rsusb-cpp-20260916T111301Z-49731d99"
GB = 1024**3


@pytest.fixture()
def umi_modules(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(ADAPTER))
    for name in ("umi_recorderctl", "umi_publish", "umi_remux"):
        sys.modules.pop(name, None)
    publish = importlib.import_module("umi_publish")
    recorderctl = importlib.import_module("umi_recorderctl")
    return recorderctl, publish


def _configure_recorder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, recorderctl):
    monkeypatch.setenv("UMI_DEVICE_ID", "umi-rk3576-161")
    monkeypatch.setenv("UMI_D405_SDK_SERIAL", "sdk-serial-161")
    monkeypatch.setenv("UMI_D405_USB_SERIAL", "usb-serial-161")
    stm32 = tmp_path / "ttyUSB0"
    stm32.write_text("")
    monkeypatch.setenv("UMI_STM32_PORT", str(stm32))
    monkeypatch.setenv("UMI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("UMI_RECORDING_ROOT", str(tmp_path / "recordings"))
    return recorderctl.Config()


# ---------------------------------------------------------------------------
# orphan builders


def _with_start_code(nal: bytes) -> bytes:
    """Prefix a complete NAL (header already included) with a 4-byte start code."""
    return b"\x00\x00\x00\x01" + nal


def _slice(nal_type: int, seed: int, size: int = 24) -> bytes:
    header = bytes([(nal_type << 1) & 0x7E, 0x01])
    body = bytes([0x80]) + bytes(((seed + i) * 7 + 3) & 0xFF for i in range(size))
    return _with_start_code(header + body)


def _hevc_stream(aus: int, *, padding: int = 128, idr_every: int = 5) -> bytes:
    out = bytearray()
    for index in range(aus):
        if index % idr_every == 0:
            out += _with_start_code(VPS)
            out += _with_start_code(SPS)
            out += _with_start_code(PPS)
        out += _slice(19 if index % idr_every == 0 else 1, index)
    out += b"\x00" * padding
    return bytes(out)


def _write_index(path: Path, rows: int, *, extra: dict | None = None, first_ns: int = 1_000_000_000):
    with path.open("w", encoding="utf-8") as stream:
        for index in range(rows):
            row = {"record_index": index, "host_read_complete_monotonic_ns": first_ns + index * 2_500_000}
            row.update(extra or {})
            stream.write(json.dumps(row) + "\n")


def _orphan(config, *, pairs: int = 20, kind: str = "unsealed", session: str = SESSION,
            assets: tuple = ("rgb", "infrared_left", "infrared_right"), stm32_packets: int | None = None,
            session_id: str | None = None, with_indexes: bool = True,
            rgb_rows: int | None = None) -> Path:
    directory = config.incoming / f".{session}.partial"
    directory.mkdir(parents=True, exist_ok=True)
    if kind == "unsealed":
        (directory / ".recording").write_text("unsealed\n")
    (directory / "session_config.json").write_text(
        json.dumps(
            {
                "schema": "three-device-slam.rk3576-umi-rsusb-session.v4",
                "session_id": session_id or session,
                "profile": {"rgb": {"width": 1280, "height": 720, "fps": 30}},
                "device": {"d405_serial": "260322273737"},
            }
        )
    )
    sealed = kind in ("sealed", "sealed_unpublished")
    for asset in assets:
        stem = {
            "rgb": "rgb.h265",
            "infrared_left": "infrared-left-y8.h265",
            "infrared_right": "infrared-right-y8.h265",
        }[asset]
        name = stem if sealed else f"{stem}.partial"
        (directory / name).write_bytes(_hevc_stream(pairs + 1))
    packets = stm32_packets if stm32_packets is not None else pairs * 400
    payload = directory / ("stm32.bin" if sealed else "stm32.bin.partial")
    payload.write_bytes(b"\x07" * (packets * 63 + 52))  # power cut leaves a torn tail
    if with_indexes:
        _write_index(directory / "ir_frames.jsonl", pairs)
        _write_index(directory / "rgb_frames.jsonl", pairs if rgb_rows is None else rgb_rows)
        _write_index(directory / "stm32_packets.jsonl", packets, extra={"offset": 0, "size": 63})
    for name in ("rgb-gstreamer.log", "ir-left-gstreamer.log", "ir-right-gstreamer.log"):
        (directory / name).write_text("mpp encoder log\n")
    if kind in ("sealed", "sealed_unpublished"):
        (directory / "manifest.json").write_text(
            json.dumps(
                {
                    "schema": "three-device-slam.rk3576-umi-rsusb-session.v4",
                    "status": "SEALED",
                    "session_id": session_id or session,
                    "counts": {"ir_frames": pairs, "rgb_input_frames": pairs, "stm32_packets": packets},
                    "metrics": {"formal_host_span_s": pairs / 30.0, "stm32": {"observed_rate_hz": 400.0}},
                }
            )
        )
    return directory


@pytest.fixture()
def recorder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, umi_modules):
    recorderctl, publish = umi_modules
    config = _configure_recorder(monkeypatch, tmp_path, recorderctl)
    config.prepare()
    return recorderctl, publish, config


# ---------------------------------------------------------------------------
# listing


def test_incomplete_list_reports_an_unsealed_orphan_with_sizes(recorder):
    recorderctl, _, config = recorder
    _orphan(config, pairs=20)
    data = recorderctl.open_incomplete_list(config)
    assert data["capturing"] is False
    assert len(data["sessions"]) == 1
    entry = data["sessions"][0]
    assert entry["session"] == SESSION
    assert entry["kind"] == "unsealed"
    assert entry["pairs"] == 20
    assert entry["asset_bytes"]["rgb"] > 0
    assert entry["imu_bytes"] > 0
    assert entry["assets_present"] == {"rgb": True, "infrared_left": True, "infrared_right": True}
    assert entry["usable"]["rgb"] is True
    assert entry["space"]["required_rgb"] > 0
    assert entry["space"]["required_ir"] > 0
    assert entry["already_published"] is False
    assert entry["live"] is False


def test_incomplete_list_reports_a_sealed_but_unpublished_session(recorder):
    recorderctl, _, config = recorder
    _orphan(config, pairs=10, kind="sealed")
    data = recorderctl.open_incomplete_list(config)
    assert [entry["kind"] for entry in data["sessions"]] == ["sealed_unpublished"]


def test_incomplete_list_reads_pair_counts_from_the_index_tail(recorder):
    recorderctl, _, config = recorder
    directory = _orphan(config, pairs=12)
    # a torn final line (power cut mid-write) must not be counted
    with (directory / "ir_frames.jsonl").open("a", encoding="utf-8") as stream:
        stream.write('{"record_index":12,"host_read_complete')
    data = recorderctl.open_incomplete_list(config)
    assert data["sessions"][0]["pairs"] == 12


def test_incomplete_list_is_stat_only_and_never_hashes(recorder, monkeypatch):
    recorderctl, _, config = recorder
    _orphan(config, pairs=8)
    monkeypatch.setattr(
        recorderctl.hashlib, "sha256", lambda *args, **kwargs: pytest.fail("listing must not hash")
    )
    data = recorderctl.open_incomplete_list(config)
    assert data["sessions"][0]["pairs"] == 8


def test_incomplete_list_ignores_a_symlinked_orphan(recorder, tmp_path):
    recorderctl, _, config = recorder
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "rgb.h265.partial").write_bytes(_hevc_stream(3))
    (config.incoming / f".{SESSION}.partial").symlink_to(target)
    data = recorderctl.open_incomplete_list(config)
    assert data["sessions"] == []


def test_incomplete_list_ignores_foreign_directory_names(recorder):
    recorderctl, _, config = recorder
    (config.incoming / ".not-a-session.partial").mkdir()
    (config.incoming / ".not-a-session.partial" / ".recording").write_text("unsealed\n")
    assert recorderctl.open_incomplete_list(config)["sessions"] == []


# ---------------------------------------------------------------------------
# guards


def test_incomplete_names_are_validated(recorder):
    recorderctl, _, config = recorder
    _orphan(config)
    with pytest.raises(recorderctl.ControllerError) as error:
        recorderctl.start_incomplete_recover(
            config, "../../etc/passwd", "44b1d5f0-1a11-4d5e-9f2a-0f2b7f9a11aa"
        )
    assert error.value.code == "INCOMPLETE_SESSION_INVALID"


def test_incomplete_recover_is_refused_while_a_capture_is_active(recorder):
    recorderctl, _, config = recorder
    _orphan(config)
    recorderctl.atomic_json(
        config.current,
        {"job_id": "umi-active", "state": "recording", "boot_id": config.boot_id,
         "record_pid": os.getpid(), "record_start_ticks": recorderctl.proc_ticks(os.getpid()),
         "worker_pid": os.getpid(), "worker_start_ticks": recorderctl.proc_ticks(os.getpid())},
    )
    with pytest.raises(recorderctl.ControllerError) as error:
        recorderctl.start_incomplete_recover(
            config, SESSION, "44b1d5f0-1a11-4d5e-9f2a-0f2b7f9a11aa"
        )
    assert error.value.code == "ACTIVE_JOB"


def test_incomplete_recover_is_refused_when_the_directory_is_being_written(recorder, monkeypatch):
    recorderctl, _, config = recorder
    _orphan(config)
    monkeypatch.setattr(recorderctl, "_pid_holds", lambda pid, directories: True)
    with pytest.raises(recorderctl.ControllerError) as error:
        recorderctl.start_incomplete_recover(
            config, SESSION, "44b1d5f0-1a11-4d5e-9f2a-0f2b7f9a11aa"
        )
    assert error.value.code == "INCOMPLETE_LIVE_CAPTURE"


def test_incomplete_recover_is_idempotent_for_the_same_request_id(recorder, monkeypatch):
    recorderctl, _, config = recorder
    _orphan(config)
    request_id = "44b1d5f0-1a11-4d5e-9f2a-0f2b7f9a11aa"
    recorderctl.atomic_json(
        config.recoveries / f"{SESSION}.json",
        {"session": SESSION, "request_id": request_id, "state": "complete", "boot_id": config.boot_id},
    )
    data = recorderctl.start_incomplete_recover(config, SESSION, request_id)
    assert data["idempotent"] is True


def test_incomplete_recover_refuses_an_already_published_session(recorder):
    recorderctl, _, config = recorder
    _orphan(config)
    completed = config.recording_root / "recordings-v2" / "completed" / f"recording_{SESSION}"
    completed.mkdir(parents=True)
    with pytest.raises(recorderctl.ControllerError) as error:
        recorderctl.start_incomplete_recover(
            config, SESSION, "44b1d5f0-1a11-4d5e-9f2a-0f2b7f9a11aa"
        )
    assert error.value.code == "INCOMPLETE_ALREADY_PUBLISHED"


def test_recovery_space_precheck_uses_the_largest_selected_asset(recorder, monkeypatch):
    recorderctl, _, config = recorder
    _orphan(config)
    plan = recorderctl._recovery_plan(
        config, SESSION, config.incoming / f".{SESSION}.partial", "ir"
    )
    rgb_only = recorderctl._recovery_plan(
        config, SESSION, config.incoming / f".{SESSION}.partial", "rgb"
    )
    assert plan["required_bytes"] > 0
    assert plan["required_bytes"] <= rgb_only["required_bytes"]
    monkeypatch.setattr(
        recorderctl.shutil, "disk_usage",
        lambda path: shutil._ntuple_diskusage(10 * GB, 9 * GB + 1, 1 * 1024 * 1024),
    )
    with pytest.raises(recorderctl.ControllerError) as error:
        recorderctl.start_incomplete_recover(
            config, SESSION, "44b1d5f0-1a11-4d5e-9f2a-0f2b7f9a11aa"
        )
    assert error.value.code == "LOCAL_SPACE_LOW"
    assert error.value.data["free_bytes"] == 1 * 1024 * 1024
    assert error.value.data["required_bytes"] > error.value.data["free_bytes"]


def test_incomplete_recover_dry_run_plans_without_spawning(recorder, monkeypatch):
    recorderctl, _, config = recorder
    _orphan(config)
    monkeypatch.setattr(
        recorderctl.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("dry run must not spawn")
    )
    data = recorderctl.start_incomplete_recover(
        config, SESSION, "44b1d5f0-1a11-4d5e-9f2a-0f2b7f9a11aa", assets="ir", dry_run=True
    )
    assert data["dry_run"] is True
    assert data["assets"] == ["infrared_left", "infrared_right"]
    assert data["verdict"] in {"ok", "LOCAL_SPACE_LOW"}
    assert not (config.recoveries / f"{SESSION}.json").exists()


def test_incomplete_recover_launches_a_detached_worker(recorder, monkeypatch):
    recorderctl, _, config = recorder
    _orphan(config)
    started = {}

    class _Worker:
        pid = 4242

    def fake_popen(command, **kwargs):
        started["command"] = command
        started["kwargs"] = kwargs
        return _Worker()

    monkeypatch.setattr(recorderctl.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(recorderctl, "proc_ticks", lambda pid: 999)
    data = recorderctl.start_incomplete_recover(
        config, SESSION, "44b1d5f0-1a11-4d5e-9f2a-0f2b7f9a11aa"
    )
    assert data["accepted"] is True and data["idempotent"] is False
    assert started["command"][-2:] == ["--session", SESSION]
    assert started["command"][-3] == "_recover"
    assert started["kwargs"]["start_new_session"] is True
    stored = recorderctl.read_json(config.recoveries / f"{SESSION}.json")
    assert stored["worker_pid"] == 4242 and stored["worker_start_ticks"] == 999
    assert stored["total_bytes"] > 0


def test_recovery_worker_refuses_a_foreign_identity(recorder):
    recorderctl, _, config = recorder
    _orphan(config)
    recorderctl.atomic_json(
        config.recoveries / f"{SESSION}.json",
        {"session": SESSION, "state": "starting", "boot_id": config.boot_id,
         "worker_pid": 1, "worker_start_ticks": 1, "request_id": "x"},
    )
    assert recorderctl.run_recovery_worker(config, SESSION) == 2


def test_active_recovery_blocks_a_new_capture(recorder, monkeypatch):
    recorderctl, _, config = recorder
    recorderctl.atomic_json(
        config.recoveries / f"{SESSION}.json",
        {"session": SESSION, "state": "recovering", "boot_id": config.boot_id,
         "worker_pid": os.getpid(), "worker_start_ticks": recorderctl.proc_ticks(os.getpid()),
         "request_id": "44b1d5f0-1a11-4d5e-9f2a-0f2b7f9a11aa"},
    )
    with pytest.raises(recorderctl.ControllerError) as error:
        recorderctl.start(config, "44b1d5f0-1a11-4d5e-9f2a-0f2b7f9a11aa", 5)
    assert error.value.code == "MAINTENANCE_BUSY"


def test_stale_recovery_worker_is_reconciled_to_failure(recorder):
    recorderctl, _, config = recorder
    recorderctl.atomic_json(
        config.recoveries / f"{SESSION}.json",
        {"session": SESSION, "state": "recovering", "boot_id": config.boot_id,
         "worker_pid": 2**22, "worker_start_ticks": 1, "request_id": "x"},
    )
    assert recorderctl._active_recovery(config) is None
    stored = recorderctl.read_json(config.recoveries / f"{SESSION}.json")
    assert stored["state"] == "failed" and stored["phase"] == "worker_missing"


# ---------------------------------------------------------------------------
# the rescue pipeline itself


@pytest.fixture()
def catalog_stubs(monkeypatch: pytest.MonkeyPatch):
    """A catalog double that records what the rescue published."""
    published: list[dict] = []

    class _Catalog:
        def __init__(self, path):
            self.path = path

        def migrate(self):
            with sqlite3.connect(self.path) as database:
                database.execute(
                    "CREATE TABLE IF NOT EXISTS recordings (recording_id TEXT PRIMARY KEY, "
                    "device_id TEXT, recorded_at TEXT, duration_ms INTEGER, total_bytes INTEGER, "
                    "state TEXT, manifest_sha256 TEXT, save_state TEXT, display_name TEXT, "
                    "recovery_hint TEXT)"
                )
                database.execute(
                    "CREATE TABLE IF NOT EXISTS catalog_recording_assets (recording_id TEXT, "
                    "position INTEGER, relative_path TEXT, size_bytes INTEGER, sha256 TEXT, "
                    "PRIMARY KEY(recording_id, position))"
                )

        def publish_recording(self, **payload):
            published.append(payload)
            with sqlite3.connect(self.path) as database:
                database.execute(
                    "INSERT OR REPLACE INTO recordings (recording_id, device_id, recorded_at, "
                    "duration_ms, total_bytes, state, manifest_sha256, save_state, display_name, "
                    "recovery_hint) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        payload["recording_id"], payload["device_id"], payload["recorded_at"],
                        payload["duration_ms"], payload["total_bytes"], payload["state"],
                        payload["manifest_sha256"], payload["save_state"],
                        payload["display_name"], payload.get("recovery_hint"),
                    ),
                )
                for position, asset in enumerate(payload.get("assets", ())):
                    database.execute(
                        "INSERT OR REPLACE INTO catalog_recording_assets (recording_id, position, "
                        "relative_path, size_bytes, sha256) VALUES (?,?,?,?,?)",
                        (
                            payload["recording_id"], position, asset["relative_path"],
                            asset["size_bytes"], asset["sha256"],
                        ),
                    )
            return dict(payload)

    monkeypatch.setitem(sys.modules, "catalog_db", types.SimpleNamespace(CatalogDb=_Catalog))
    return published


def _run_recovery(recorderctl, config, *, session=SESSION, selection="all", **kwargs):
    _, _, directory = recorderctl._session_or_fail(config, session)
    operation = {
        "session": session,
        "request_id": "44b1d5f0-1a11-4d5e-9f2a-0f2b7f9a11aa",
        "assets": selection,
        "delete_remainder": kwargs.pop("delete_remainder", False),
        "state": "recovering",
        "phase": "classify",
        "boot_id": config.boot_id,
    }
    recorderctl.atomic_json(config.recoveries / f"{session}.json", operation)
    return recorderctl.recover_incomplete_sync(config, operation)


def test_recovery_publishes_complete_local_with_a_recovery_hint(recorder, catalog_stubs):
    recorderctl, _, config = recorder
    _orphan(config, pairs=20)
    result = _run_recovery(recorderctl, config)
    assert result["state"] == "complete"
    assert result["recording_id"] == f"recording_{SESSION}"
    assert result["stereo_pairs"] == 20
    assert result["quality_status"] in {"DEGRADED", "FAILED"}
    assert "RECOVERED_FROM_INTERRUPTED_CAPTURE" in result["warnings"]
    payload = catalog_stubs[-1]
    assert payload["state"] == "COMPLETE_LOCAL"
    assert payload["save_state"] == "LOCAL_ONLY"
    assert payload["recovery_hint"].startswith("RECOVERED_PARTIAL")
    assert payload["imu_quality_status"] == result["quality_status"]
    assert "已抢救" in payload["display_name"]
    published = Path(result["output_dir"])
    assert (published / "rgb.mp4").is_file()
    assert (published / "infrared-left-y8.mp4").is_file()
    assert (published / "infrared-right-y8.mp4").is_file()
    assert (published / "stm32.bin").is_file()
    assert (published / "manifest.json").is_file()
    assert (published / "MANIFEST.sha256").is_file()
    assert not (config.incoming / f".{SESSION}.partial").exists()
    assert not (config.incoming / f".recover-{SESSION}").exists()


def test_recovered_manifest_keeps_profile_and_device(recorder, catalog_stubs):
    recorderctl, _, config = recorder
    directory = _orphan(config, pairs=10)
    _run_recovery(recorderctl, config)
    manifest = json.loads(
        (config.recording_root / "recordings-v2" / "completed" / f"recording_{SESSION}" / "manifest.json")
        .read_text()
    )
    assert manifest["profile"] == {"rgb": {"width": 1280, "height": 720, "fps": 30}}
    assert manifest["device"] == {"d405_serial": "260322273737"}
    assert manifest["counts"]["ir_frames"] == manifest["counts"]["rgb_input_frames"] == 10
    assert manifest["metrics"]["stm32"]["crc_errors"] is None
    assert manifest["metrics"]["recovery"]["crc_counters_observable"] is False
    assert manifest["metrics"]["recovery"]["unobserved_counters"]


def test_stm32_payload_is_truncated_to_whole_packets(recorder, catalog_stubs):
    recorderctl, _, config = recorder
    _orphan(config, pairs=5, stm32_packets=100)
    result = _run_recovery(recorderctl, config)
    payload = Path(result["output_dir"]) / "stm32.bin"
    size = payload.stat().st_size
    assert size % 63 == 0
    assert size == 100 * 63


def test_the_source_is_released_only_after_the_remux_verifies(recorder, catalog_stubs, monkeypatch):
    recorderctl, _, config = recorder
    directory = _orphan(config, pairs=12)
    source = directory / "rgb.h265.partial"
    calls = {"n": 0}

    import umi_remux

    real_verify = umi_remux.verify_roundtrip

    def failing_verify(path, **kwargs):
        calls["n"] += 1
        raise umi_remux.RemuxError("injected verification failure")

    monkeypatch.setattr(umi_remux, "verify_roundtrip", failing_verify)
    with pytest.raises(recorderctl.ControllerError) as error:
        _run_recovery(recorderctl, config)
    assert error.value.code == "INCOMPLETE_REMUX_FAILED"
    assert source.is_file(), "the source must survive a failed rescue"
    assert not (config.recording_root / "recordings-v2" / "completed" / f"recording_{SESSION}").exists()
    monkeypatch.setattr(umi_remux, "verify_roundtrip", real_verify)


def test_unselected_assets_stay_behind_and_are_reported(recorder, catalog_stubs):
    recorderctl, _, config = recorder
    directory = _orphan(config, pairs=9)
    result = _run_recovery(recorderctl, config, selection="ir")
    assert (directory / "rgb.h265.partial").is_file()
    assert result["leftover_bytes"] > 0
    published = Path(result["output_dir"])
    assert (published / "infrared-left-y8.mp4").is_file()
    assert not (published / "rgb.mp4").exists()
    listing = recorderctl.open_incomplete_list(config)
    assert listing["sessions"][0]["session"] == SESSION


def test_delete_remainder_reclaims_the_rest(recorder, catalog_stubs):
    recorderctl, _, config = recorder
    directory = _orphan(config, pairs=9)
    result = _run_recovery(recorderctl, config, selection="ir", delete_remainder=True)
    assert result["leftover_bytes"] == 0
    assert not directory.exists()


def test_a_sealed_session_is_published_without_remuxing(recorder, catalog_stubs, monkeypatch):
    recorderctl, _, config = recorder
    _orphan(config, pairs=7, kind="sealed")
    import umi_remux

    monkeypatch.setattr(
        umi_remux, "remux", lambda *args, **kwargs: pytest.fail("a sealed session needs no remux")
    )
    result = _run_recovery(recorderctl, config)
    assert result["state"] == "complete"
    published = Path(result["output_dir"])
    # the sealed assets keep their native names
    assert (published / "rgb.h265").is_file()
    assert not (published / "rgb.mp4").exists()


def test_recovery_fails_closed_when_the_indexes_describe_nothing(recorder, catalog_stubs):
    recorderctl, _, config = recorder
    directory = _orphan(config, pairs=4, with_indexes=False)
    _write_index(directory / "ir_frames.jsonl", 0)
    _write_index(directory / "rgb_frames.jsonl", 0)
    with pytest.raises(recorderctl.ControllerError) as error:
        _run_recovery(recorderctl, config)
    assert error.value.code == "INCOMPLETE_GATE_FAILED"


def test_recovery_clears_a_rolled_back_publication_ledger(recorder, catalog_stubs):
    recorderctl, _, config = recorder
    _orphan(config, pairs=6)
    ledger_dir = config.recording_root / "recordings-v2" / ".publication-ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    ledger = ledger_dir / f"recording_{SESSION}.json"
    ledger.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "state": "ROLLED_BACK",
                "device_id": config.device_id,
                "recording_id": f"recording_{SESSION}",
                "source_relpath": f"incoming/{SESSION}",
                "prepared_relpath": f"recordings-v2/completed/.recording_{SESSION}.publishing",
                "final_relpath": f"recordings-v2/completed/recording_{SESSION}",
            }
        )
    )
    result = _run_recovery(recorderctl, config)
    assert result["state"] == "complete"


def test_recovery_missing_asset_is_reported(recorder, catalog_stubs):
    recorderctl, _, config = recorder
    directory = _orphan(config, pairs=6, assets=("infrared_left",))
    with pytest.raises(recorderctl.ControllerError) as error:
        _run_recovery(recorderctl, config, selection="rgb")
    assert error.value.code == "INCOMPLETE_ASSET_MISSING"


# ---------------------------------------------------------------------------
# deletion


def test_incomplete_delete_reclaims_the_tree_and_reports_freed_bytes(recorder):
    recorderctl, _, config = recorder
    directory = _orphan(config, pairs=15)
    expected = sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())
    data = recorderctl.start_incomplete_delete(
        config, SESSION, "44b1d5f0-1a11-4d5e-9f2a-0f2b7f9a11aa"
    )
    assert data["accepted"] is True
    operation = recorderctl.read_json(config.incomplete_deletions / f"{SESSION}.json")
    result = recorderctl.incomplete_delete_sync(config, operation)
    assert result["freed_bytes"] == expected
    assert not directory.exists()
    assert recorderctl.open_incomplete_list(config)["sessions"] == []


def test_incomplete_delete_refuses_symlinked_content(recorder, tmp_path):
    recorderctl, _, config = recorder
    directory = _orphan(config, pairs=4)
    (directory / "escape").symlink_to(tmp_path)
    with pytest.raises(recorderctl.ControllerError) as error:
        recorderctl._delete_orphan_tree(directory, config.incoming)
    assert error.value.code == "INCOMPLETE_UNSAFE_PATH"
    assert directory.exists()


def test_incomplete_delete_is_refused_while_a_capture_is_active(recorder):
    recorderctl, _, config = recorder
    _orphan(config)
    recorderctl.atomic_json(
        config.current,
        {"job_id": "umi-active", "state": "recording", "boot_id": config.boot_id,
         "record_pid": os.getpid(), "record_start_ticks": recorderctl.proc_ticks(os.getpid()),
         "worker_pid": os.getpid(), "worker_start_ticks": recorderctl.proc_ticks(os.getpid())},
    )
    with pytest.raises(recorderctl.ControllerError) as error:
        recorderctl.start_incomplete_delete(
            config, SESSION, "44b1d5f0-1a11-4d5e-9f2a-0f2b7f9a11aa"
        )
    assert error.value.code == "ACTIVE_JOB"


def test_incomplete_delete_worker_refuses_a_foreign_identity(recorder):
    recorderctl, _, config = recorder
    _orphan(config)
    recorderctl.atomic_json(
        config.incomplete_deletions / f"{SESSION}.json",
        {"session": SESSION, "state": "starting", "boot_id": config.boot_id,
         "worker_pid": 1, "worker_start_ticks": 1, "request_id": "x"},
    )
    assert recorderctl.run_incomplete_delete_worker(config, SESSION) == 2


def test_incomplete_status_projects_operations(recorder):
    recorderctl, _, config = recorder
    recorderctl.atomic_json(
        config.recoveries / f"{SESSION}.json",
        {"session": SESSION, "state": "complete", "phase": "done", "recording_id": "recording_x",
         "request_id": "44b1d5f0-1a11-4d5e-9f2a-0f2b7f9a11aa", "boot_id": config.boot_id},
    )
    data = recorderctl.incomplete_status(config)
    assert data["operations"][0]["session"] == SESSION
    assert data["operations"][0]["state"] == "complete"


# ---------------------------------------------------------------------------
# the rescued recording behaves like any other recording


def test_a_rescued_recording_deletes_through_the_existing_catalog_path(
    recorder, catalog_stubs, monkeypatch
):
    recorderctl, _, config = recorder
    _orphan(config, pairs=8)
    result = _run_recovery(recorderctl, config)
    recording_id = result["recording_id"]
    root = Path(result["output_dir"])
    manifest_sha256 = result["manifest_sha256"]

    def _catalog_row():
        with sqlite3.connect(config.catalog_db) as database:
            database.row_factory = sqlite3.Row
            return database.execute(
                "SELECT recording_id, device_id, state, manifest_sha256, save_state "
                "FROM recordings WHERE recording_id = ?",
                (recording_id,),
            ).fetchone()

    def _assets():
        with sqlite3.connect(config.catalog_db) as database:
            database.row_factory = sqlite3.Row
            return database.execute(
                "SELECT relative_path, size_bytes, sha256 FROM catalog_recording_assets "
                "WHERE recording_id = ? ORDER BY position",
                (recording_id,),
            ).fetchall()

    catalog = types.SimpleNamespace(mark_recording_deleted=None)

    def mark_recording_deleted(*, recording_id, device_id, manifest_sha256):
        with sqlite3.connect(config.catalog_db) as database:
            database.execute(
                "UPDATE recordings SET save_state = 'SOURCE_DELETED' WHERE recording_id = ?",
                (recording_id,),
            )
        return {"recording_id": recording_id, "save_state": "SOURCE_DELETED"}

    catalog.mark_recording_deleted = mark_recording_deleted
    monkeypatch.setitem(sys.modules, "catalog_db", types.SimpleNamespace(CatalogDb=lambda path: catalog))
    deleted = []

    def delete_catalog_tree(path, identity, assets):
        deleted.append((path, identity, assets))
        shutil.rmtree(path)

    monkeypatch.setitem(
        sys.modules,
        "transfer_commit",
        types.SimpleNamespace(delete_catalog_tree=delete_catalog_tree, TransferCommitError=RuntimeError),
    )
    monkeypatch.setattr(recorderctl, "_recording_identity", lambda cfg, rid: {
        "recording_id": rid,
        "device_id": config.device_id,
        "state": "COMPLETE_LOCAL",
        "manifest_sha256": manifest_sha256,
        "save_state": _catalog_row()["save_state"],
        "assets": [dict(row) for row in _assets()],
    })
    operation = {
        "recording_id": recording_id,
        "request_id": "44b1d5f0-1a11-4d5e-9f2a-0f2b7f9a11aa",
        "state": "deleting",
        "phase": "verify_recording",
    }
    recorderctl.atomic_json(config.deletions / f"{recording_id}.json", operation)
    outcome = recorderctl.delete_recording_sync(config, operation)
    assert outcome["state"] == "complete"
    assert deleted and deleted[0][0] == root
    assert not root.exists()


def test_a_stream_shorter_than_its_index_still_rescues(recorder, catalog_stubs):
    """A capture killed mid-stream can hold fewer access units than its index
    claims; the rescue must reconcile instead of refusing the data."""
    recorderctl, _, config = recorder
    directory = _orphan(config, pairs=40)
    # the IR stream lost frames while the index kept claiming them
    (directory / "infrared-left-y8.h265.partial").write_bytes(_hevc_stream(18))
    (directory / "infrared-right-y8.h265.partial").write_bytes(_hevc_stream(18))
    result = _run_recovery(recorderctl, config)
    assert result["state"] == "complete"
    assert result["stereo_pairs"] <= 18
    assert "STREAM_LENGTH_MISMATCH" in result["warnings"]
    published = Path(result["output_dir"])
    manifest = json.loads((published / "manifest.json").read_text())
    assert manifest["counts"]["ir_frames"] == result["stereo_pairs"]
    # the shipped index must not claim more frames than the video holds
    rows = len((published / "ir_frames.jsonl").read_text().strip().splitlines())
    assert rows == result["stereo_pairs"]


def test_index_longer_than_the_video_is_truncated_on_disk(recorder, catalog_stubs):
    recorderctl, _, config = recorder
    directory = _orphan(config, pairs=30, rgb_rows=30)
    (directory / "rgb.h265.partial").write_bytes(_hevc_stream(12))
    result = _run_recovery(recorderctl, config)
    published = Path(result["output_dir"])
    rgb_rows = len((published / "rgb_frames.jsonl").read_text().strip().splitlines())
    assert rgb_rows == result["stereo_pairs"] == 12
    assert not (directory / "rgb_frames.jsonl").exists()


def test_a_failed_rescue_work_directory_is_visible_and_reclaimable(recorder):
    """A rescue that fails before publishing leaves .recover-<session>/ behind;
    once the orphan is deleted that directory is unreferenced garbage."""
    recorderctl, _, config = recorder
    staging = config.incoming / f".recover-{SESSION}"
    staging.mkdir(parents=True)
    (staging / "rgb.mp4").write_bytes(b"x" * 4096)

    listing = recorderctl.open_incomplete_list(config)
    assert [entry["kind"] for entry in listing["sessions"]] == ["staging_leftover"]
    entry = listing["sessions"][0]
    assert entry["total_bytes"] == 4096
    assert entry["recoverable"] is False

    with pytest.raises(recorderctl.ControllerError) as error:
        recorderctl.start_incomplete_recover(
            config, SESSION, "44b1d5f0-1a11-4d5e-9f2a-0f2b7f9a11aa"
        )
    assert error.value.code == "INCOMPLETE_NOT_FOUND"

    operation = {
        "session": SESSION,
        "request_id": "44b1d5f0-1a11-4d5e-9f2a-0f2b7f9a11aa",
        "state": "deleting",
        "boot_id": config.boot_id,
    }
    recorderctl.atomic_json(config.incomplete_deletions / f"{SESSION}.json", operation)
    result = recorderctl.incomplete_delete_sync(config, operation)
    assert result["freed_bytes"] == 4096
    assert not staging.exists()


def test_deleting_an_orphan_also_reclaims_its_staging_directory(recorder):
    recorderctl, _, config = recorder
    _orphan(config, pairs=6)
    staging = config.incoming / f".recover-{SESSION}"
    staging.mkdir(parents=True)
    (staging / "infrared-left-y8.mp4").write_bytes(b"y" * 2048)
    operation = {
        "session": SESSION,
        "request_id": "44b1d5f0-1a11-4d5e-9f2a-0f2b7f9a11aa",
        "state": "deleting",
        "boot_id": config.boot_id,
    }
    recorderctl.atomic_json(config.incomplete_deletions / f"{SESSION}.json", operation)
    result = recorderctl.incomplete_delete_sync(config, operation)
    assert result["freed_bytes"] > 2048
    assert not staging.exists()
    assert not (config.incoming / f".{SESSION}.partial").exists()


def test_the_orphan_is_preferred_over_a_leftover_work_directory(recorder):
    recorderctl, _, config = recorder
    _orphan(config, pairs=5)
    (config.incoming / f".recover-{SESSION}").mkdir(parents=True)
    listing = recorderctl.open_incomplete_list(config)
    assert [entry["kind"] for entry in listing["sessions"]] == ["unsealed"]


def test_a_completed_deletion_does_not_hide_a_later_work_directory(recorder):
    """Deleting the orphan once must not make a rescue work directory that
    appears later permanently undeletable."""
    recorderctl, _, config = recorder
    request_id = "44b1d5f0-1a11-4d5e-9f2a-0f2b7f9a11aa"
    recorderctl.atomic_json(
        config.incomplete_deletions / f"{SESSION}.json",
        {"session": SESSION, "request_id": request_id, "state": "complete", "phase": "done",
         "freed_bytes": 1024, "boot_id": config.boot_id},
    )
    # nothing left: a replay is idempotent
    data = recorderctl.start_incomplete_delete(config, SESSION, request_id)
    assert data["idempotent"] is True

    # a failed rescue leaves a work directory behind afterwards
    staging = config.incoming / f".recover-{SESSION}"
    staging.mkdir(parents=True)
    (staging / "rgb.mp4").write_bytes(b"z" * 2048)
    data = recorderctl.start_incomplete_delete(config, SESSION, "55c2e6a1-2b22-4e6f-8a3b-1f3c8faa22bb")
    assert data["idempotent"] is False
    operation = recorderctl.read_json(config.incomplete_deletions / f"{SESSION}.json")
    result = recorderctl.incomplete_delete_sync(config, operation)
    assert result["freed_bytes"] == 2048
    assert not staging.exists()


def test_incomplete_status_reports_the_most_recent_record(recorder):
    recorderctl, _, config = recorder
    recorderctl.atomic_json(
        config.recoveries / f"{SESSION}.json",
        {"session": SESSION, "state": "failed", "phase": "recovery_failed",
         "last_heartbeat_at": "2026-09-17T10:00:00.000Z", "boot_id": config.boot_id},
    )
    recorderctl.atomic_json(
        config.incomplete_deletions / f"{SESSION}.json",
        {"session": SESSION, "state": "complete", "phase": "done", "freed_bytes": 4096,
         "last_heartbeat_at": "2026-09-17T10:05:00.000Z", "boot_id": config.boot_id},
    )
    assert recorderctl.incomplete_status(config, SESSION)["state"] == "complete"
