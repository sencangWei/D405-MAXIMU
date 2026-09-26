#!/usr/bin/env python3
"""Capture and validate a Vive Tracker pose stream for SLAM reference use."""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import re
import selectors
import statistics
import subprocess
import time
from pathlib import Path


STREAM = Path("/home/robot/.local/bin/vive_pose_stream")
MAX_TRANSLATION_SPEED_M_S = 2.0
MIN_TRANSLATION_JUMP_M = 0.010
MAX_ANGULAR_SPEED_DEG_S = 1500.0
MIN_ANGULAR_JUMP_DEG = 15.0
MIN_D405_TRACKER_OVERLAP_RATIO = 0.98
MAX_TRACKER_HOST_INTERVAL_MS = 100.0
MAX_TRACKER_INTERPOLATION_GAP_MS = 30.0
MAX_LIGHTHOUSE_POSE_TRANSLATION_CHANGE_MM = 0.1
MAX_LIGHTHOUSE_POSE_ROTATION_CHANGE_DEG = 0.05
MAX_LIGHTHOUSE_ACCEL_DIRECTION_CHANGE_DEG = 0.5


def _parse_config_array(block: str, key: str, expected_size: int) -> tuple[float, ...]:
    match = re.search(rf'"{re.escape(key)}":\[(.*?)\]', block)
    if match is None:
        raise ValueError(f"missing {key} in Lighthouse config")
    values = tuple(float(value.strip().strip('"')) for value in match.group(1).split(","))
    if len(values) != expected_size:
        raise ValueError(
            f"invalid {key} size in Lighthouse config: {len(values)} != {expected_size}"
        )
    return values


def _parse_config_scalar(block: str, key: str) -> str:
    match = re.search(rf'"{re.escape(key)}":"([^"]*)"', block)
    if match is None:
        raise ValueError(f"missing {key} in Lighthouse config")
    return match.group(1)


def parse_lighthouse_config(text: str) -> dict[int, dict[str, object]]:
    states: dict[int, dict[str, object]] = {}
    for match in re.finditer(r'"lighthouse(\d+)":\{(.*?)\n\}', text, re.DOTALL):
        index = int(match.group(1))
        block = match.group(2)
        states[index] = {
            "id": _parse_config_scalar(block, "id"),
            "pose": _parse_config_array(block, "pose", 7),
            "accel": _parse_config_array(block, "accel", 3),
            "position_set": _parse_config_scalar(block, "PositionSet") == "1",
        }
    if not states:
        raise ValueError("no Lighthouse state found in config")
    return states


