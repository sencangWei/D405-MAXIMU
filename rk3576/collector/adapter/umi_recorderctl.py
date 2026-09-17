#!/usr/bin/env python3
"""EGO recorderctl compatibility layer for the native RK3576 UMI collector."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.request import Request, urlopen

from umi_publish import STM32_ZERO_METRICS, publish_session, recover_pending_publications


SCHEMA_VERSION = 1
CONTROLLER_VERSION = "0.3.3-umi"
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
RECORDING_ID = re.compile(r"recording_[A-Za-z0-9_-]{1,160}\Z")
# Native staging identity: SAFE_ID rejects a leading dot, so orphan directory
# names are validated by stripping the affixes and matching the session itself.
SESSION_NAME = re.compile(r"rk3576-rsusb-cpp-\d{8}T\d{6}Z-[0-9a-f]{8}\Z")
ORPHAN_SUFFIX = ".partial"
STAGING_PREFIX = ".recover-"
UNSEALED_MARKER = ".recording"
SESSION_CONFIG = "session_config.json"
STM32_PACKET_BYTES = 63
RECOVERY_SLACK_BYTES = 512 * 1024**2
RECOVERY_SIZE_FACTOR = 1.02
ASSET_SELECTIONS = ("all", "rgb", "ir")
VIDEO_ASSETS = ("rgb", "infrared_left", "infrared_right")
ACTIVE = {"starting", "recording", "stop_requested", "finalizing"}
FINAL = {"complete_local", "incomplete", "interrupted"}
DELETE_ACTIVE = {"starting", "deleting"}
RECOVERY_ACTIVE = {"starting", "recovering", "publishing"}
INCOMPLETE_DELETE_ACTIVE = {"starting", "deleting"}
# A rescued session is honest about itself: the normal capture gate cannot be
# re-proved from an interrupted stream, so the catalog row carries a quality
# status instead of a silent PASSED.
RECOVERY_WARNINGS = (
    "RECOVERED_FROM_INTERRUPTED_CAPTURE",
    "TAIL_ACCESS_UNIT_DROPPED",
    "STM32_CRC_COUNTERS_NOT_OBSERVABLE",
    "CONTINUITY_NOT_VERIFIED",
)
# (source stem, output stem) per video asset; the native collector renames the
# *.partial staging names to their final names while sealing.
ASSET_FILES = {
    "rgb": ("rgb.h265", "rgb.mp4"),
    "infrared_left": ("infrared-left-y8.h265", "infrared-left-y8.mp4"),
    "infrared_right": ("infrared-right-y8.h265", "infrared-right-y8.mp4"),
}
ASSET_CAMERA = {
    "rgb": "rgb",
    "infrared_left": "infrared-left",
    "infrared_right": "infrared-right",
}
SELECTION_ASSETS = {
    "all": VIDEO_ASSETS,
    "rgb": ("rgb",),
    "ir": ("infrared_left", "infrared_right"),
}


class ControllerError(RuntimeError):
    def __init__(self, code: str, message: str, data: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data or {}


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value or value.startswith("CHANGE_ME"):
        raise ControllerError("CONFIG_MISSING", f"{name} must be explicitly configured")
    return value


class Config:
    def __init__(self) -> None:
        self.release_root = Path(__file__).resolve().parents[1]
        self.state_dir = Path(os.environ.get("UMI_STATE_DIR", "/home/pi/.local/state/umi-recorder"))
        self.recording_root = Path(os.environ.get("UMI_RECORDING_ROOT", "/home/pi/umi-recordings"))
        self.device_id = required_env("UMI_DEVICE_ID")
        self.sdk_serial = required_env("UMI_D405_SDK_SERIAL")
        self.usb_serial = required_env("UMI_D405_USB_SERIAL")
        self.stm32_port = required_env("UMI_STM32_PORT")
        if SAFE_ID.fullmatch(self.device_id) is None:
            raise ControllerError("CONFIG_INVALID", "UMI_DEVICE_ID is invalid")
        self.preview_source_port = int(os.environ.get("UMI_PREVIEW_SOURCE_PORT", "18081"))
        self.native = Path(os.environ.get("UMI_NATIVE_COLLECTOR", str(self.release_root / "native/bin/umi-record-native")))
        self.catalog_db = Path(os.environ.get("EGO_CATALOG_DB", str(self.state_dir / "catalog.sqlite3")))
        self.boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        self.current = self.state_dir / "current.json"
        self.lock = self.state_dir / "controller.lock"
        self.jobs = self.state_dir / "jobs"
        self.requests = self.state_dir / "requests"
        self.deletions = self.state_dir / "deletions"
        self.recoveries = self.state_dir / "recoveries"
        self.incomplete_deletions = self.state_dir / "incomplete-deletions"
        self.incoming = self.recording_root / "incoming"

    def prepare(self) -> None:
        for path in (
            self.state_dir,
            self.jobs,
            self.requests,
            self.deletions,
            self.recoveries,
            self.incomplete_deletions,
            self.incoming,
        ):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(value, dict):
        raise ControllerError("STATE_INVALID", "controller state is invalid")
    return value


@contextmanager
def locked(cfg: Config):
    cfg.prepare()
    with cfg.lock.open("a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield


def proc_ticks(pid: int) -> int:
    fields = Path(f"/proc/{pid}/stat").read_text(encoding="ascii").split()
    return int(fields[21])


def process_matches(state: dict) -> bool:
    pid = state.get("record_pid")
    ticks = state.get("record_start_ticks")
    if not isinstance(pid, int) or pid <= 0 or not isinstance(ticks, int):
        return False
    try:
        return proc_ticks(pid) == ticks
    except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
        return False


def worker_matches(cfg: Config, state: dict) -> bool:
    if state.get("boot_id") != cfg.boot_id:
        return False
    pid = state.get("worker_pid")
    ticks = state.get("worker_start_ticks")
    if not isinstance(pid, int) or pid <= 0 or not isinstance(ticks, int):
        return False
    try:
        return proc_ticks(pid) == ticks
    except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
        return False


def _save_job_and_current_if_selected(cfg: Config, state: dict) -> None:
    atomic_json(cfg.jobs / f"{state['job_id']}.json", state)
    current = read_json(cfg.current)
    if current is not None and current.get("job_id") == state.get("job_id"):
        atomic_json(cfg.current, state)


def _active_delete(cfg: Config) -> dict | None:
    return _active_operation(cfg, cfg.deletions, "recording_*.json", DELETE_ACTIVE)


def _active_operation(cfg: Config, directory: Path, pattern: str, states: set[str]) -> dict | None:
    """Reconcile one operation directory: a vanished worker becomes a failure."""
    cfg.prepare()
    for path in sorted(directory.glob(pattern)):
        value = read_json(path)
        if value is None or value.get("state") not in states:
            continue
        if worker_matches(cfg, value):
            return value
        value.update(
            state="failed",
            phase="worker_missing",
            error="operation worker disappeared",
            last_heartbeat_at=now_iso(),
        )
        atomic_json(path, value)
    return None


def _active_recovery(cfg: Config) -> dict | None:
    return _active_operation(cfg, cfg.recoveries, "*.json", RECOVERY_ACTIVE)


def _active_incomplete_delete(cfg: Config) -> dict | None:
    return _active_operation(cfg, cfg.incomplete_deletions, "*.json", INCOMPLETE_DELETE_ACTIVE)


def _stop_orphan_recorder(state: dict) -> bool:
    if not process_matches(state):
        return True
    pid = state["record_pid"]
    for signum, timeout_s in ((signal.SIGINT, 4.0), (signal.SIGTERM, 2.0)):
        try:
            os.kill(pid, signum)
        except ProcessLookupError:
            return True
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if not process_matches(state):
                return True
            time.sleep(0.05)
    return not process_matches(state)


def reconcile_stale_state(cfg: Config, state: dict | None) -> dict | None:
    if state is None or state.get("state") not in ACTIVE or worker_matches(cfg, state):
        return state
    if state.get("boot_id") == cfg.boot_id and not _stop_orphan_recorder(state):
        unresolved = dict(state)
        unresolved.update(
            phase="orphan_recorder_shutdown_pending",
            error="controller worker disappeared and native recorder has not stopped",
            recovery_reason="ORPHAN_RECORDER_STILL_RUNNING",
            last_heartbeat_at=now_iso(),
        )
        _save_job_and_current_if_selected(cfg, unresolved)
        return unresolved
    recovered = dict(state)
    recovered.update(
        state="interrupted",
        phase="recovered_stale_state",
        error="active process identity disappeared or belongs to an earlier boot",
        recovery_reason="STALE_PROCESS_IDENTITY",
        last_heartbeat_at=now_iso(),
    )
    _save_job_and_current_if_selected(cfg, recovered)
    return recovered


def reconcile_published_job(cfg: Config, publication: dict) -> None:
    job_id = publication.get("job_id")
    if not isinstance(job_id, str) or SAFE_ID.fullmatch(job_id) is None:
        raise ValueError("recovered publication job_id is invalid")
    state = read_json(cfg.jobs / f"{job_id}.json")
    if state is None or state.get("state") == "complete_local":
        return
    state.update(
        state="complete_local",
        phase="done",
        mode="STEREO_IMU",
        stereo_pairs=publication.get("stereo_pairs"),
        imu_samples=publication.get("imu_samples"),
        imu_runtime_state="ok",
        imu_quality_status="PASSED",
        imu_quality_error_codes=[],
        local_data_present=True,
        recording_id=publication.get("recording_id"),
        output_dir=publication.get("output_dir"),
        manifest_sha256=publication.get("manifest_sha256"),
        recovery_reason="PUBLICATION_LEDGER_REPLAYED",
        last_heartbeat_at=now_iso(),
    )
    _save_job_and_current_if_selected(cfg, state)


def status_data(state: dict | None) -> dict:
    if state is None:
        return {
            "job_id": None,
            "state": "idle",
            "phase": "idle",
            "elapsed_s": 0.0,
            "output_dir": None,
            "mode": None,
            "stereo_pairs": None,
            "imu_samples": None,
            "imu_runtime_state": None,
            "imu_quality_status": None,
            "imu_quality_error_codes": [],
            "local_data_present": False,
            "usb_verified": False,
            "capture_started": False,
            "capture_running": False,
            "capture_stop_confirmed": False,
            "capture_elapsed_s": 0.0,
            "saving_running": False,
            "saving_elapsed_s": 0.0,
            "timing_source": "monotonic_v1",
        }
    started = state.get("capture_started_monotonic_ns")
    ended = state.get("capture_ended_monotonic_ns")
    elapsed = float(state.get("capture_elapsed_s") or 0.0)
    if isinstance(started, int):
        boundary = ended if isinstance(ended, int) else time.monotonic_ns()
        if boundary >= started:
            elapsed = (boundary - started) / 1e9
    remote_state = state.get("state")
    return {
        "job_id": state.get("job_id"),
        "request_id": state.get("request_id"),
        "boot_id": state.get("boot_id"),
        "state": remote_state,
        "phase": state.get("phase", remote_state),
        "elapsed_s": elapsed,
        "capture_started": isinstance(started, int),
        "capture_running": isinstance(started, int) and not isinstance(ended, int) and remote_state in {"recording", "stop_requested"},
        "capture_stop_confirmed": isinstance(ended, int),
        "capture_elapsed_s": elapsed,
        "saving_running": remote_state == "finalizing",
        "saving_elapsed_s": float(state.get("saving_elapsed_s") or 0.0),
        "timing_source": "monotonic_v1",
        "output_dir": state.get("output_dir"),
        "recording_id": state.get("recording_id"),
        "manifest_sha256": state.get("manifest_sha256"),
        "mode": state.get("mode"),
        "stereo_pairs": state.get("stereo_pairs"),
        "imu_samples": state.get("imu_samples"),
        "imu_runtime_state": state.get("imu_runtime_state"),
        "imu_fault_code": state.get("imu_fault_code"),
        "imu_fault_detail": state.get("imu_fault_detail"),
        "imu_quality_status": state.get("imu_quality_status"),
        "imu_quality_error_codes": state.get("imu_quality_error_codes", []),
        "local_data_present": state.get("local_data_present") is True,
        "usb_verified": False,
        "recovery_verified": False,
        "exit_code": state.get("exit_code"),
        "last_heartbeat_at": state.get("last_heartbeat_at"),
        "stop_sent_at": state.get("stop_sent_at"),
    }


def identity(cfg: Config) -> dict:
    return {
        "device_id": cfg.device_id,
        "model": "UMI-D405-RK3576",
        "platform": "rk3576",
        "controller_version": CONTROLLER_VERSION,
        "recorder_root": str(cfg.release_root),
        "capabilities": [
            "capture_timing_v1",
            "preview_v1",
            "preview_shared_media_v1",
            "catalog_v1",
            "resumable_transfer_v1",
            "recording_delete_v1",
            "incomplete_recovery_v1",
            "incomplete_delete_v1",
        ],
    }


def _preflight_locked(cfg: Config) -> dict:
    state = reconcile_stale_state(cfg, read_json(cfg.current))
    publications = recover_pending_publications(
        recording_root=cfg.recording_root,
        catalog_db_path=cfg.catalog_db,
        device_id=cfg.device_id,
    )
    for publication in publications:
        reconcile_published_job(cfg, publication)
    state = read_json(cfg.current)
    active = status_data(state) if state and state.get("state") in ACTIVE else None
    try:
        free = shutil.disk_usage(cfg.recording_root).free
    except OSError as error:
        raise ControllerError("LOCAL_STORAGE_UNAVAILABLE", str(error)) from error
    d405 = any(
        (path / "serial").is_file()
        and (path / "serial").read_text(encoding="ascii").strip() == cfg.usb_serial
        for path in Path("/sys/bus/usb/devices").iterdir()
    )
    stm32 = Path(cfg.stm32_port).exists()
    return {
        "recorder_exists": cfg.native.is_file() and os.access(cfg.native, os.X_OK),
        "local_free_bytes": free,
        "min_local_free_bytes": 2 * 1024**3,
        "camera_busy": active is not None,
        "maintenance_active": False,
        "maintenance_kind": None,
        "active_job": active,
        "d405_present": d405,
        "stm32_present": stm32,
        "imu": {
            "state": "unknown" if stm32 else "fault",
            "sample_count": 0,
            "error_code": (
                "IMU_LIVE_PROBE_UNAVAILABLE" if stm32 else "STM32_NOT_FOUND"
            ),
            "detail": (
                "Sensor packets are checked during capture warmup"
                if stm32
                else "STM32 serial device is unavailable"
            ),
        },
    }


def preflight(cfg: Config) -> dict:
    cfg.prepare()
    with locked(cfg):
        return _preflight_locked(cfg)


def start(cfg: Config, request_id: str, duration: int) -> dict:
    try:
        request_id = str(uuid.UUID(request_id))
    except (ValueError, TypeError, AttributeError) as error:
        raise ControllerError("INVALID_REQUEST_ID", "request_id must be a canonical UUID") from error
    if not isinstance(duration, int) or isinstance(duration, bool) or duration < 0 or duration > 86400:
        raise ControllerError("INVALID_DURATION", "duration must be 0..86400 seconds")
    cfg.prepare()
    with locked(cfg):
        current = reconcile_stale_state(cfg, read_json(cfg.current))
        deleting = _active_delete(cfg)
        if deleting is not None:
            raise ControllerError(
                "MAINTENANCE_BUSY",
                "a recording deletion is active",
                {"recording_id": deleting.get("recording_id")},
            )
        recovering = _active_recovery(cfg)
        if recovering is not None:
            raise ControllerError(
                "MAINTENANCE_BUSY",
                "an incomplete-recording rescue is active",
                {"session": recovering.get("session")},
            )
        if _active_incomplete_delete(cfg) is not None:
            raise ControllerError("MAINTENANCE_BUSY", "an incomplete-recording deletion is active")
        replay = read_json(cfg.requests / f"{request_id}.json")
        if replay is not None:
            state = read_json(cfg.jobs / f"{replay['job_id']}.json") or replay
            state = reconcile_stale_state(cfg, state) or state
            return {"accepted": True, "idempotent": True, "job_id": replay["job_id"], "state": state["state"]}
        check = _preflight_locked(cfg)
        if current and current.get("state") in ACTIVE:
            raise ControllerError("ALREADY_RECORDING", "a recording is already active", status_data(current))
        if not check["recorder_exists"] or not check["d405_present"] or not check["stm32_present"]:
            raise ControllerError("PREFLIGHT_FAILED", "D405, STM32, or native collector is unavailable", check)
        if check["local_free_bytes"] < check["min_local_free_bytes"]:
            raise ControllerError("LOCAL_SPACE_LOW", "less than 2 GiB free space remains", check)
        job_id = "umi-" + uuid.uuid4().hex
        created = {
            "schema_version": 1,
            "device_id": cfg.device_id,
            "boot_id": cfg.boot_id,
            "job_id": job_id,
            "request_id": request_id,
            "duration": duration,
            "state": "starting",
            "phase": "launch_collector",
            "created_at": now_iso(),
            "last_heartbeat_at": now_iso(),
        }
        atomic_json(cfg.current, created)
        atomic_json(cfg.jobs / f"{job_id}.json", created)
        atomic_json(cfg.requests / f"{request_id}.json", created)
        worker = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "_run", "--job-id", job_id],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        created.update(worker_pid=worker.pid, worker_start_ticks=proc_ticks(worker.pid))
        _save_state(cfg, created)
        atomic_json(cfg.requests / f"{request_id}.json", created)
    return {"accepted": True, "idempotent": False, "job_id": job_id, "state": "starting"}


def status(cfg: Config, job_id: str | None) -> dict:
    with locked(cfg):
        state = read_json(cfg.jobs / f"{job_id}.json") if job_id else read_json(cfg.current)
        if job_id and state is None:
            raise ControllerError("JOB_NOT_FOUND", "job_id was not found")
        return status_data(reconcile_stale_state(cfg, state))


def stop(cfg: Config, job_id: str) -> dict:
    if SAFE_ID.fullmatch(job_id) is None:
        raise ControllerError("INVALID_ARGUMENT", "job_id is invalid")
    with locked(cfg):
        state = read_json(cfg.jobs / f"{job_id}.json")
        if state is None:
            raise ControllerError("JOB_NOT_FOUND", "job_id was not found")
        state = reconcile_stale_state(cfg, state)
        if state.get("state") in FINAL:
            raise ControllerError("JOB_NOT_ACTIVE", "job is already complete")
        if state.get("stop_sent_at"):
            return {"accepted": True, "already_requested": True, "job_id": job_id, "state": state["state"]}
        if not process_matches(state):
            raise ControllerError("PROCESS_NOT_READY", "collector process identity is not available")
        os.kill(state["record_pid"], signal.SIGINT)
        state.update(state="stop_requested", phase="stop_requested", stop_sent_at=now_iso(), last_heartbeat_at=now_iso())
        _save_state(cfg, state)
    return {"accepted": True, "already_requested": False, "job_id": job_id, "state": "stop_requested"}


def _save_state(cfg: Config, state: dict) -> None:
    atomic_json(cfg.jobs / f"{state['job_id']}.json", state)
    atomic_json(cfg.current, state)


def run_worker(cfg: Config, job_id: str) -> int:
    with locked(cfg):
        state = read_json(cfg.jobs / f"{job_id}.json")
        if (
            state is None
            or state.get("state") != "starting"
            or state.get("boot_id") != cfg.boot_id
            or state.get("worker_pid") != os.getpid()
            or state.get("worker_start_ticks") != proc_ticks(os.getpid())
        ):
            return 2
    duration = int(state["duration"])
    try:
        prepare_preview_handoff(cfg)
    except ControllerError as error:
        with locked(cfg):
            state = read_json(cfg.jobs / f"{job_id}.json") or state
            state.update(
                state="interrupted",
                phase="preview_handoff_failed",
                error=error.message,
                last_heartbeat_at=now_iso(),
            )
            _save_state(cfg, state)
        return 1
    command = [
        str(cfg.native),
        "--output-root", str(cfg.incoming),
        "--duration", str(duration or 86400),
        "--until-signal",
        "--d405-sdk-serial", cfg.sdk_serial,
        "--d405-usb-serial", cfg.usb_serial,
        "--stm32-port", cfg.stm32_port,
        "--ir-encoding", "y8_split_h265",
        "--preview-mjpeg-port", str(cfg.preview_source_port),
    ]
    log_path = cfg.jobs / f"{job_id}.collector.log"
    with log_path.open("ab", buffering=0) as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=log, start_new_session=False)
        with locked(cfg):
            state = read_json(cfg.jobs / f"{job_id}.json") or state
            state.update(
                state="recording",
                phase="capture",
                record_pid=process.pid,
                record_start_ticks=proc_ticks(process.pid),
                capture_started_monotonic_ns=time.monotonic_ns(),
                last_heartbeat_at=now_iso(),
            )
            _save_state(cfg, state)
        stdout, _ = process.communicate()
    ended_ns = time.monotonic_ns()
    try:
        result = json.loads(stdout.decode("utf-8").strip())
    except (UnicodeDecodeError, json.JSONDecodeError):
        result = {}
    with locked(cfg):
        state = read_json(cfg.jobs / f"{job_id}.json") or state
        state.update(
            state="finalizing" if process.returncode == 0 and result.get("status") == "SEALED" else "interrupted",
            phase="publish_catalog" if process.returncode == 0 else "capture_failed",
            capture_ended_monotonic_ns=ended_ns,
            capture_elapsed_s=max(0.0, (ended_ns - state["capture_started_monotonic_ns"]) / 1e9),
            exit_code=process.returncode,
            last_heartbeat_at=now_iso(),
        )
        if state["state"] == "interrupted":
            state["error"] = result.get("reason") or f"collector exit {process.returncode}"
        _save_state(cfg, state)
    if state["state"] == "interrupted":
        return 1
    try:
        publication = publish_session(
            session=Path(result["session"]),
            recording_root=cfg.recording_root,
            catalog_db_path=cfg.catalog_db,
            device_id=cfg.device_id,
            job_id=job_id,
            request_id=state["request_id"],
            boot_id=cfg.boot_id,
        )
    except Exception as error:
        with locked(cfg):
            state = read_json(cfg.jobs / f"{job_id}.json") or state
            state.update(state="incomplete", phase="publication_failed", error=str(error), last_heartbeat_at=now_iso())
            _save_state(cfg, state)
        return 1
    with locked(cfg):
        state = read_json(cfg.jobs / f"{job_id}.json") or state
        state.update(
            state="complete_local",
            phase="done",
            mode="STEREO_IMU",
            stereo_pairs=publication["stereo_pairs"],
            imu_samples=publication["imu_samples"],
            imu_runtime_state="ok",
            imu_quality_status="PASSED",
            imu_quality_error_codes=[],
            local_data_present=True,
            recording_id=publication["recording_id"],
            output_dir=publication["output_dir"],
            manifest_sha256=publication["manifest_sha256"],
            publication_ledger=publication["ledger"],
            last_heartbeat_at=now_iso(),
        )
        _save_state(cfg, state)
    return 0


def preview_health() -> dict | None:
    try:
        with urlopen("http://127.0.0.1:18080/healthz", timeout=1.5) as response:
            value = json.load(response)
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def prepare_preview_handoff(cfg: Config) -> None:
    health = preview_health()
    if health is None or health.get("session_id") is None:
        return
    body = json.dumps({"device_id": cfg.device_id}, separators=(",", ":")).encode("utf-8")
    request = Request(
        "http://127.0.0.1:18080/internal/recording-handoff",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=12.0) as response:
            result = json.load(response)
    except Exception as error:
        raise ControllerError(
            "PREVIEW_HANDOFF_FAILED",
            "active preview could not release the D405 for recording",
        ) from error
    if (
        not isinstance(result, dict)
        or result.get("released") is not True
        or result.get("session_id") not in {None, health.get("session_id")}
    ):
        raise ControllerError(
            "PREVIEW_HANDOFF_FAILED",
            "preview handoff response did not match the active session",
        )


def logs(cfg: Config, job_id: str, after: int, limit: int) -> dict:
    state = read_json(cfg.jobs / f"{job_id}.json")
    if state is None:
        raise ControllerError("JOB_NOT_FOUND", "job_id was not found")
    events = []
    if after < 1:
        events.append({"cursor": 1, "timestamp": state.get("created_at", now_iso()), "level": "info", "phase": state.get("phase"), "message": state.get("error") or state.get("state")})
    return {"events": events[:limit], "next_cursor": max([after] + [item["cursor"] for item in events])}


def _recording_identity(cfg: Config, recording_id: str) -> dict:
    if RECORDING_ID.fullmatch(recording_id or "") is None:
        raise ControllerError("INVALID_RECORDING_ID", "recording_id is invalid")
    if not cfg.catalog_db.is_file():
        raise ControllerError("RECORDING_NOT_FOUND", "recording is not published")
    try:
        with sqlite3.connect(cfg.catalog_db) as database:
            database.row_factory = sqlite3.Row
            row = database.execute(
                "SELECT recording_id, device_id, state, manifest_sha256, save_state "
                "FROM recordings WHERE recording_id = ?",
                (recording_id,),
            ).fetchone()
            assets = database.execute(
                "SELECT relative_path, size_bytes, sha256 "
                "FROM catalog_recording_assets WHERE recording_id = ? ORDER BY position",
                (recording_id,),
            ).fetchall()
    except sqlite3.Error as error:
        raise ControllerError("CATALOG_UNAVAILABLE", "recording catalog is unavailable") from error
    if row is None:
        raise ControllerError("RECORDING_NOT_FOUND", "recording is not published")
    value = dict(row)
    if (
        value.get("device_id") != cfg.device_id
        or value.get("state") != "COMPLETE_LOCAL"
        or HEX64.fullmatch(value.get("manifest_sha256") or "") is None
    ):
        raise ControllerError("RECORDING_IDENTITY_INVALID", "recording identity is invalid")
    value["assets"] = [dict(item) for item in assets]
    return value


def _manifest_delete_snapshot(root: Path, identity: dict) -> tuple[dict, list[dict]]:
    completed = root.parent.resolve()
    if root.is_symlink() or root.resolve().parent != completed:
        raise ControllerError("UNSAFE_RECORDING_PATH", "recording directory is unsafe")
    try:
        root_stat = root.lstat()
    except FileNotFoundError as error:
        raise ControllerError("RECORDING_NOT_FOUND", "recording directory is unavailable") from error
    if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
        raise ControllerError("UNSAFE_RECORDING_PATH", "recording directory is unsafe")
    manifest_path = root / "MANIFEST.sha256"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ControllerError("RECORDING_CHANGED", "recording manifest is unavailable")
    raw = manifest_path.read_bytes()
    manifest_sha256 = hashlib.sha256(raw).hexdigest()
    if manifest_sha256 != identity["manifest_sha256"]:
        raise ControllerError("RECORDING_CHANGED", "recording manifest identity changed")
    expected: list[dict] = []
    seen: set[str] = set()
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ControllerError("RECORDING_CHANGED", "recording manifest is invalid") from error
    for line in lines:
        try:
            digest, relative = line.split("  ", 1)
        except ValueError as error:
            raise ControllerError("RECORDING_CHANGED", "recording manifest is invalid") from error
        parts = PurePosixPath(relative).parts
        if (
            HEX64.fullmatch(digest) is None
            or not parts
            or relative.startswith("/")
            or relative in seen
            or any(part in {"", ".", ".."} for part in parts)
            or "\\" in relative
            or ":" in relative
        ):
            raise ControllerError("RECORDING_CHANGED", "recording manifest is invalid")
        seen.add(relative)
        expected.append({"path": relative, "sha256": digest})
    catalog_assets = [
        {
            "path": item["relative_path"],
            "size": item["size_bytes"],
            "sha256": item["sha256"],
        }
        for item in identity["assets"]
    ]
    if [(item["path"], item["sha256"]) for item in catalog_assets] != [
        (item["path"], item["sha256"]) for item in expected
    ]:
        raise ControllerError("RECORDING_CHANGED", "catalog and manifest assets differ")
    catalog_assets.append(
        {"path": "MANIFEST.sha256", "size": len(raw), "sha256": manifest_sha256}
    )
    root_identity = {"dev": root_stat.st_dev, "ino": root_stat.st_ino, "mode": root_stat.st_mode}
    return root_identity, catalog_assets


def _retire_publication_ledger(cfg: Config, recording_id: str) -> None:
    """Mark the publication ledger DELETED so recovery does not treat the
    user-confirmed deletion as a lost payload and block later captures."""
    ledger_path = cfg.recording_root / "recordings-v2" / ".publication-ledger" / f"{recording_id}.json"
    ledger = read_json(ledger_path)
    if (
        not isinstance(ledger, dict)
        or ledger.get("recording_id") != recording_id
        or ledger.get("state") == "DELETED"
    ):
        return
    ledger.update(
        state="DELETED",
        deleted_at=now_iso(),
        delete_reason="recording_delete_v1 confirmed by user",
    )
    atomic_json(ledger_path, ledger)


def _mark_jobs_deleted(cfg: Config, recording_id: str, root: Path) -> None:
    resolved = root.resolve(strict=False)
    for path in [*sorted(cfg.jobs.glob("umi-*.json")), cfg.current]:
        state = read_json(path)
        if state is None or state.get("recording_id") != recording_id:
            continue
        output = state.get("output_dir")
        if not isinstance(output, str) or Path(output).resolve(strict=False) != resolved:
            raise ControllerError("RECORDING_IDENTITY_INVALID", "job output identity is invalid")
        state.update(
            local_data_present=False,
            cleanup_deleted_at=now_iso(),
            cleanup_reason="user_confirmed_recording_delete",
        )
        atomic_json(path, state)


def delete_recording_sync(cfg: Config, operation: dict) -> dict:
    recording_id = operation.get("recording_id")
    request_id = operation.get("request_id")
    if RECORDING_ID.fullmatch(recording_id or "") is None:
        raise ControllerError("INVALID_RECORDING_ID", "recording_id is invalid")
    try:
        request_id = str(uuid.UUID(str(request_id)))
    except (TypeError, ValueError, AttributeError) as error:
        raise ControllerError("INVALID_REQUEST_ID", "request_id must be a canonical UUID") from error
    identity = _recording_identity(cfg, recording_id)
    root = cfg.recording_root / "recordings-v2" / "completed" / recording_id
    if identity.get("save_state") == "SOURCE_DELETED":
        _mark_jobs_deleted(cfg, recording_id, root)
        _retire_publication_ledger(cfg, recording_id)
        return {"state": "complete", "recording_id": recording_id, "request_id": request_id, "idempotent": True}
    if root.exists():
        root_identity, assets = _manifest_delete_snapshot(root, identity)
        operation["delete_snapshot"] = {
            "manifest_sha256": identity["manifest_sha256"],
            "root_identity": root_identity,
            "assets": assets,
        }
        operation.update(phase="delete_tree", last_heartbeat_at=now_iso())
        atomic_json(cfg.deletions / f"{recording_id}.json", operation)
        from transfer_commit import TransferCommitError, delete_catalog_tree

        try:
            delete_catalog_tree(root, root_identity, assets)
        except TransferCommitError as error:
            raise ControllerError("LOCAL_DELETE_FAILED", str(error)) from error
    else:
        snapshot = operation.get("delete_snapshot")
        if not isinstance(snapshot, dict) or snapshot.get("manifest_sha256") != identity["manifest_sha256"]:
            raise ControllerError("RECORDING_CHANGED", "recording disappeared without a deletion snapshot")
    operation["phase"] = "tree_deleted"
    operation["last_heartbeat_at"] = now_iso()
    atomic_json(cfg.deletions / f"{recording_id}.json", operation)
    from catalog_db import CatalogDb

    try:
        result = CatalogDb(cfg.catalog_db).mark_recording_deleted(
            recording_id=recording_id,
            device_id=cfg.device_id,
            manifest_sha256=identity["manifest_sha256"],
        )
    except Exception as error:
        raise ControllerError("CATALOG_UPDATE_FAILED", "catalog deletion state update failed") from error
    if not isinstance(result, dict) or result.get("save_state") != "SOURCE_DELETED":
        raise ControllerError("CATALOG_UPDATE_FAILED", "catalog deletion state is invalid")
    _mark_jobs_deleted(cfg, recording_id, root)
    _retire_publication_ledger(cfg, recording_id)
    return {"state": "complete", "recording_id": recording_id, "request_id": request_id, "idempotent": False}


def start_delete_recording(cfg: Config, recording_id: str, request_id: str) -> dict:
    if RECORDING_ID.fullmatch(recording_id or "") is None:
        raise ControllerError("INVALID_RECORDING_ID", "recording_id is invalid")
    try:
        request_id = str(uuid.UUID(str(request_id)))
    except (TypeError, ValueError, AttributeError) as error:
        raise ControllerError("INVALID_REQUEST_ID", "request_id must be a canonical UUID") from error
    with locked(cfg):
        current = reconcile_stale_state(cfg, read_json(cfg.current))
        if current is not None and current.get("state") in ACTIVE:
            raise ControllerError("ACTIVE_JOB", "recording deletion is blocked during capture")
        if _active_recovery(cfg) is not None or _active_incomplete_delete(cfg) is not None:
            raise ControllerError("MAINTENANCE_BUSY", "another maintenance operation is active")
        active = _active_delete(cfg)
        path = cfg.deletions / f"{recording_id}.json"
        previous = read_json(path)
        if active is not None:
            if active.get("recording_id") == recording_id and active.get("request_id") == request_id:
                return {"accepted": True, "idempotent": True, **_delete_status_projection(active)}
            raise ControllerError("MAINTENANCE_BUSY", "another recording deletion is active")
        if previous is not None and previous.get("state") == "complete":
            return {"accepted": True, "idempotent": True, **_delete_status_projection(previous)}
        operation = {
            "schema_version": 1,
            "device_id": cfg.device_id,
            "boot_id": cfg.boot_id,
            "recording_id": recording_id,
            "request_id": request_id,
            "state": "starting",
            "phase": "launch_delete_worker",
            "created_at": now_iso(),
            "last_heartbeat_at": now_iso(),
        }
        if previous is not None and isinstance(previous.get("delete_snapshot"), dict):
            operation["delete_snapshot"] = previous["delete_snapshot"]
        atomic_json(path, operation)
        worker = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "_delete", "--recording-id", recording_id],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        operation.update(worker_pid=worker.pid, worker_start_ticks=proc_ticks(worker.pid))
        atomic_json(path, operation)
    return {"accepted": True, "idempotent": False, **_delete_status_projection(operation)}


def run_delete_worker(cfg: Config, recording_id: str) -> int:
    path = cfg.deletions / f"{recording_id}.json"
    with locked(cfg):
        operation = read_json(path)
        if (
            operation is None
            or operation.get("state") != "starting"
            or operation.get("boot_id") != cfg.boot_id
            or operation.get("worker_pid") != os.getpid()
            or operation.get("worker_start_ticks") != proc_ticks(os.getpid())
        ):
            return 2
        operation.update(state="deleting", phase="verify_recording", last_heartbeat_at=now_iso())
        atomic_json(path, operation)
    try:
        result = delete_recording_sync(cfg, operation)
    except ControllerError as error:
        operation.update(state="failed", phase="delete_failed", error=error.message, error_code=error.code, last_heartbeat_at=now_iso())
        with locked(cfg):
            atomic_json(path, operation)
        return 1
    operation.update(**result, phase="done", completed_at=now_iso(), last_heartbeat_at=now_iso())
    with locked(cfg):
        atomic_json(path, operation)
    return 0


def delete_status(cfg: Config, recording_id: str | None = None) -> dict:
    cfg.prepare()
    with locked(cfg):
        _active_delete(cfg)
        if recording_id is not None:
            if RECORDING_ID.fullmatch(recording_id or "") is None:
                raise ControllerError("INVALID_RECORDING_ID", "recording_id is invalid")
            value = read_json(cfg.deletions / f"{recording_id}.json")
            if value is None:
                raise ControllerError("DELETE_NOT_FOUND", "recording deletion was not found")
            return _delete_status_projection(value)
        return {
            "operations": [
                _delete_status_projection(value)
                for path in sorted(cfg.deletions.glob("recording_*.json"))
                if (value := read_json(path)) is not None
            ]
        }


def _delete_status_projection(value: dict) -> dict:
    fields = (
        "recording_id",
        "request_id",
        "state",
        "phase",
        "error_code",
        "error",
        "created_at",
        "completed_at",
        "last_heartbeat_at",
    )
    return {field: value.get(field) for field in fields if field in value}


# ---------------------------------------------------------------------------
# Incomplete (interrupted) recordings
#
# A power loss or SIGKILL leaves unsealed staging in incoming/.<session>.partial
# (marker .recording = "unsealed"). Nothing publishes it, so the catalog-driven
# Web console cannot see it and the operator cannot reclaim the space. These
# helpers list, rescue (lossless remux into a normal local recording) and delete
# such sessions.


def _session_from_dirname(name: str) -> str | None:
    if name.startswith(STAGING_PREFIX):
        candidate = name[len(STAGING_PREFIX) :]
    elif name.startswith(".") and name.endswith(ORPHAN_SUFFIX):
        candidate = name[1 : -len(ORPHAN_SUFFIX)]
    else:
        candidate = name
    return candidate if SESSION_NAME.fullmatch(candidate) else None


def _session_dirs(cfg: Config, session: str) -> dict:
    incoming = cfg.incoming
    return {
        "orphan": incoming / f".{session}{ORPHAN_SUFFIX}",
        "staging": incoming / f"{STAGING_PREFIX}{session}",
        "sealed": incoming / session,
    }


def _classify_session_dir(path: Path) -> str:
    """sealed_unpublished | unsealed | staging_leftover | unknown (never guesses)."""
    if path.name.startswith(STAGING_PREFIX):
        # a rescue that failed before publishing leaves its work directory behind;
        # once the orphan is gone it is unreferenced and only reclaimable here
        return "staging_leftover"
    manifest_path = path / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = None
        if (
            isinstance(manifest, dict)
            and manifest.get("status") == "SEALED"
            and manifest.get("session_id") == _session_from_dirname(path.name)
        ):
            return "sealed_unpublished"
    if (path / UNSEALED_MARKER).exists():
        return "unsealed"
    return "unknown"


def _asset_source(directory: Path, asset: str) -> Path:
    stem, _ = ASSET_FILES[asset]
    staged = directory / f"{stem}{ORPHAN_SUFFIX}"
    return staged if staged.is_file() else directory / stem


def _parse_row(line: bytes) -> dict | None:
    try:
        value = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _index_summary(path: Path) -> dict:
    """Row count and edge rows of a JSONL index without reading the whole file.

    Rows are written with a monotonic ``record_index``, so the last complete
    row gives the count. A torn final line (power cut mid-write) is reported
    and excluded.
    """
    summary = {"rows": 0, "first": None, "last": None, "torn_tail": False}
    if not path.is_file():
        return summary
    size = path.stat().st_size
    if size == 0:
        return summary
    with path.open("rb") as stream:
        head = stream.readline()
        if head.endswith(b"\n"):
            summary["first"] = _parse_row(head)
        window = min(size, 1 << 20)
        stream.seek(size - window)
        tail = stream.read(window)
        if tail.endswith(b"\n"):
            tail = tail[:-1]
        else:
            summary["torn_tail"] = True
            cut = tail.rfind(b"\n")
            tail = tail[:cut] if cut >= 0 else b""
        if tail:
            summary["last"] = _parse_row(tail[tail.rfind(b"\n") + 1 :])
    last_index = (summary["last"] or {}).get("record_index")
    if isinstance(last_index, int) and last_index >= 0:
        summary["rows"] = last_index + 1
    return summary


def _stm32_recovery_metrics(payload: Path, index: Path) -> dict:
    """Recompute what the index and payload can still prove.

    The native collector only writes a row for an accepted packet, so
    crc_errors/discarded_bytes and the flag-derived counters cannot be observed
    from an interrupted session; they are reported as null and declared
    unobservable instead of being silently claimed as zero.
    """
    summary = _index_summary(index)
    first = summary["first"] or {}
    last = summary["last"] or {}
    packets_index = summary["rows"]
    payload_bytes = payload.stat().st_size if payload.is_file() else 0
    packets_payload = payload_bytes // STM32_PACKET_BYTES
    packets = min(packets_index, packets_payload) if packets_index else packets_payload
    first_ns = first.get("host_read_complete_monotonic_ns")
    last_ns = last.get("host_read_complete_monotonic_ns")
    span_ns = 0
    if isinstance(first_ns, int) and isinstance(last_ns, int) and last_ns > first_ns:
        span_ns = last_ns - first_ns
    rate = (packets - 1) * 1e9 / span_ns if span_ns > 0 and packets > 1 else 0.0
    metrics = {name: None for name in STM32_ZERO_METRICS}
    metrics.update(
        packets=packets,
        observed_rate_hz=rate,
        coverage_rate_hz=rate,
        first_rx_monotonic_ns=first_ns,
        last_rx_monotonic_ns=last_ns,
        tail_covered=False,
        crc_counters_observable=False,
        index_rows=packets_index,
        payload_bytes=payload_bytes,
        torn_index_tail=summary["torn_tail"],
        truncated_bytes=payload_bytes - packets * STM32_PACKET_BYTES,
    )
    return metrics


def _video_counts(directory: Path) -> dict:
    return {
        "ir_frames": _index_summary(directory / "ir_frames.jsonl")["rows"],
        "rgb_input_frames": _index_summary(directory / "rgb_frames.jsonl")["rows"],
    }


def _orphan_inventory(cfg: Config, session: str, directory: Path, kind: str) -> dict:
    """Stat-only view of one incomplete session (never hashes; snapshot must not
    blow the web console's 45 s RPC budget)."""
    asset_bytes = {name: 0 for name in VIDEO_ASSETS}
    for asset in VIDEO_ASSETS:
        source = _asset_source(directory, asset)
        if source.is_file():
            asset_bytes[asset] = source.stat().st_size
    payload = directory / f"stm32.bin{ORPHAN_SUFFIX}"
    if not payload.is_file():
        payload = directory / "stm32.bin"
    imu_bytes = payload.stat().st_size if payload.is_file() else 0
    index_bytes = sum(
        path.stat().st_size
        for path in (
            directory / "ir_frames.jsonl",
            directory / "rgb_frames.jsonl",
            directory / "stm32_packets.jsonl",
        )
        if path.is_file()
    )
    probes = {
        asset: _video_probe(directory, asset) for asset in VIDEO_ASSETS
    }
    counts = _video_counts(directory) if kind != "unknown" else {"ir_frames": 0, "rgb_input_frames": 0}
    pairs = min(counts["ir_frames"], counts["rgb_input_frames"])
    total = sum(asset_bytes.values()) + imu_bytes + index_bytes
    space = {
        f"required_{selection}": _space_requirement(asset_bytes, assets)
        for selection, assets in SELECTION_ASSETS.items()
    }
    manifest = read_json(directory / "manifest.json") if kind == "sealed_unpublished" else None
    if isinstance(manifest, dict):
        sealed_counts = manifest.get("counts") if isinstance(manifest.get("counts"), dict) else {}
        pairs = sealed_counts.get("ir_frames") or pairs
    return {
        "session": session,
        "kind": kind,
        "directory": str(directory.relative_to(cfg.recording_root)),
        "total_bytes": total,
        "asset_bytes": asset_bytes,
        "imu_bytes": imu_bytes,
        "index_bytes": index_bytes,
        "assets_present": {
            asset: _asset_source(directory, asset).is_file() for asset in VIDEO_ASSETS
        },
        "usable": {
            asset: probes[asset].get("vps") and probes[asset].get("sps")
            and probes[asset].get("pps") and probes[asset].get("idr")
            for asset in VIDEO_ASSETS
        },
        "probe": probes,
        "pairs": pairs,
        "estimated_duration_s": round(pairs / 30.0, 3) if pairs else 0.0,
        "space": space,
    }


def _video_probe(directory: Path, asset: str) -> dict:
    source = _asset_source(directory, asset)
    if not source.is_file():
        return {"annexb": False, "vps": False, "sps": False, "pps": False, "idr": False}
    from umi_remux import probe_head

    return probe_head(source)


def _space_requirement(asset_bytes: dict, assets: tuple) -> int:
    largest = max((asset_bytes.get(asset, 0) for asset in assets), default=0)
    if largest <= 0:
        return 0
    return int(largest * RECOVERY_SIZE_FACTOR) + RECOVERY_SLACK_BYTES


def _pid_holds(pid: int, directories: list[Path]) -> bool:
    targets = tuple(f"{str(item)}/" for item in directories)
    try:
        entries = os.listdir(f"/proc/{pid}/fd")
    except (FileNotFoundError, PermissionError, ProcessLookupError, NotADirectoryError):
        return False
    for entry in entries:
        try:
            target = os.readlink(f"/proc/{pid}/fd/{entry}")
        except OSError:
            continue
        if target.startswith(targets):
            return True
    return False


def _session_is_live(cfg: Config, dirs: dict) -> int | None:
    """Return the pid still writing this session, if any.

    The precise check is the open file descriptors of the recorder: it lets an
    operator delete an old orphan while a *new* capture runs. If a job is
    ACTIVE but its recorder identity cannot be verified, mutations are refused
    conservatively instead.
    """
    directories = [path for path in dirs.values() if path.exists()]
    if not directories:
        return None
    state = read_json(cfg.current)
    if state is not None and state.get("state") in ACTIVE:
        if process_matches(state):
            pid = int(state["record_pid"])
            if _pid_holds(pid, directories):
                return pid
            return None
        return int(state.get("record_pid") or 0) or -1
    for entry in sorted(os.listdir("/proc"))[:4096]:
        if not entry.isdigit():
            continue
        if _pid_holds(int(entry), directories):
            return int(entry)
    return None


def _incomplete_session_entries(cfg: Config) -> list[tuple[str, str, Path]]:
    """(session, kind, directory) for every actionable incomplete session."""
    found: dict[str, tuple[str, str, Path]] = {}
    if not cfg.incoming.is_dir():
        return []
    for path in sorted(cfg.incoming.iterdir()):
        session = _session_from_dirname(path.name)
        if session is None:
            continue
        if path.is_symlink() or not path.is_dir():
            continue
        if path.resolve(strict=False).parent != cfg.incoming.resolve(strict=False):
            continue
        kind = _classify_session_dir(path)
        if kind == "unknown":
            continue
        previous = found.get(session)
        # the orphan (or its sealed form) is the actionable entry; a staging
        # directory is only reported when nothing else describes the session
        if previous is None:
            found[session] = (session, kind, path)
        elif previous[1] == "staging_leftover" and kind != "staging_leftover":
            found[session] = (session, kind, path)
    return [found[key] for key in sorted(found)]


def open_incomplete_list(cfg: Config) -> dict:
    cfg.prepare()
    with locked(cfg):
        active_recovery = _active_recovery(cfg)
        active_delete = _active_incomplete_delete(cfg)
        try:
            free = shutil.disk_usage(cfg.recording_root).free
        except OSError:
            free = 0
        state = read_json(cfg.current)
        capturing = bool(state and state.get("state") in ACTIVE)
        sessions = []
        for session, kind, directory in _incomplete_session_entries(cfg):
            if kind == "staging_leftover":
                entry = _staging_leftover_entry(cfg, session, directory)
                entry["live"] = False
            else:
                entry = _orphan_inventory(cfg, session, directory, kind)
                entry["live"] = _session_is_live(cfg, _session_dirs(cfg, session)) is not None
            entry["already_published"] = _recovery_target_exists(cfg, session)
            entry["recovery"] = (
                _recovery_projection(read_json(cfg.recoveries / f"{session}.json"))
                if (cfg.recoveries / f"{session}.json").is_file()
                else None
            )
            entry["deletion"] = (
                _incomplete_delete_projection(read_json(cfg.incomplete_deletions / f"{session}.json"))
                if (cfg.incomplete_deletions / f"{session}.json").is_file()
                else None
            )
            sessions.append(entry)
        return {
            "incoming_root": str(cfg.incoming),
            "free_bytes": free,
            "min_free_bytes": 2 * 1024**3,
            "capturing": capturing,
            "sessions": sessions,
            "recovery": _recovery_projection(active_recovery) if active_recovery else None,
            "deletion": _incomplete_delete_projection(active_delete) if active_delete else None,
        }


def _recovery_target_exists(cfg: Config, session: str) -> bool:
    recording_id = f"recording_{session}"
    ledger = read_json(
        cfg.recording_root / "recordings-v2" / ".publication-ledger" / f"{recording_id}.json"
    )
    if isinstance(ledger, dict) and ledger.get("state") in {"PREPARED", "PUBLISHED", "PREPARING"}:
        return True
    if (cfg.recording_root / "recordings-v2" / "completed" / recording_id).is_dir():
        return True
    if not cfg.catalog_db.is_file():
        return False
    try:
        with sqlite3.connect(cfg.catalog_db) as database:
            row = database.execute(
                "SELECT save_state FROM recordings WHERE recording_id = ?", (recording_id,)
            ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None and row[0] != "SOURCE_DELETED"


def _session_or_fail(cfg: Config, session: str) -> tuple[str, str, Path]:
    if not isinstance(session, str) or SESSION_NAME.fullmatch(session) is None:
        raise ControllerError("INCOMPLETE_SESSION_INVALID", "incomplete session name is invalid")
    for candidate, kind, directory in _incomplete_session_entries(cfg):
        if candidate == session:
            return candidate, kind, directory
    raise ControllerError("INCOMPLETE_NOT_FOUND", "incomplete session was not found")


def _resolve_request_id(request_id: str) -> str:
    try:
        return str(uuid.UUID(str(request_id)))
    except (TypeError, ValueError, AttributeError) as error:
        raise ControllerError("INVALID_REQUEST_ID", "request_id must be a canonical UUID") from error


def _recovery_plan(cfg: Config, session: str, directory: Path, selection: str) -> dict:
    if selection not in ASSET_SELECTIONS:
        raise ControllerError("INVALID_ARGUMENT", "assets must be all, rgb or ir")
    assets = SELECTION_ASSETS[selection]
    asset_bytes = {}
    sources = {}
    for asset in assets:
        source = _asset_source(directory, asset)
        if not source.is_file():
            raise ControllerError(
                "INCOMPLETE_ASSET_MISSING",
                f"{ASSET_FILES[asset][0]} is not present in this session",
                {"asset": asset},
            )
        probe = _video_probe(directory, asset)
        if not (probe.get("vps") and probe.get("sps") and probe.get("pps")):
            raise ControllerError(
                "INCOMPLETE_ASSET_UNREADABLE",
                f"{ASSET_FILES[asset][0]} has no usable parameter sets",
                {"asset": asset, "probe": probe},
            )
        asset_bytes[asset] = source.stat().st_size
        sources[asset] = source
    required = _space_requirement(asset_bytes, assets)
    try:
        free = shutil.disk_usage(cfg.recording_root).free
    except OSError:
        free = 0
    _, outputs = zip(*(ASSET_FILES[asset] for asset in assets))
    return {
        "session": session,
        "selection": selection,
        "assets": list(assets),
        "sources": {asset: str(sources[asset].name) for asset in assets},
        "outputs": list(outputs),
        "asset_bytes": asset_bytes,
        "largest_asset_bytes": max(asset_bytes.values(), default=0),
        "required_bytes": required,
        "free_bytes": free,
        "verdict": "ok" if free >= required else "LOCAL_SPACE_LOW",
    }


def start_incomplete_recover(
    cfg: Config,
    session: str,
    request_id: str,
    assets: str = "all",
    delete_remainder: bool = False,
    dry_run: bool = False,
) -> dict:
    request_id = _resolve_request_id(request_id)
    if not isinstance(session, str) or SESSION_NAME.fullmatch(session) is None:
        raise ControllerError("INCOMPLETE_SESSION_INVALID", "incomplete session name is invalid")
    cfg.prepare()
    with locked(cfg):
        previous = read_json(cfg.recoveries / f"{session}.json")
        if previous is not None and previous.get("state") == "complete":
            return {"accepted": True, "idempotent": True, **_recovery_projection(previous)}
        session, kind, directory = _session_or_fail(cfg, session)
        if delete_remainder not in (True, False):
            raise ControllerError("INVALID_ARGUMENT", "delete_remainder must be a boolean")
        if kind == "staging_leftover":
            raise ControllerError(
                "INCOMPLETE_NOT_FOUND",
                "only a failed rescue's work directory remains; delete it to reclaim the space",
            )
        plan = _recovery_plan(cfg, session, directory, assets)
        if dry_run:
            return {"accepted": True, "dry_run": True, "kind": kind, **plan}
        current = reconcile_stale_state(cfg, read_json(cfg.current))
        if current is not None and current.get("state") in ACTIVE:
            raise ControllerError("ACTIVE_JOB", "recording rescue is blocked during capture")
        if _active_delete(cfg) is not None or _active_incomplete_delete(cfg) is not None:
            raise ControllerError("MAINTENANCE_BUSY", "another maintenance operation is active")
        active = _active_recovery(cfg)
        path = cfg.recoveries / f"{session}.json"
        if active is not None:
            if active.get("session") == session and active.get("request_id") == request_id:
                return {"accepted": True, "idempotent": True, **_recovery_projection(active)}
            raise ControllerError("MAINTENANCE_BUSY", "another recording rescue is active")
        holder = _session_is_live(cfg, _session_dirs(cfg, session))
        if holder is not None:
            raise ControllerError(
                "INCOMPLETE_LIVE_CAPTURE",
                "session data is still being written",
                {"pid": holder},
            )
        if _recovery_target_exists(cfg, session):
            raise ControllerError(
                "INCOMPLETE_ALREADY_PUBLISHED",
                "this session is already published as a local recording",
            )
        if plan["verdict"] != "ok":
            raise ControllerError(
                "LOCAL_SPACE_LOW",
                "not enough free space for the selected assets",
                {
                    "free_bytes": plan["free_bytes"],
                    "required_bytes": plan["required_bytes"],
                    "largest_asset_bytes": plan["largest_asset_bytes"],
                    "selection": plan["selection"],
                    "suggestions": [name for name in ASSET_SELECTIONS if name != plan["selection"]],
                },
            )
        operation = {
            "schema_version": 1,
            "kind": "recover",
            "device_id": cfg.device_id,
            "boot_id": cfg.boot_id,
            "session": session,
            "request_id": request_id,
            "assets": assets,
            "delete_remainder": bool(delete_remainder),
            "state": "starting",
            "phase": "launch_recovery_worker",
            "bytes_done": 0,
            "total_bytes": sum(plan["asset_bytes"].values()),
            "free_bytes": plan["free_bytes"],
            "created_at": now_iso(),
            "last_heartbeat_at": now_iso(),
        }
        atomic_json(path, operation)
        worker = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "_recover", "--session", session],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        operation.update(worker_pid=worker.pid, worker_start_ticks=proc_ticks(worker.pid))
        atomic_json(path, operation)
    return {"accepted": True, "idempotent": False, **_recovery_projection(operation)}


def _recovery_projection(value: dict | None) -> dict | None:
    if value is None:
        return None
    fields = (
        "session",
        "request_id",
        "state",
        "phase",
        "assets",
        "asset",
        "bytes_done",
        "total_bytes",
        "free_bytes",
        "leftover_bytes",
        "recording_id",
        "quality_status",
        "warnings",
        "error_code",
        "error",
        "created_at",
        "completed_at",
        "last_heartbeat_at",
    )
    return {field: value.get(field) for field in fields if field in value}


def incomplete_status(cfg: Config, session: str | None = None) -> dict:
    cfg.prepare()
    with locked(cfg):
        _active_recovery(cfg)
        _active_incomplete_delete(cfg)
        if session is not None:
            if SESSION_NAME.fullmatch(session) is None:
                raise ControllerError("INCOMPLETE_SESSION_INVALID", "incomplete session name is invalid")
            candidates = [
                read_json(directory / f"{session}.json")
                for directory in (cfg.recoveries, cfg.incomplete_deletions)
            ]
            values = [value for value in candidates if value is not None]
            if not values:
                raise ControllerError("INCOMPLETE_NOT_FOUND", "incomplete session operation was not found")
            value = max(values, key=lambda item: item.get("last_heartbeat_at") or "")
            return _recovery_projection(value)
        return {
            "operations": [
                _recovery_projection(value)
                for directory in (cfg.recoveries, cfg.incomplete_deletions)
                for path in sorted(directory.glob("*.json"))
                if (value := read_json(path)) is not None
            ]
        }


def _incomplete_delete_projection(value: dict | None) -> dict | None:
    if value is None:
        return None
    fields = (
        "session",
        "request_id",
        "state",
        "phase",
        "freed_bytes",
        "error_code",
        "error",
        "created_at",
        "completed_at",
        "last_heartbeat_at",
    )
    return {field: value.get(field) for field in fields if field in value}


def _delete_orphan_tree(root: Path, incoming: Path) -> int:
    """Remove an unsealed staging directory without hashing its contents.

    Hashing 32 GB only to delete it is unacceptable, and there is no catalog
    identity to protect, so this validates the tree shape (regular files and
    real directories only, no symlinks, still inside incoming/) and then walks
    it. The caller passes the identity captured when the operation started.
    """
    if root.is_symlink() or root.resolve(strict=False).parent != incoming.resolve(strict=False):
        raise ControllerError("INCOMPLETE_UNSAFE_PATH", "incomplete session path is unsafe")
    try:
        info = root.lstat()
    except FileNotFoundError as error:
        raise ControllerError("INCOMPLETE_NOT_FOUND", "incomplete session directory is unavailable") from error
    if not stat.S_ISDIR(info.st_mode):
        raise ControllerError("INCOMPLETE_UNSAFE_PATH", "incomplete session path is not a directory")
    freed = 0
    for current, directories, files in os.walk(root, followlinks=False):
        base = Path(current)
        for name in list(directories) + list(files):
            path = base / name
            if path.is_symlink():
                raise ControllerError("INCOMPLETE_UNSAFE_PATH", "incomplete session contains a symlink")
            if path.is_file():
                if not stat.S_ISREG(path.lstat().st_mode):
                    raise ControllerError("INCOMPLETE_UNSAFE_PATH", "incomplete session holds a special file")
                freed += path.stat().st_size
            elif not path.is_dir():
                raise ControllerError("INCOMPLETE_UNSAFE_PATH", "incomplete session holds a special file")
    shutil.rmtree(root)
    _fsync_directory(root.parent)
    return freed


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def start_incomplete_delete(cfg: Config, session: str, request_id: str) -> dict:
    request_id = _resolve_request_id(request_id)
    if not isinstance(session, str) or SESSION_NAME.fullmatch(session) is None:
        raise ControllerError("INCOMPLETE_SESSION_INVALID", "incomplete session name is invalid")
    cfg.prepare()
    with locked(cfg):
        path = cfg.incomplete_deletions / f"{session}.json"
        previous = read_json(path)
        if previous is not None and previous.get("state") == "complete" and not _session_residue(cfg, session):
            return {"accepted": True, "idempotent": True, **_incomplete_delete_projection(previous)}
        session, kind, directory = _session_or_fail(cfg, session)
        current = reconcile_stale_state(cfg, read_json(cfg.current))
        if current is not None and current.get("state") in ACTIVE:
            raise ControllerError("ACTIVE_JOB", "deletion of incomplete data is blocked during capture")
        if _active_recovery(cfg) is not None or _active_delete(cfg) is not None:
            raise ControllerError("MAINTENANCE_BUSY", "another maintenance operation is active")
        active = _active_incomplete_delete(cfg)
        if active is not None:
            if active.get("session") == session and active.get("request_id") == request_id:
                return {"accepted": True, "idempotent": True, **_incomplete_delete_projection(active)}
            raise ControllerError("MAINTENANCE_BUSY", "another incomplete deletion is active")
        holder = _session_is_live(cfg, _session_dirs(cfg, session))
        if holder is not None:
            raise ControllerError(
                "INCOMPLETE_LIVE_CAPTURE",
                "session data is still being written",
                {"pid": holder},
            )
        operation = {
            "schema_version": 1,
            "kind": "incomplete-delete",
            "device_id": cfg.device_id,
            "boot_id": cfg.boot_id,
            "session": session,
            "kind_name": kind,
            "request_id": request_id,
            "state": "starting",
            "phase": "launch_delete_worker",
            "created_at": now_iso(),
            "last_heartbeat_at": now_iso(),
        }
        atomic_json(path, operation)
        worker = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "_incomplete-delete", "--session", session],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        operation.update(worker_pid=worker.pid, worker_start_ticks=proc_ticks(worker.pid))
        atomic_json(path, operation)
    return {"accepted": True, "idempotent": False, **_incomplete_delete_projection(operation)}


