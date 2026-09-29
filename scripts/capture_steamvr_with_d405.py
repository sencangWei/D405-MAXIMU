#!/usr/bin/env python3
"""Record raw D405/400 Hz IMU and official SteamVR poses, without libsurvive.

This prepares independent calibration data, not calibrated ground truth.
Host pose-query timestamps and camera exposure timestamps remain distinct.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import tempfile
import time
from datetime import datetime

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "reports/steamvr_reference_setup_20260927/steamvr_pose_probe"
RUNNER = Path("/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/umi-device2-d405.sh")
DATA_ROOT = Path("/home/robot/umi_ego_vio_data_device2_c48df736/recordings")
IMAGE = "umi-ego-vio:device2-c48df736-d405-lighthouse-calibration-v1-20260908"
SERIAL = "LHR-A2A59C7D"
LIGHTHOUSE_DB = Path("/home/robot/.steam/debian-installation/config/lighthouse/lighthousedb.json")
BASE_SERIALS = {int(serial, 16) for serial in ("6E49384F", "9D67E4BD")}


def snapshot_lighthouse_world(database: Path, output: Path) -> dict:
    """Freeze the official SteamVR two-base geometry with each acquisition."""
    raw = database.read_bytes()
    world = json.loads(raw)
    matches = []
    for universe in world.get("known_universes", []):
        stations = {station.get("base_serial_number"): station
                    for station in universe.get("base_stations", [])}
        if BASE_SERIALS <= stations.keys():
            poses = {str(serial): stations[serial]["target_pose"]["pose"]
                     for serial in BASE_SERIALS}
            if all(len(pose) == 7 and all(isinstance(value, (int, float)) and
                   math.isfinite(value) for value in pose) for pose in poses.values()):
                matches.append((universe["id"], poses))
    if len(matches) != 1:
        raise ValueError("expected Lighthouse bases in exactly one valid SteamVR universe")
    with output.open("xb") as target:
        target.write(raw)
    universe_id, poses = matches[0]
    return {"snapshot": str(output), "sha256": hashlib.sha256(raw).hexdigest(),
            "revision": world.get("revision"), "universe_id": universe_id,
            "base_poses": poses}


def same_lighthouse_geometry(before: dict, after: dict) -> bool:
    return before["universe_id"] == after["universe_id"] and before["base_poses"] == after["base_poses"]


def capture_completion_status(world_unchanged: bool, require_unchanged_world: bool) -> str:
    if world_unchanged:
        return "PASS_CAPTURE_ONLY_NOT_CALIBRATED"
    if not require_unchanged_world:
        return "PASS_CAPTURE_ONLY_WORLD_CHANGED"
    raise RuntimeError("SteamVR Lighthouse geometry changed during acquisition; reference unusable")


def camera_deadline(now: float, duration: float, formal_started: bool) -> float:
    """Give Docker startup and formal acquisition independent timeout budgets."""
    return now + 180 + (duration if formal_started else 0)


def preserve_staged_trace(staged: Path, target: Path) -> None:
    """Persist a private RAM-staged trace before removing its temporary copy."""
    with staged.open("rb") as source, target.open("xb") as destination:
        shutil.copyfileobj(source, destination)
    if hashlib.sha256(staged.read_bytes()).digest() != hashlib.sha256(target.read_bytes()).digest():
        raise RuntimeError(f"Raw trace copy mismatch; RAM original preserved at {staged}")
    # Only the exact freshly-created mkstemp file, never a directory/glob.
    if staged.parent != Path("/dev/shm") or not staged.name.startswith("umi_steamvr_pose_") or staged.is_symlink():
        raise RuntimeError(f"Unexpected staging target; preserved at {staged}")
    staged.unlink()


def conflicting_readers(process_table: str) -> list[str]:
    """Match executable basenames, including Python-launched readers."""
    names = {"survive-cli", "vive_pose_stream", "vive_pose_stream.py"}
    matches = []
    for line in process_table.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) != 3 or fields[0] == str(os.getpid()):
            continue
        _, comm, args = fields
        # ps displays argv, not shell syntax; unrelated unmatched quotes must
        # not abort preflight. These reader executable paths have no spaces.
        tokens = args.split()
        executables = tokens[:1]
        if tokens and Path(tokens[0]).name.startswith("python"):
            executables += tokens[1:2]
        if comm in names or any(Path(token).name in names for token in executables):
            matches.append(line.strip())
    return matches


def stop_child(process: subprocess.Popen | None) -> None:
    if process is not None and process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def overlap_report(tracker: Path, frames: Path) -> dict:
    with tracker.open() as fp:
        times = np.array([int(r["host_monotonic_ns"]) * 1e-9 for r in csv.DictReader(fp)])
    with frames.open() as fp:
        rows = list(csv.DictReader(fp))
    if not rows or not all(r.get("sensor_event_mono") for r in rows):
        raise ValueError("D405 authoritative sensor_event_mono timestamps missing")
    events = np.array([float(r["sensor_event_mono"]) for r in rows])
    if not np.isfinite(events).all() or np.any(np.diff(events) <= 0):
        raise ValueError("D405 exposure event timestamps invalid or not increasing")
    if len(times) < 2 or np.any(np.diff(times) <= 0):
        raise ValueError("Tracker query timestamps invalid")
    right = np.searchsorted(times, events, side="right")
    covered = (right > 0) & (right < len(times))
    idx = right[covered]
    supported = np.zeros(len(events), dtype=bool)
    supported[covered] = (times[idx] - times[idx - 1]) <= 0.030
    ratio = float(supported.mean())
    return {"status": "PASS_CLOCK_COVERAGE_ONLY" if ratio >= 0.98 else "FAIL_CLOCK_COVERAGE",
            "camera_frames": len(events), "supported_frames": int(supported.sum()),
            "ratio": ratio, "minimum_ratio": 0.98, "max_bracketing_gap_ms": 30,
            "alignment_domain": "host_monotonic", "dynamic_time_offset": "NOT_ESTIMATED"}


def camera_window_trace(raw: Path, frames: Path, output: Path) -> dict:
    """Select by exposure interval only, retaining bracketing query rows.

    Startup/DB3 analysis are outside calibration acquisition. The full raw
    trace remains intact; no in-window invalid states or gaps are removed.
    """
    with frames.open() as fp:
        events = [float(r["sensor_event_mono"]) for r in csv.DictReader(fp)]
    if len(events) < 2 or not np.isfinite(events).all() or np.any(np.diff(events) <= 0):
        raise ValueError("Camera exposure interval invalid")
    with raw.open() as fp:
        reader = csv.DictReader(fp)
        fields, rows = reader.fieldnames, list(reader)
    times = np.array([(int(r["query_monotonic_begin_ns"]) +
                       int(r["query_monotonic_end_ns"])) // 2 for r in rows], dtype=np.int64)
    if len(times) < 2 or np.any(np.diff(times) <= 0):
        raise ValueError("Raw Tracker timestamps not increasing")
    lo = int(np.searchsorted(times, int(events[0] * 1e9), side="right")) - 1
    hi = int(np.searchsorted(times, int(events[-1] * 1e9), side="left")) + 1
    if lo < 0 or hi > len(rows):
        raise ValueError("Tracker does not bracket camera acquisition")
    with output.open("x", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows[lo:hi])
    return {"selection": "camera_exposure_interval_plus_bracketing_queries",
            "full_raw_rows": len(rows), "selected_rows": hi - lo,
            "pre_acquisition_rows": lo, "post_acquisition_rows": len(rows) - hi,
            "full_raw_preserved": str(raw), "selected_raw": str(output)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=40)
    parser.add_argument("--label", default="steamvr_calibration")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--guided", action="store_true")
    parser.add_argument("--require-unchanged-world", action="store_true",
                        help="Fail acquisition if SteamVR changes its base geometry")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not 5 <= args.duration <= 60 or not re.fullmatch(r"[A-Za-z0-9_-]+", args.label):
        parser.error("duration must be 5–60 seconds and label alphanumeric/-/_")
    if not os.access(PROBE, os.X_OK) or not os.access(RUNNER, os.X_OK):
        parser.error("Official probe or camera-only runner unavailable; see setup README")
    command = [str(RUNNER), "capture", str(args.duration)]
    if args.dry_run:
        print(json.dumps({"camera_command": command, "image": IMAGE,
                          "tracker_command": [str(PROBE), SERIAL, str(args.duration + 150)],
                          "starts_libsurvive": False, "calibration": "NOT_APPLIED"}, indent=2))
        return 0
    if subprocess.run(["pgrep", "-x", "vrserver"], stdout=subprocess.DEVNULL).returncode != 0:
        parser.error("SteamVR vrserver is not running")
    process_table = subprocess.run(["ps", "-eo", "pid=,comm=,args="],
                                   text=True, capture_output=True, check=True).stdout
    conflicts = conflicting_readers(process_table)
    if conflicts:
        parser.error("libsurvive reader running; do not share Tracker USB with SteamVR: " + "; ".join(conflicts))
    out = args.output or ROOT / "reports/steamvr_umi_sessions" / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + args.label)
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    print(f"输出目录: {out}", flush=True)
    tracker_process = camera_process = guide_process = None
    staged_raw = None
    handles = []
    manifest = {"schema": "d405_steamvr_capture_v1", "reference_backend": "steamvr_official",
                "reference_pose_frame": "steamvr_standing_tracker", "serial": SERIAL,
                "slam_supervision": False, "extrinsic": "NOT_VALIDATED",
                "time_offset": "NOT_ESTIMATED", "duration_requested_s": args.duration}
    try:
        raw = out / "tracker_openvr_raw.csv"
        # Direct disk writes measured up to ~82ms during DB3 processing. Keep
        # query-path output on private tmpfs, then hash-check the durable copy.
        fd, staged_name = tempfile.mkstemp(prefix="umi_steamvr_pose_", suffix=".csv", dir="/dev/shm")
        staged_raw = Path(staged_name)
        handles.append(os.fdopen(fd, "w"))
        for path in (out / "tracker.log", out / "d405_capture.log"):
            handles.append(path.open("x"))
        tracker_process = subprocess.Popen([str(PROBE), SERIAL, str(args.duration + 150)],
            stdout=handles[0], stderr=handles[1], start_new_session=True)
        deadline = time.monotonic() + 10
        while True:
            if tracker_process.poll() is not None:
                raise RuntimeError("Official Tracker reader exited before camera start")
            with staged_raw.open() as fp:
                ready = any(r.get("connected") == "1" and r.get("pose_valid") == "1" and
                            r.get("tracking_result") == "200" for r in csv.DictReader(fp))
            if ready:
                break
            if time.monotonic() > deadline:
                raise RuntimeError("No valid official Tracker pose within 10 seconds")
            time.sleep(0.1)
        env = dict(os.environ, UMI_DEVICE2_D405_IMAGE=IMAGE,
                   UMI_CAPTURE_PREVIEW="1" if args.preview else "0")
        camera_process = subprocess.Popen(command, env=env, stdout=handles[2],
                                         stderr=subprocess.STDOUT, start_new_session=True)
        # Container startup is separate from the formal camera acquisition.
        # A slow Docker launch must not consume the recording/cleanup budget.
        deadline = camera_deadline(time.monotonic(), args.duration, formal_started=False)
        announced = False
        while camera_process.poll() is None:
            log = (out / "d405_capture.log").read_text(errors="replace")
            if not announced and "[全流采集] 正式采集" in log:
                manifest["steamvr_world_start"] = snapshot_lighthouse_world(
                    LIGHTHOUSE_DB, out / "lighthousedb_at_start.json")
                print(f"正式采集开始：{args.duration:g} 秒", flush=True)
                announced = True
                deadline = camera_deadline(time.monotonic(), args.duration, formal_started=True)
                if args.guided:
                    guide_process = subprocess.Popen(["python3", str(ROOT / "scripts/aprilgrid_motion_prompt.py"),
                        "--duration", str(args.duration)], start_new_session=True)
            if time.monotonic() > deadline:
                raise RuntimeError("D405 capture timeout; partial files preserved")
            time.sleep(0.1)
        if not announced:
            raise RuntimeError("D405 never announced formal capture start")
        manifest["steamvr_world_end"] = snapshot_lighthouse_world(
            LIGHTHOUSE_DB, out / "lighthousedb_at_end.json")
        manifest["steamvr_world_unchanged"] = same_lighthouse_geometry(
            manifest["steamvr_world_start"], manifest["steamvr_world_end"])
        completion_status = capture_completion_status(
            manifest["steamvr_world_unchanged"], args.require_unchanged_world)
        if not manifest["steamvr_world_unchanged"]:
            manifest["reference_quality"] = "REVIEW_REQUIRED_WORLD_GEOMETRY_CHANGE"
            print("基站关系已变化：继续验收原始采集；精度参考需单独检查 Tracker 轨迹", flush=True)
        stop_child(guide_process)
        stop_child(tracker_process)
        for fp in handles:
            fp.close()
        handles.clear()
        preserve_staged_trace(staged_raw, raw)
        staged_raw = None
        manifest["raw_storage"] = "private_tmpfs_then_sha256_verified_disk_copy"
        log = (out / "d405_capture.log").read_text(errors="replace")
        sessions = re.findall(r"^\[全流采集\] 输出目录: (.+)$", log, re.MULTILINE)
        if not sessions:
            raise RuntimeError("Camera output directory absent from log")
        session = DATA_ROOT / Path(sessions[-1].strip()).name
        manifest["d405_session"] = str(session)
        (out / "d405_session.txt").write_text(str(session) + "\n")
        if camera_process.returncode != 0:
            raise RuntimeError(f"Camera recorder returned {camera_process.returncode}; data retained")
        acceptance = json.loads((session / "acceptance.json").read_text())
        if acceptance.get("result") != "PASS":
            raise RuntimeError("D405 raw recording quality not PASS")
        selected = out / "tracker_camera_window_raw.csv"
        manifest["tracker_acquisition_window"] = camera_window_trace(raw, session / "d405_frames.csv", selected)
        from steamvr_trace_adapter import adapt_trace
        adapter = adapt_trace(selected, out / "tracker.csv", out / "tracker_integrity.json", serial=SERIAL)
        manifest["tracker_integrity"] = adapter
        if tracker_process.returncode != 0 or adapter["status"] != "PASS":
            raise RuntimeError("Official Tracker trace failed integrity; raw data retained")
        overlap = overlap_report(out / "tracker.csv", session / "d405_frames.csv")
        manifest["clock_coverage"] = overlap
        if overlap["status"] != "PASS_CLOCK_COVERAGE_ONLY":
            raise RuntimeError("Tracker query coverage of camera exposures insufficient")
        manifest["status"] = completion_status
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        manifest.update(status="FAIL_CAPTURE", error=str(error))
        print(f"采集验收失败：{error}", flush=True)
        return 1
    except KeyboardInterrupt:
        manifest.update(status="FAIL_CAPTURE_INTERRUPTED", error="Operator interrupted capture")
        return 130
    finally:
        for process in (guide_process, camera_process, tracker_process):
            stop_child(process)
        for fp in handles:
            fp.close()
        if staged_raw is not None:
            try:
                preserve_staged_trace(staged_raw, out / "tracker_openvr_raw.csv")
            except (OSError, RuntimeError) as error:
                manifest.update(status="FAIL_RAW_PRESERVATION", raw_staging_path=str(staged_raw),
                                raw_preservation_error=str(error))
                print(f"原始位姿暂存仍保留：{staged_raw}，错误：{error}", flush=True)
        (out / "capture_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"采集结束，验收记录：{out / 'capture_manifest.json'}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
