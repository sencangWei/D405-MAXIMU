#!/usr/bin/env python3
"""EGO recorderctl compatibility layer for the native RK3576 UMI collector."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.request import Request, urlopen

from umi_publish import publish_session, recover_pending_publications


SCHEMA_VERSION = 1
CONTROLLER_VERSION = "0.2.4-umi"
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
ACTIVE = {"starting", "recording", "stop_requested", "finalizing"}
FINAL = {"complete_local", "incomplete", "interrupted"}


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
        self.incoming = self.recording_root / "incoming"

    def prepare(self) -> None:
        for path in (self.state_dir, self.jobs, self.requests, self.incoming):
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
    worker = commands.add_parser("_run")
    worker.add_argument("--job-id", required=True)
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