def _session_residue(cfg: Config, session: str) -> list[str]:
    dirs = _session_dirs(cfg, session)
    return [str(path) for path in dirs.values() if path.exists()]


def incomplete_delete_sync(cfg: Config, operation: dict) -> dict:
    session = operation["session"]
    _, _, directory = _session_or_fail(cfg, session)
    operation.update(phase="delete_tree", last_heartbeat_at=now_iso())
    atomic_json(cfg.incomplete_deletions / f"{session}.json", operation)
    freed = _delete_orphan_tree(directory, cfg.incoming)
    staging = cfg.incoming / f"{STAGING_PREFIX}{session}"
    if staging.exists():
        # a failed rescue leaves its work directory behind; the session is gone now
        operation.update(phase="delete_staging", last_heartbeat_at=now_iso())
        atomic_json(cfg.incomplete_deletions / f"{session}.json", operation)
        freed += _delete_orphan_tree(staging, cfg.incoming)
    return {"state": "complete", "freed_bytes": freed}


def run_incomplete_delete_worker(cfg: Config, session: str) -> int:
    path = cfg.incomplete_deletions / f"{session}.json"
    with locked(cfg):
        operation = read_json(path)
        if (
            operation is None
            or operation.get("state") != "starting"
            or operation.get("boot_id") != cfg.boot_id
            or operation.get("worker_pid") != os.getpid()
            or operation.get("worker_start_ticks") != proc_ticks(os.getpid())
        ):
            return 2
        operation.update(state="deleting", phase="verify_tree", last_heartbeat_at=now_iso())
        atomic_json(path, operation)
    try:
        result = incomplete_delete_sync(cfg, operation)
    except ControllerError as error:
        operation.update(
            state="failed", phase="delete_failed",
            error=error.message, error_code=error.code, last_heartbeat_at=now_iso(),
        )
        with locked(cfg):
            atomic_json(path, operation)
        return 1
    operation.update(**result, phase="done", completed_at=now_iso(), last_heartbeat_at=now_iso())
    with locked(cfg):
        atomic_json(path, operation)
    return 0