def _quaternion_angle_deg(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    norm_a = math.sqrt(sum(value * value for value in a))
    norm_b = math.sqrt(sum(value * value for value in b))
    if norm_a <= 1e-12 or norm_b <= 1e-12:
        return math.inf
    dot = abs(sum(x * y for x, y in zip(a, b)) / (norm_a * norm_b))
    return math.degrees(2.0 * math.acos(min(1.0, dot)))


def _vector_angle_deg(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    norm_a = math.sqrt(sum(value * value for value in a))
    norm_b = math.sqrt(sum(value * value for value in b))
    if norm_a <= 1e-12 or norm_b <= 1e-12:
        return math.inf
    dot = sum(x * y for x, y in zip(a, b)) / (norm_a * norm_b)
    return math.degrees(math.acos(max(-1.0, min(1.0, dot))))


def audit_lighthouse_config(before_text: str, after_path: Path) -> dict[str, object]:
    before = parse_lighthouse_config(before_text)
    after = parse_lighthouse_config(after_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    lighthouse_reports = []
    if set(before) != set(after):
        failures.append("lighthouse_set_changed_during_capture")
    for index in sorted(set(before) & set(after)):
        initial = before[index]
        final = after[index]
        initial_pose = initial["pose"]
        final_pose = final["pose"]
        translation_change_mm = math.dist(initial_pose[:3], final_pose[:3]) * 1000.0
        rotation_change_deg = _quaternion_angle_deg(initial_pose[3:], final_pose[3:])
        accel_direction_change_deg = _vector_angle_deg(initial["accel"], final["accel"])
        id_changed = initial["id"] != final["id"]
        position_lost = bool(initial["position_set"]) and not bool(final["position_set"])
        if id_changed:
            failures.append("lighthouse_id_changed_during_capture")
        if position_lost:
            failures.append("lighthouse_position_lost_during_capture")
        if (
            translation_change_mm > MAX_LIGHTHOUSE_POSE_TRANSLATION_CHANGE_MM
            or rotation_change_deg > MAX_LIGHTHOUSE_POSE_ROTATION_CHANGE_DEG
        ):
            failures.append("lighthouse_pose_changed_during_capture")
        if accel_direction_change_deg > MAX_LIGHTHOUSE_ACCEL_DIRECTION_CHANGE_DEG:
            failures.append("lighthouse_ootx_up_changed_during_capture")
        lighthouse_reports.append(
            {
                "index": index,
                "id_before": initial["id"],
                "id_after": final["id"],
                "position_set_before": initial["position_set"],
                "position_set_after": final["position_set"],
                "translation_change_mm": translation_change_mm,
                "rotation_change_deg": rotation_change_deg,
                "accel_direction_change_deg": accel_direction_change_deg,
            }
        )
    failures = list(dict.fromkeys(failures))
    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "thresholds": {
            "max_pose_translation_change_mm": MAX_LIGHTHOUSE_POSE_TRANSLATION_CHANGE_MM,
            "max_pose_rotation_change_deg": MAX_LIGHTHOUSE_POSE_ROTATION_CHANGE_DEG,
            "max_accel_direction_change_deg": MAX_LIGHTHOUSE_ACCEL_DIRECTION_CHANGE_DEG,
        },
        "lighthouses": lighthouse_reports,
    }


def attach_lighthouse_audit(
    report: dict[str, object], audit: dict[str, object] | None
) -> dict[str, object]:
    if audit is None:
        return report
    report["lighthouse_configuration"] = audit
    if audit["status"] == "FAIL":
        report["status"] = "FAIL"
        report["failures"] = list(
            dict.fromkeys([*report.get("failures", []), *audit["failures"]])
        )
    return report


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return math.nan
    pos = (len(ordered) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    return ordered[lo] * (hi - pos) + ordered[hi] * (pos - lo)


def capture(
    duration_s: float,
    output: Path,
    warmup_s: float,
    wall_timeout_s: float | None = None,
    config: Path | None = None,
    lighthouse_gen: int | None = None,
    raw_record: Path | None = None,
) -> dict[str, object] | None:
    if config is not None and not config.is_file():
        raise ValueError(f"Lighthouse config does not exist: {config}")
    if config is not None:
        config = config.resolve()
    config_before = config.read_text(encoding="utf-8") if config is not None else None
    if lighthouse_gen not in (None, 1, 2):
        raise ValueError(f"unsupported Lighthouse generation: {lighthouse_gen}")
    if raw_record is not None:
        raw_record = raw_record.resolve()
        if raw_record.exists():
            raise FileExistsError(f"refusing to overwrite Lighthouse raw record: {raw_record}")
        raw_record.parent.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    log_path = output.with_suffix(".log")
    if wall_timeout_s is None:
        wall_timeout_s = warmup_s + duration_s + 10.0
    with output.open("w", newline="", encoding="utf-8") as csv_file, log_path.open(
        "w", encoding="utf-8"
    ) as log_file:
        command = [str(STREAM)]
        if config is not None:
            command.extend(["-c", str(config)])
        if lighthouse_gen is not None:
            command.extend(["--lighthouse-gen", str(lighthouse_gen)])
        if raw_record is not None:
            command.extend(["--record", str(raw_record)])
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=log_file, text=True, bufsize=1
        )
        assert process.stdout is not None
        warmup_deadline_ns = None
        capture_started_ns = None
        deadline_ns = None
        header = ""
        header_written = False
        tracker_samples = 0
        wall_deadline = time.monotonic() + wall_timeout_s
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            while True:
                remaining_s = wall_deadline - time.monotonic()
                if remaining_s <= 0:
                    if tracker_samples == 0:
                        raise RuntimeError(
                            "No WM0 tracker pose received before timeout; "
                            "check Tracker power, radio link, and pairing"
                        )
                    raise RuntimeError(
                        f"WM0 tracker stream stopped before capture completed "
                        f"({tracker_samples} samples)"
                    )
                if not selector.select(timeout=min(1.0, remaining_s)):
                    if process.poll() is not None:
                        raise RuntimeError(
                            f"Tracker pose stream exited early with code {process.returncode}"
                        )
                    continue
                line = process.stdout.readline()
                if not line:
                    raise RuntimeError(
                        f"Tracker pose stream ended early with code {process.poll()}"
                    )
                if line.startswith("host_monotonic_ns,"):
                    header = line
                    continue
                fields = line.rstrip().split(",")
                if len(fields) < 5 or fields[3] != "WM0":
                    continue
                tracker_samples += 1
                event_ns = int(fields[0])
                if warmup_deadline_ns is None:
                    warmup_deadline_ns = event_ns + int(warmup_s * 1e9)
                if event_ns < warmup_deadline_ns:
                    continue
                if capture_started_ns is None:
                    capture_started_ns = event_ns
                    deadline_ns = capture_started_ns + int(duration_s * 1e9)
                if not header_written:
                    csv_file.write(header)
                    header_written = True
                csv_file.write(line)
                csv_file.flush()
                if event_ns >= deadline_ns:
                    break
        finally:
            selector.close()
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
    if config is None or config_before is None:
        return None
    return audit_lighthouse_config(config_before, config)


def load_tracker_rows(path: Path) -> list[dict[str, float | int | str]]:
    rows: list[dict[str, float | int | str]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("name") != "WM0":
                continue
            rows.append(
                {
                    "host_ns": int(row["host_monotonic_ns"]),
                    "host_realtime_ns": (
                        int(row["host_realtime_ns"])
                        if row.get("host_realtime_ns")
                        else None
                    ),
                    "device_s": float(row["device_time_s"]),
                    "serial": row["serial"],
                    "x": float(row["px_m"]),
                    "y": float(row["py_m"]),
                    "z": float(row["pz_m"]),
                    "qw": float(row["qw"]),
                    "qx": float(row["qx"]),
                    "qy": float(row["qy"]),
                    "qz": float(row["qz"]),
                }
            )
    return rows


def normalized_mean_quaternion(rows: list[dict[str, float | int | str]]) -> tuple[float, ...]:
    reference = tuple(float(rows[0][key]) for key in ("qw", "qx", "qy", "qz"))
    total = [0.0, 0.0, 0.0, 0.0]
    for row in rows:
        q = [float(row[key]) for key in ("qw", "qx", "qy", "qz")]
        if sum(a * b for a, b in zip(q, reference)) < 0:
            q = [-value for value in q]
        for index, value in enumerate(q):
            total[index] += value
    norm = math.sqrt(sum(value * value for value in total))
    return tuple(value / norm for value in total)


def summarize(path: Path) -> dict[str, object]:
    rows = load_tracker_rows(path)
    if len(rows) < 2:
        raise RuntimeError(f"Insufficient WM0 samples in {path}: {len(rows)}")

    host_s = [int(row["host_ns"]) * 1e-9 for row in rows]
    host_realtime_s = [
        int(row["host_realtime_ns"]) * 1e-9
        for row in rows
        if row["host_realtime_ns"] is not None
    ]
    device_s = [float(row["device_s"]) for row in rows]
    duration_s = host_s[-1] - host_s[0]
    intervals_ms = [(host_s[i] - host_s[i - 1]) * 1000 for i in range(1, len(rows))]
    xyz = [[float(row[key]) for row in rows] for key in ("x", "y", "z")]
    center = [statistics.median(axis) for axis in xyz]
    radial_mm = [
        math.sqrt(sum((float(row[key]) - center[i]) ** 2 for i, key in enumerate(("x", "y", "z"))))
        * 1000
        for row in rows
    ]

    mean_q = normalized_mean_quaternion(rows)
    angular_deg = []
    for row in rows:
        q = tuple(float(row[key]) for key in ("qw", "qx", "qy", "qz"))
        dot = min(1.0, abs(sum(a * b for a, b in zip(q, mean_q))))
        angular_deg.append(math.degrees(2 * math.acos(dot)))

    edge_count = max(1, min(len(rows) // 4, int(5 * (len(rows) - 1) / duration_s)))
    first_center = [statistics.mean(axis[:edge_count]) for axis in xyz]
    last_center = [statistics.mean(axis[-edge_count:]) for axis in xyz]
    endpoint_drift_mm = math.dist(first_center, last_center) * 1000

    metrics = {
        "sample_count": len(rows),
        "serials": sorted({str(row["serial"]) for row in rows}),
        "duration_s": duration_s,
        "rate_hz": (len(rows) - 1) / duration_s,
        "device_timestamp_regressions": sum(
            device_s[i] <= device_s[i - 1] for i in range(1, len(device_s))
        ),
        "host_realtime_timestamp_regressions": sum(
            host_realtime_s[i] <= host_realtime_s[i - 1]
            for i in range(1, len(host_realtime_s))
        ),
        "interval_ms": {
            "median": statistics.median(intervals_ms),
            "p95": percentile(intervals_ms, 0.95),
            "p99": percentile(intervals_ms, 0.99),
            "max": max(intervals_ms),
            "over_25ms_count": sum(value > 25 for value in intervals_ms),
        },
        "position_center_m": center,
        "position_std_mm_xyz": [statistics.pstdev(axis) * 1000 for axis in xyz],
        "position_peak_to_peak_mm_xyz": [(max(axis) - min(axis)) * 1000 for axis in xyz],
        "radial_error_mm": {
            "median": statistics.median(radial_mm),
            "p95": percentile(radial_mm, 0.95),
            "p99": percentile(radial_mm, 0.99),
            "max": max(radial_mm),
        },
        "orientation_error_deg": {
            "median": statistics.median(angular_deg),
            "p95": percentile(angular_deg, 0.95),
            "max": max(angular_deg),
        },
        "endpoint_drift_mm": endpoint_drift_mm,
    }
    failures = []
    if metrics["rate_hz"] < 100:
        failures.append("rate_below_100_hz")
    if metrics["device_timestamp_regressions"]:
        failures.append("device_timestamp_regression")
    if metrics["host_realtime_timestamp_regressions"]:
        failures.append("host_realtime_timestamp_regression")
    if metrics["radial_error_mm"]["p95"] > 1.0:
        failures.append("static_radial_p95_over_1mm")
    if metrics["radial_error_mm"]["max"] > 2.0:
        failures.append("static_radial_max_over_2mm")
    if metrics["orientation_error_deg"]["p95"] > 0.2:
        failures.append("orientation_p95_over_0.2deg")
    return {"status": "PASS" if not failures else "FAIL", "failures": failures, "metrics": metrics}


def restart_test(runs: int, duration_s: float, warmup_s: float, output_dir: Path) -> dict[str, object]:
    reports = []
    for index in range(1, runs + 1):
        path = output_dir / f"restart_{index:02d}.csv"
        capture(duration_s, path, warmup_s)
        reports.append(summarize(path))

    centers = [report["metrics"]["position_center_m"] for report in reports]
    reference = [statistics.median([center[axis] for center in centers]) for axis in range(3)]
    errors_mm = [math.dist(center, reference) * 1000 for center in centers]
    max_error_mm = max(errors_mm)
    failures = [] if max_error_mm <= 2.0 else ["restart_center_max_over_2mm"]
    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "runs": runs,
        "duration_per_run_s": duration_s,
        "warmup_per_run_s": warmup_s,
        "reference_center_m": reference,
        "run_center_error_mm": errors_mm,
        "median_error_mm": statistics.median(errors_mm),
        "p95_error_mm": percentile(errors_mm, 0.95),
        "max_error_mm": max_error_mm,
        "run_reports": reports,
    }


def stream_integrity(path: Path) -> dict[str, object]:
    """Report dynamic-capture integrity without applying static-pose thresholds."""
    rows = load_tracker_rows(path)
    if len(rows) < 2:
        raise RuntimeError(f"Insufficient WM0 samples in {path}: {len(rows)}")
    host_ns = [int(row["host_ns"]) for row in rows]
    device_s = [float(row["device_s"]) for row in rows]
    realtime_samples = [
        (index, int(row["host_realtime_ns"]))
        for index, row in enumerate(rows)
        if row["host_realtime_ns"] is not None
    ]
    realtime_ns = [value for _, value in realtime_samples]
    duration_s = (host_ns[-1] - host_ns[0]) * 1e-9
    rate_hz = (len(rows) - 1) / duration_s if duration_s > 0.0 else 0.0
    intervals_ms = [
        (host_ns[index] - host_ns[index - 1]) * 1e-6
        for index in range(1, len(host_ns))
    ]
    positions = [
        tuple(float(row[key]) for key in ("x", "y", "z")) for row in rows
    ]
    quaternions = [
        tuple(float(row[key]) for key in ("qw", "qx", "qy", "qz"))
        for row in rows
    ]
    translation_speeds_m_s = []
    translation_steps_m = []
    angular_speeds_deg_s = []
    angular_steps_deg = []
    invalid_quaternion_count = 0
    for index in range(1, len(rows)):
        delta_s = (host_ns[index] - host_ns[index - 1]) * 1e-9
        if delta_s <= 0.0:
            continue
        translation_step_m = math.dist(positions[index], positions[index - 1])
        translation_steps_m.append(translation_step_m)
        translation_speeds_m_s.append(translation_step_m / delta_s)
        previous = quaternions[index - 1]
        current = quaternions[index]
        previous_norm = math.sqrt(sum(value * value for value in previous))
        current_norm = math.sqrt(sum(value * value for value in current))
        if previous_norm <= 1e-12 or current_norm <= 1e-12:
            invalid_quaternion_count += 1
            continue
        dot = abs(
            sum(a * b for a, b in zip(previous, current))
            / (previous_norm * current_norm)
        )
        angle_deg = math.degrees(2.0 * math.acos(min(1.0, dot)))
        angular_steps_deg.append(angle_deg)
        angular_speeds_deg_s.append(angle_deg / delta_s)
    translation_jumps = [
        speed > MAX_TRANSLATION_SPEED_M_S and step > MIN_TRANSLATION_JUMP_M
        for speed, step in zip(translation_speeds_m_s, translation_steps_m)
    ]
    angular_jumps = [
        speed > MAX_ANGULAR_SPEED_DEG_S and step > MIN_ANGULAR_JUMP_DEG
        for speed, step in zip(angular_speeds_deg_s, angular_steps_deg)
    ]
    failures = []
    warnings = []
    if duration_s <= 0.0:
        failures.append("invalid_host_monotonic_duration")
    if rate_hz < 100.0:
        failures.append("rate_below_100_hz")
    if any(host_ns[index] <= host_ns[index - 1] for index in range(1, len(host_ns))):
        failures.append("host_monotonic_timestamp_regression")
    # D405/Tracker alignment is defined in host_monotonic_ns.  libsurvive's
    # device timestamp and CLOCK_REALTIME can both move backwards when the
    # host clock is corrected; rejecting an otherwise continuous monotonic
    # pose stream would discard valid ground truth.
    if any(device_s[index] <= device_s[index - 1] for index in range(1, len(device_s))):
        warnings.append("device_timestamp_regression")
    if realtime_ns and any(
        realtime_ns[index] <= realtime_ns[index - 1]
        for index in range(1, len(realtime_ns))
    ):
        warnings.append("host_realtime_timestamp_regression")
    if sum(interval > MAX_TRACKER_HOST_INTERVAL_MS for interval in intervals_ms):
        failures.append("host_interval_over_100ms")
    if any(translation_jumps):
        failures.append("translation_pose_jump")
    if any(angular_jumps):
        failures.append("angular_pose_jump")
    if invalid_quaternion_count:
        failures.append("invalid_quaternion_norm")
    clock_offsets_ns = [
        realtime - host_ns[index] for index, realtime in realtime_samples
    ]
    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "warnings": warnings,
        "metrics": {
            "sample_count": len(rows),
            "serials": sorted({str(row["serial"]) for row in rows}),
            "duration_s": duration_s,
            "rate_hz": rate_hz,
            "interval_ms": {
                "median": statistics.median(intervals_ms),
                "p95": percentile(intervals_ms, 0.95),
                "max": max(intervals_ms),
                "over_25ms_count": sum(interval > 25.0 for interval in intervals_ms),
                "over_100ms_count": sum(
                    interval > MAX_TRACKER_HOST_INTERVAL_MS
                    for interval in intervals_ms
                ),
            },
            "device_timestamp_regressions": sum(
                device_s[index] <= device_s[index - 1]
                for index in range(1, len(device_s))
            ),
            "host_monotonic_timestamp_regressions": sum(
                host_ns[index] <= host_ns[index - 1]
                for index in range(1, len(host_ns))
            ),
            "host_realtime_timestamp_regressions": sum(
                realtime_ns[index] <= realtime_ns[index - 1]
                for index in range(1, len(realtime_ns))
            ),
            "realtime_minus_monotonic_span_us": (
                (max(clock_offsets_ns) - min(clock_offsets_ns)) / 1000.0
                if clock_offsets_ns
                else None
            ),
            "motion_continuity": {
                "max_translation_speed_m_s": (
                    max(translation_speeds_m_s) if translation_speeds_m_s else None
                ),
                "max_translation_step_mm": (
                    max(translation_steps_m) * 1000.0
                    if translation_steps_m
                    else None
                ),
                "translation_pose_jump_count": sum(translation_jumps),
                "max_angular_speed_deg_s": (
                    max(angular_speeds_deg_s) if angular_speeds_deg_s else None
                ),
                "max_angular_step_deg": (
                    max(angular_steps_deg) if angular_steps_deg else None
                ),
                "angular_pose_jump_count": sum(angular_jumps),
                "invalid_quaternion_count": invalid_quaternion_count,
            },
        },
    }


def capture_overlap(
    tracker_path: Path,
    d405_frames_path: Path,
    minimum_ratio: float = MIN_D405_TRACKER_OVERLAP_RATIO,
) -> dict[str, object]:
    tracker_rows = load_tracker_rows(tracker_path)
    if len(tracker_rows) < 2:
        raise RuntimeError(f"Insufficient WM0 samples in {tracker_path}: {len(tracker_rows)}")
    with d405_frames_path.open(newline="", encoding="utf-8") as stream:
        frame_rows = list(csv.DictReader(stream))
    d405_times = []
    for row in frame_rows:
        value = (
            row.get("infrared_left_mono")
            or row.get("sensor_event_mono")
            or row.get("arrival_mono")
        )
        if value:
            d405_times.append(float(value))
    if len(d405_times) < 2:
        raise RuntimeError(
            f"Insufficient D405 monotonic timestamps in {d405_frames_path}: "
            f"{len(d405_times)}"
        )
    tracker_times = [int(row["host_ns"]) * 1e-9 for row in tracker_rows]
    d405_start, d405_end = d405_times[0], d405_times[-1]
    tracker_start, tracker_end = tracker_times[0], tracker_times[-1]
    d405_duration = d405_end - d405_start
    if d405_duration <= 0.0:
        raise RuntimeError(f"Invalid D405 timestamp range in {d405_frames_path}")
    overlap_start = max(d405_start, tracker_start)
    overlap_end = min(d405_end, tracker_end)
    overlap_s = max(0.0, overlap_end - overlap_start)
    overlap_ratio = overlap_s / d405_duration
    supported_queries = 0
    maximum_gap_s = MAX_TRACKER_INTERPOLATION_GAP_MS / 1000.0
    for query_time in d405_times:
        if query_time < tracker_start or query_time > tracker_end:
            continue
        right = bisect.bisect_right(tracker_times, query_time)
        if right > 0 and tracker_times[right - 1] == query_time:
            supported_queries += 1
            continue
        if right == len(tracker_times):
            continue
        if right == 0:
            continue
        if tracker_times[right] - tracker_times[right - 1] <= maximum_gap_s:
            supported_queries += 1
    query_coverage_ratio = supported_queries / len(d405_times)
    failures = []
    if overlap_ratio < minimum_ratio:
        failures.append("d405_tracker_overlap_below_98pct")
    if query_coverage_ratio < minimum_ratio:
        failures.append("d405_tracker_query_coverage_below_98pct")
    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "metrics": {
            "d405_duration_s": d405_duration,
            "tracker_duration_s": tracker_end - tracker_start,
            "overlap_s": overlap_s,
            "overlap_ratio": overlap_ratio,
            "minimum_overlap_ratio": minimum_ratio,
            "query_samples": len(d405_times),
            "supported_query_samples": supported_queries,
            "query_coverage_ratio": query_coverage_ratio,
            "maximum_interpolation_gap_ms": MAX_TRACKER_INTERPOLATION_GAP_MS,
            "tracker_start_minus_d405_start_s": tracker_start - d405_start,
            "tracker_end_minus_d405_end_s": tracker_end - d405_end,
        },
    }


def write_report(report: dict[str, object], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture_parser = subparsers.add_parser("capture")
    capture_parser.add_argument("--duration", type=float, required=True)
    capture_parser.add_argument("--warmup", type=float, default=4.0)
    capture_parser.add_argument("--output", type=Path, required=True)
    capture_parser.add_argument("--report", type=Path, required=True)
    capture_parser.add_argument("--config", type=Path)
    capture_parser.add_argument("--lighthouse-gen", type=int, choices=(1, 2))

    record_parser = subparsers.add_parser(
        "record", help="record a moving tracker and validate only stream integrity"
    )
    record_parser.add_argument("--duration", type=float, required=True)
    record_parser.add_argument("--warmup", type=float, default=2.0)
    record_parser.add_argument("--output", type=Path, required=True)
    record_parser.add_argument("--report", type=Path, required=True)
    record_parser.add_argument("--config", type=Path)
    record_parser.add_argument("--lighthouse-gen", type=int, choices=(1, 2))
    record_parser.add_argument("--raw-record", type=Path, help="save original libsurvive optical/IMU events for offline replay")

    restart_parser = subparsers.add_parser("restart")
    restart_parser.add_argument("--runs", type=int, default=10)
    restart_parser.add_argument("--duration", type=float, default=5.0)
    restart_parser.add_argument("--warmup", type=float, default=4.0)
    restart_parser.add_argument("--output-dir", type=Path, required=True)
    restart_parser.add_argument("--report", type=Path, required=True)

    overlap_parser = subparsers.add_parser(
        "overlap", help="validate that Tracker covers the formal D405 window"
    )
    overlap_parser.add_argument("--tracker", type=Path, required=True)
    overlap_parser.add_argument("--d405-frames", type=Path, required=True)
    overlap_parser.add_argument("--minimum-ratio", type=float, default=0.98)
    overlap_parser.add_argument("--report", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "capture":
        lighthouse_audit = capture(
            args.duration,
            args.output,
            args.warmup,
            config=args.config,
            lighthouse_gen=args.lighthouse_gen,
        )
        report = attach_lighthouse_audit(summarize(args.output), lighthouse_audit)
    elif args.command == "record":
        lighthouse_audit = capture(
            args.duration,
            args.output,
            args.warmup,
            config=args.config,
            lighthouse_gen=args.lighthouse_gen,
            raw_record=args.raw_record,
        )
        report = attach_lighthouse_audit(stream_integrity(args.output), lighthouse_audit)
    elif args.command == "overlap":
        report = capture_overlap(
            args.tracker, args.d405_frames, minimum_ratio=args.minimum_ratio
        )
    else:
        report = restart_test(args.runs, args.duration, args.warmup, args.output_dir)
    write_report(report, args.report)
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