def run_recovery_worker(cfg: Config, session: str) -> int:
    path = cfg.recoveries / f"{session}.json"
    with locked(cfg):
        operation = read_json(path)
        if (
            operation is None
            or operation.get("state") != "starting"
            or operation.get("boot_id") != cfg.boot_id
            or operation.get("worker_pid") != os.getpid()
            or operation.get("worker_start_ticks") != proc_ticks(os.getpid())
        ):
            return 2
        operation.update(state="recovering", phase="classify", last_heartbeat_at=now_iso())
        atomic_json(path, operation)
    try:
        result = recover_incomplete_sync(cfg, operation)
    except ControllerError as error:
        operation.update(
            state="failed", phase="recovery_failed",
            error=error.message, error_code=error.code, last_heartbeat_at=now_iso(),
        )
        with locked(cfg):
            atomic_json(path, operation)
        return 1
    except Exception as error:  # noqa: BLE001 - surfaced as a sanitized code
        operation.update(
            state="failed", phase="recovery_failed",
            error=str(error), error_code="RECOVERY_FAILED", last_heartbeat_at=now_iso(),
        )
        with locked(cfg):
            atomic_json(path, operation)
        return 1
    operation.update(**result, phase="done", completed_at=now_iso(), last_heartbeat_at=now_iso())
    with locked(cfg):
        atomic_json(path, operation)
    return 0


def _touch_recovery(cfg: Config, operation: dict, **fields) -> None:
    operation.update(fields, last_heartbeat_at=now_iso())
    atomic_json(cfg.recoveries / f"{operation['session']}.json", operation)


def recover_incomplete_sync(cfg: Config, operation: dict) -> dict:
    """Rescue one interrupted session into a normal local recording.

    Nothing is written into the orphan directory: results land in
    incoming/.recover-<session>/ and only its final rename makes the payload
    publishable, so an interrupted rescue leaves the original bytes untouched.
    """
    from umi_publish import STM32_ZERO_METRICS, publish_session, recover_pending_publications

    session = operation["session"]
    selection = operation.get("assets", "all")
    _, kind, directory = _session_or_fail(cfg, session)
    holder = _session_is_live(cfg, _session_dirs(cfg, session))
    if holder is not None:
        raise ControllerError("INCOMPLETE_LIVE_CAPTURE", "session data started being written again")
    reconcile_stale_state(cfg, read_json(cfg.current))
    recover_pending_publications(
        recording_root=cfg.recording_root,
        catalog_db_path=cfg.catalog_db,
        device_id=cfg.device_id,
    )
    if _recovery_target_exists(cfg, session):
        raise ControllerError("INCOMPLETE_ALREADY_PUBLISHED", "this session is already published")

    orphan_dir = directory
    staging = cfg.incoming / f"{STAGING_PREFIX}{session}"
    staging.mkdir(parents=True, exist_ok=True, mode=0o700)
    plan = _recovery_plan(cfg, session, directory, selection)
    _touch_recovery(cfg, operation, phase="prepare_session", total_bytes=sum(plan["asset_bytes"].values()))

    if kind == "sealed_unpublished":
        # already sealed by the native collector: publish it as it stands. The
        # native marker is removed and a dot-prefixed staging name is renamed,
        # exactly like the collector's own seal step.
        already_sealed = _sealed_counts(directory)
        counts = {
            "ir_frames": already_sealed.get("ir_frames", 0),
            "rgb_input_frames": already_sealed.get("rgb_input_frames", 0),
        }
        payload = directory / f"stm32.bin{ORPHAN_SUFFIX}"
        if payload.is_file():
            os.replace(payload, directory / "stm32.bin")
        marker = directory / UNSEALED_MARKER
        if marker.exists():
            marker.unlink()
        staging.rmdir()  # the sealed path never stages anything
        if directory.name != session:
            staged_session = cfg.incoming / session
            if staged_session.exists():
                raise ControllerError(
                    "INCOMPLETE_ALREADY_PUBLISHED", "a sealed session directory already exists"
                )
            os.replace(directory, staged_session)
            _fsync_directory(cfg.incoming)
            directory = staged_session
        return _publish_recovered(
            cfg, operation, session, directory,
            counts=counts,
            stm32=_stm32_recovery_metrics(directory / "stm32.bin", directory / "stm32_packets.jsonl"),
            remuxed={}, selection="sealed", warnings=[],
            publish_dir=directory, write_manifest=False,
        )

    counts = _video_counts(directory)
    pairs = min(counts["ir_frames"], counts["rgb_input_frames"])
    warnings = list(RECOVERY_WARNINGS)
    if pairs <= 0:
        raise ControllerError(
            "INCOMPLETE_GATE_FAILED",
            "the frame indexes do not describe a usable stereo stream",
            {"counts": counts},
        )
    stm32 = _stm32_recovery_metrics(
        directory / f"stm32.bin{ORPHAN_SUFFIX}", directory / "stm32_packets.jsonl"
    )
    _touch_recovery(cfg, operation, phase="counters", pairs=pairs, counts=counts, imu=stm32["packets"])

    from umi_remux import RemuxError, remux, verify_roundtrip

    remuxed: dict[str, dict] = {}
    bytes_done = 0
    for asset in plan["assets"]:
        source = _asset_source(directory, asset)
        stem, output_name = ASSET_FILES[asset]
        required = int(source.stat().st_size * RECOVERY_SIZE_FACTOR) + RECOVERY_SLACK_BYTES
        free = shutil.disk_usage(cfg.recording_root).free
        if free < required:
            raise ControllerError(
                "LOCAL_SPACE_LOW",
                "not enough free space to remux the selected asset",
                {"asset": asset, "free_bytes": free, "required_bytes": required},
            )
        _touch_recovery(cfg, operation, phase="remux", asset=asset, free_bytes=free)
        target = staging / output_name
        try:
            result = remux(source, target, fps=30)
            check = verify_roundtrip(
                target, expected_samples=result.samples, expected_mdat_sha256=result.mdat_sha256
            )
        except RemuxError as error:
            raise ControllerError(
                "INCOMPLETE_REMUX_FAILED", f"remuxing {stem} failed", {"asset": asset, "detail": str(error)}
            ) from error
        if not check.ok:
            raise ControllerError(
                "INCOMPLETE_VERIFY_FAILED",
                f"the remuxed {output_name} did not verify",
                {"asset": asset, "problems": check.problems},
            )
        # only now is the source released
        source.unlink()
        remuxed[asset] = {
            "output": output_name,
            "samples": result.samples,
            "source_bytes": result.mdat_size,
            "mdat_sha256": result.mdat_sha256,
            "dropped_tail_au": result.dropped_tail_au,
            "truncated_bytes": result.truncated_bytes,
        }
        bytes_done += remuxed[asset]["source_bytes"]
        _touch_recovery(cfg, operation, phase="verify", asset=asset, remuxed=remuxed, bytes_done=bytes_done)

    # The power cut can leave a stream shorter than its frame index (the collector
    # died mid-frame). The truthful pair count is the minimum over the indexes and
    # the access units the streams actually carry; publishing the index as-is would
    # claim frames that no video holds.
    natural = {asset: info["samples"] for asset, info in remuxed.items()}
    short = {
        asset: count for asset, count in natural.items()
        if count < pairs or count != min(natural.values())
    }
    pairs = min([pairs, *[info["samples"] for info in remuxed.values()]])
    if pairs <= 0:
        raise ControllerError(
            "INCOMPLETE_GATE_FAILED",
            "the selected streams carry no complete access units",
            {"samples": natural},
        )
    if short:
        warnings.append("STREAM_LENGTH_MISMATCH")
    _truncate_index(directory / "ir_frames.jsonl", pairs)
    _truncate_index(directory / "rgb_frames.jsonl", pairs)
    _touch_recovery(cfg, operation, phase="reconcile", pairs=pairs, stream_samples=natural)

    # move the small sidecar files into the staging directory; the sources of the
    # assets that were NOT selected stay behind for incomplete-delete
    for name in (
        "ir_frames.jsonl",
        "rgb_frames.jsonl",
        "stm32_packets.jsonl",
        SESSION_CONFIG,
        "ir-left-gstreamer.log",
        "ir-right-gstreamer.log",
        "rgb-gstreamer.log",
    ):
        path = directory / name
        if path.is_file():
            os.replace(path, staging / name)
    payload = directory / f"stm32.bin{ORPHAN_SUFFIX}"
    if payload.is_file():
        packets = stm32["packets"]
        if packets > 0:
            os.truncate(payload, packets * STM32_PACKET_BYTES)
        os.replace(payload, staging / "stm32.bin")
    _touch_recovery(cfg, operation, phase="staging", bytes_done=bytes_done)

    # the staging directory becomes the publishable session only here; unselected
    # .partial sources stay behind in the orphan directory for incomplete-delete
    if directory.exists():
        marker = directory / UNSEALED_MARKER
        if marker.exists() and not any(path != marker for path in directory.iterdir()):
            marker.unlink()
        if not any(directory.iterdir()):
            directory.rmdir()
    publish_dir = cfg.incoming / session
    if publish_dir.exists():
        raise ControllerError("INCOMPLETE_ALREADY_PUBLISHED", "a sealed session directory already exists")
    os.replace(staging, publish_dir)
    _fsync_directory(cfg.incoming)
    return _publish_recovered(
        cfg, operation, session, publish_dir,
        counts=counts, stm32=stm32, remuxed=remuxed, selection=selection,
        warnings=warnings,
        publish_dir=publish_dir, write_manifest=True, leftover_dir=orphan_dir,
        pairs_override=pairs,
    )


def _quality_status(stm32: dict, pairs: int, counts: dict) -> str:
    rate = stm32.get("observed_rate_hz") or 0.0
    packets = stm32.get("packets") or 0
    if pairs <= 0 or packets <= 0 or stm32.get("first_rx_monotonic_ns") is None:
        return "FAILED"
    if not 395.0 <= float(rate) <= 405.0:
        return "FAILED"
    if counts.get("ir_frames") != counts.get("rgb_input_frames"):
        return "DEGRADED"
    return "DEGRADED"  # a rescued session is never a verified capture


def _truncate_index(path: Path, rows: int) -> None:
    """Keep only the first ``rows`` index rows so the index matches the video."""
    if not path.is_file() or rows <= 0:
        return
    summary = _index_summary(path)
    if summary["rows"] <= rows or summary["torn_tail"] is False and summary["rows"] == 0:
        return
    with path.open("r+b") as stream:
        kept = 0
        offset = 0
        while kept < rows:
            line = stream.readline()
            if not line.endswith(b"\n"):
                break
            offset = stream.tell()
            kept += 1
        stream.truncate(offset)
        stream.flush()
        os.fsync(stream.fileno())


def _sealed_counts(directory: Path) -> dict:
    manifest = read_json(directory / "manifest.json") or {}
    counts = manifest.get("counts")
    return counts if isinstance(counts, dict) else {}


def _publish_recovered(
    cfg: Config,
    operation: dict,
    session: str,
    directory: Path,
    *,
    counts: dict,
    stm32: dict,
    remuxed: dict,
    selection: str,
    warnings: list,
    publish_dir: Path,
    write_manifest: bool,
    leftover_dir: Path | None = None,
    pairs_override: int | None = None,
) -> dict:
    from umi_publish import publish_session

    pairs = min(counts["ir_frames"], counts["rgb_input_frames"])
    if pairs_override is not None:
        pairs = pairs_override
    if selection == "sealed":
        manifest_counts = _sealed_counts(directory)
        pairs = int(manifest_counts.get("ir_frames") or pairs)
        stm32_packets = int(manifest_counts.get("stm32_packets") or 0)
        if stm32_packets:
            stm32["packets"] = stm32_packets
    if pairs <= 0 or (stm32.get("packets") or 0) <= 0:
        raise ControllerError("INCOMPLETE_GATE_FAILED", "recovered counters are not usable")
    session_config = read_json(directory / SESSION_CONFIG) or {}
    profile = session_config.get("profile")
    device = session_config.get("device")
    if not isinstance(profile, dict) or not isinstance(device, dict):
        raise ControllerError("INCOMPLETE_STATE_INVALID", "session_config.json is missing or invalid")
    duration_s = float(_recovery_span(stm32, pairs))
    recovered_manifest = {
        "schema": session_config.get("schema"),
        "status": "SEALED",
        "session_id": session,
        "warnings": list(warnings),
        "profile": profile,
        "device": device,
        "counts": {
            "ir_frames": pairs,
            "rgb_input_frames": pairs,
            "stm32_packets": int(stm32.get("packets") or 0),
        },
        "metrics": {
            "formal_start_monotonic_ns": stm32.get("first_rx_monotonic_ns"),
            "formal_stop_monotonic_ns": stm32.get("last_rx_monotonic_ns"),
            "formal_host_span_s": duration_s,
            "termination": {
                "mode": "until_signal",
                "reason": "interrupted_capture_recovery",
                "captured_frames": pairs,
                "stop_observed_monotonic_ns": None,
                "storage_guard": None,
            },
            "ir": {
                "frames": pairs,
                "writer": {
                    "frames": pairs,
                    "queue_overflows": 0,
                    "streams": {
                        key: {
                            "encoding": "h265_mp4_remux",
                            "codec": "h265",
                            "frames": info["samples"],
                            "compressed_bytes": info["source_bytes"],
                        }
                        for key, info in (
                            ("left", remuxed.get("infrared_left")),
                            ("right", remuxed.get("infrared_right")),
                        )
                        if info
                    },
                },
            },
            "rgb": {
                "input_frames": pairs,
                "encoded_bytes": (remuxed.get("rgb") or {}).get("source_bytes", 0),
                "pts_regressions": 0,
            },
            "stm32": stm32,
            "recovery": {
                "schema_version": 1,
                "tool": "umi_remux/1",
                "recovered_at": now_iso(),
                "selection": selection,
                "assets": remuxed,
                "index_counts": counts,
                "unobserved_counters": list(STM32_ZERO_METRICS),
                "crc_counters_observable": False,
            },
        },
        "files": {},
    }
    for path in sorted(publish_dir.iterdir()):
        if path.is_file() and path.name not in {"manifest.json", "MANIFEST.sha256", UNSEALED_MARKER}:
            recovered_manifest["files"][path.name] = {
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
    if write_manifest:
        _fsync_directory(publish_dir)
        atomic_json(publish_dir / "manifest.json", recovered_manifest)
    quality = _quality_status(stm32, pairs, counts)
    _touch_recovery(cfg, operation, phase="seal", quality_status=quality)

    recording_id = f"recording_{session}"
    _clear_rolled_back_ledger(cfg, recording_id)
    staged_session = publish_dir
    _touch_recovery(cfg, operation, phase="publish")
    job_id = "umi-" + uuid.uuid4().hex
    streams = [
        {"camera": ASSET_CAMERA[asset], "segments": [ASSET_FILES[asset][1]]}
        for asset in VIDEO_ASSETS
        if (staged_session / ASSET_FILES[asset][1]).exists()
    ]
    try:
        publication = publish_session(
            session=staged_session,
            recording_root=cfg.recording_root,
            catalog_db_path=cfg.catalog_db,
            device_id=cfg.device_id,
            job_id=job_id,
            request_id=operation["request_id"],
            boot_id=cfg.boot_id,
            recovery_hint=(
                f"RECOVERED_PARTIAL selection={selection} warnings={','.join(warnings) or 'NONE'}"
            ),
            display_name=f"{_recorded_at_label(session)} 已抢救（{_selection_label(selection)}）",
            streams=streams or None,
            extra_metadata={
                "recovery": recovered_manifest["metrics"]["recovery"],
                "warnings": list(warnings),
                "quality_status": quality,
            },
            quality_status=quality,
            enforce_completion_gate=False,
        )
    except Exception as error:
        if isinstance(error, ControllerError):
            raise
        raise ControllerError("RECOVERY_FAILED", f"publishing the recovered session failed: {error}") from error
    leftover = _leftover_bytes(leftover_dir if leftover_dir is not None else publish_dir)
    job_state = {
        "schema_version": 1,
        "device_id": cfg.device_id,
        "boot_id": cfg.boot_id,
        "job_id": job_id,
        "request_id": operation["request_id"],
        "duration": 0,
        "state": "complete_local",
        "phase": "done",
        "mode": "STEREO_IMU",
        "stereo_pairs": publication["stereo_pairs"],
        "imu_samples": publication["imu_samples"],
        "imu_runtime_state": "ok",
        "imu_quality_status": quality,
        "imu_quality_error_codes": list(warnings),
        "local_data_present": True,
        "recording_id": publication["recording_id"],
        "output_dir": publication["output_dir"],
        "manifest_sha256": publication["manifest_sha256"],
        "publication_ledger": publication["ledger"],
        "recovery_reason": "INCOMPLETE_RECOVERY",
        "created_at": now_iso(),
        "last_heartbeat_at": now_iso(),
    }
    _save_job_and_current_if_selected(cfg, job_state)
    if leftover_dir is not None and leftover_dir.exists():
        if operation.get("delete_remainder"):
            _delete_orphan_tree(leftover_dir, cfg.incoming)
            leftover = 0
        elif not any(leftover_dir.iterdir()):
            leftover_dir.rmdir()
    return {
        "state": "complete",
        "recording_id": publication["recording_id"],
        "output_dir": publication["output_dir"],
        "manifest_sha256": publication["manifest_sha256"],
        "quality_status": quality,
        "warnings": list(warnings),
        "leftover_bytes": leftover,
        "stereo_pairs": publication["stereo_pairs"],
        "imu_samples": publication["imu_samples"],
    }


def _recovery_span(stm32: dict, pairs: int) -> float:
    first = stm32.get("first_rx_monotonic_ns")
    last = stm32.get("last_rx_monotonic_ns")
    if isinstance(first, int) and isinstance(last, int) and last > first:
        return (last - first) / 1e9
    return pairs / 30.0


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _recorded_at_label(session: str) -> str:
    match = re.search(r"-(\d{8}T\d{6}Z)-", session)
    return match.group(1) if match else session


def _selection_label(selection: str) -> str:
    return {"all": "全部", "rgb": "仅彩色", "ir": "仅左右红外", "sealed": "已封存数据"}.get(
        selection, selection
    )


def _staging_leftover_entry(cfg: Config, session: str, directory: Path) -> dict:
    total = _leftover_bytes(directory)
    return {
        "session": session,
        "kind": "staging_leftover",
        "directory": str(directory.relative_to(cfg.recording_root)),
        "total_bytes": total,
        "asset_bytes": {name: 0 for name in VIDEO_ASSETS},
        "imu_bytes": 0,
        "index_bytes": 0,
        "assets_present": {name: False for name in VIDEO_ASSETS},
        "usable": {name: False for name in VIDEO_ASSETS},
        "probe": {name: {"annexb": False, "vps": False, "sps": False, "pps": False, "idr": False}
                  for name in VIDEO_ASSETS},
        "pairs": 0,
        "estimated_duration_s": 0.0,
        "space": {f"required_{selection}": 0 for selection in ASSET_SELECTIONS},
        "recoverable": False,
    }


def _leftover_bytes(directory: Path) -> int:
    if not directory.exists():
        return 0
    total = 0
    for path in directory.rglob("*"):
        if path.is_file() and not path.is_symlink():
            total += path.stat().st_size
    return total


def _clear_rolled_back_ledger(cfg: Config, recording_id: str) -> None:
    """A previous failed publish leaves ROLLED_BACK; nothing else may exist."""
    ledger_path = cfg.recording_root / "recordings-v2" / ".publication-ledger" / f"{recording_id}.json"
    ledger = read_json(ledger_path)
    if not isinstance(ledger, dict) or ledger.get("state") != "ROLLED_BACK":
        return
    prepared = cfg.recording_root / "recordings-v2" / "completed" / f".{recording_id}.publishing"
    final = cfg.recording_root / "recordings-v2" / "completed" / recording_id
    if prepared.exists() or final.exists():
        return
    ledger_path.unlink()


def envelope(cfg: Config | None, command: str, data: dict | None = None, error: dict | None = None) -> dict:
    return {
        "ok": error is None,
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "device_id": cfg.device_id if cfg else None,
        "boot_id": cfg.boot_id if cfg else None,
        "server_time": now_iso(),
        "server_unix_ns": time.time_ns(),
        "error": error,
        "data": data or {},
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="recorderctl")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("identity", "preflight", "preview-start", "preview-status", "preview-stop"):
        commands.add_parser(name).add_argument("--json", action="store_true")
    start_cmd = commands.add_parser("start")
    start_cmd.add_argument("--json", action="store_true")
    start_cmd.add_argument("--request-id", required=True)
    start_cmd.add_argument("--duration", type=int, default=0)
    status_cmd = commands.add_parser("status")
    status_cmd.add_argument("--json", action="store_true")
    status_cmd.add_argument("--job-id")
    stop_cmd = commands.add_parser("stop")
    stop_cmd.add_argument("--json", action="store_true")
    stop_cmd.add_argument("--job-id", required=True)
    logs_cmd = commands.add_parser("logs")
    logs_cmd.add_argument("--json", action="store_true")
    logs_cmd.add_argument("--follow", action="store_true")
    logs_cmd.add_argument("--job-id", required=True)
    logs_cmd.add_argument("--after", type=int, default=0)
    logs_cmd.add_argument("--limit", type=int, default=100)
    delete_cmd = commands.add_parser("delete-recording")
    delete_cmd.add_argument("--json", action="store_true")
    delete_cmd.add_argument("--recording-id", required=True)
    delete_cmd.add_argument("--request-id", required=True)
    delete_status_cmd = commands.add_parser("delete-status")
    delete_status_cmd.add_argument("--json", action="store_true")
    delete_status_cmd.add_argument("--recording-id")
    incomplete_list_cmd = commands.add_parser("incomplete-list")
    incomplete_list_cmd.add_argument("--json", action="store_true")
    incomplete_status_cmd = commands.add_parser("incomplete-status")
    incomplete_status_cmd.add_argument("--json", action="store_true")
    incomplete_status_cmd.add_argument("--session")
    recover_cmd = commands.add_parser("incomplete-recover")
    recover_cmd.add_argument("--json", action="store_true")
    recover_cmd.add_argument("--session", required=True)
    recover_cmd.add_argument("--request-id", required=True)
    recover_cmd.add_argument("--assets", default="all", choices=list(ASSET_SELECTIONS))
    recover_cmd.add_argument("--delete-remainder", action="store_true")
    recover_cmd.add_argument("--dry-run", action="store_true")
    incomplete_delete_cmd = commands.add_parser("incomplete-delete")
    incomplete_delete_cmd.add_argument("--json", action="store_true")
    incomplete_delete_cmd.add_argument("--session", required=True)
    incomplete_delete_cmd.add_argument("--request-id", required=True)
    worker = commands.add_parser("_run")
    worker.add_argument("--job-id", required=True)
    delete_worker = commands.add_parser("_delete")
    delete_worker.add_argument("--recording-id", required=True)
    recover_worker = commands.add_parser("_recover")
    recover_worker.add_argument("--session", required=True)
    incomplete_delete_worker = commands.add_parser("_incomplete-delete")
    incomplete_delete_worker.add_argument("--session", required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    cfg = None
    command = "unknown"
    try:
        args = parser().parse_args(argv)
        command = args.command
        cfg = Config()
        if command == "_run":
            return run_worker(cfg, args.job_id)
        if command == "_delete":
            return run_delete_worker(cfg, args.recording_id)
        if command == "_recover":
            return run_recovery_worker(cfg, args.session)
        if command == "_incomplete-delete":
            return run_incomplete_delete_worker(cfg, args.session)
        if command == "identity":
            data = identity(cfg)
        elif command == "preflight":
            data = preflight(cfg)
        elif command == "start":
            data = start(cfg, args.request_id, args.duration)
        elif command == "status":
            data = status(cfg, args.job_id)
        elif command == "stop":
            data = stop(cfg, args.job_id)
        elif command == "logs":
            if args.follow:
                raise ControllerError("UNSUPPORTED", "follow logs are not enabled for the UMI adapter")
            data = logs(cfg, args.job_id, args.after, args.limit)
        elif command == "delete-recording":
            data = start_delete_recording(cfg, args.recording_id, args.request_id)
        elif command == "delete-status":
            data = delete_status(cfg, args.recording_id)
        elif command == "incomplete-list":
            data = open_incomplete_list(cfg)
        elif command == "incomplete-status":
            data = incomplete_status(cfg, args.session)
        elif command == "incomplete-recover":
            data = start_incomplete_recover(
                cfg, args.session, args.request_id, args.assets,
                delete_remainder=args.delete_remainder, dry_run=args.dry_run,
            )
        elif command == "incomplete-delete":
            data = start_incomplete_delete(cfg, args.session, args.request_id)
        elif command == "preview-start":
            health = preview_health()
            if health is None:
                raise ControllerError("PREVIEW_START_FAILED", "preview service is unavailable")
            data = {"accepted": True, "idempotent": True, "service": "umi-preview", "health": health}
        elif command == "preview-status":
            health = preview_health()
            data = {"active": health is not None, "service": "umi-preview", "health": health}
        elif command == "preview-stop":
            data = {"released": True, "service": "umi-preview"}
        else:
            raise ControllerError("INVALID_ARGUMENT", "unknown command")
        print(json.dumps(envelope(cfg, command, data=data), ensure_ascii=False, separators=(",", ":")), flush=True)
        return 0
    except ControllerError as error:
        print(json.dumps(envelope(cfg, command, error={"code": error.code, "message": error.message}, data=error.data), ensure_ascii=False, separators=(",", ":")), flush=True)
        return 1
    except Exception as error:
        print(json.dumps(envelope(cfg, command, error={"code": "INTERNAL_ERROR", "message": str(error)}), ensure_ascii=False, separators=(",", ":")), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
