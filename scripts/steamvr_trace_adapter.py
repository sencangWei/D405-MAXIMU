#!/usr/bin/env python3
"""Convert official OpenVR pose probe CSV into the tracker.csv handeye schema."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation


DEFAULT_SERIAL = "LHR-A2A59C7D"
TRACKING_RESULT_RUNNING_OK = 200
ROTATION_TOLERANCE = 1.0e-3
MAX_CLOCK_PAIR_DRIFT_NS = 1_000_000
MAX_GOOD_POSE_GAP_NS = 30_000_000
MIN_SAMPLE_GOOD_FRACTION = 0.999

RAW_FIELDS = {
    "sequence",
    "query_monotonic_begin_ns",
    "query_monotonic_end_ns",
    "host_realtime_ns",
    "index",
    "connected",
    "pose_valid",
    "tracking_result",
    "m00",
    "m01",
    "m02",
    "px",
    "m10",
    "m11",
    "m12",
    "py",
    "m20",
    "m21",
    "m22",
    "pz",
    "vx",
    "vy",
    "vz",
    "wx",
    "wy",
    "wz",
}

OUTPUT_FIELDS = [
    "host_monotonic_ns",
    "host_realtime_ns",
    "device_time_s",
    "name",
    "serial",
    "px_m",
    "py_m",
    "pz_m",
    "qw",
    "qx",
    "qy",
    "qz",
    "source",
    "timestamp_source",
    "pose_frame",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes"}


def _parse_int(row: dict[str, str], key: str) -> int:
    return int(row[key])


def _parse_float(row: dict[str, str], key: str) -> float:
    return float(row[key])


def _read_raw(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise ValueError(f"empty CSV header: {path}")
        missing = sorted(RAW_FIELDS - set(reader.fieldnames))
        if missing:
            raise ValueError(f"raw CSV missing columns: {missing}")
        return list(reader), list(reader.fieldnames)


def _rotation_quality(matrix: np.ndarray) -> tuple[bool, float, float]:
    orth_error = float(np.max(np.abs(matrix.T @ matrix - np.eye(3))))
    det = float(np.linalg.det(matrix))
    ok = orth_error <= ROTATION_TOLERANCE and abs(det - 1.0) <= ROTATION_TOLERANCE
    return ok, orth_error, det


def _row_is_finite(row: dict[str, str]) -> bool:
    numeric_keys = (
        "query_monotonic_begin_ns",
        "query_monotonic_end_ns",
        "host_realtime_ns",
        "m00",
        "m01",
        "m02",
        "px",
        "m10",
        "m11",
        "m12",
        "py",
        "m20",
        "m21",
        "m22",
        "pz",
    )
    try:
        return all(math.isfinite(float(row[key])) for key in numeric_keys)
    except ValueError:
        return False


def _raw_timing(rows: list[dict[str, str]]) -> dict[str, Any]:
    sequences = np.asarray([_parse_int(row, "sequence") for row in rows], dtype=np.int64)
    begins = np.asarray(
        [_parse_int(row, "query_monotonic_begin_ns") for row in rows], dtype=np.int64
    )
    ends = np.asarray(
        [_parse_int(row, "query_monotonic_end_ns") for row in rows], dtype=np.int64
    )
    midpoints = begins + ((ends - begins) // 2)
    realtime_midpoints = np.asarray(
        [
            _parse_int(row, "host_realtime_ns") + int(midpoint - begin)
            for row, begin, midpoint in zip(rows, begins, midpoints)
        ],
        dtype=np.int64,
    )
    clock_offsets = realtime_midpoints - midpoints
    return {
        "sequences": sequences,
        "begins": begins,
        "ends": ends,
        "midpoints": midpoints,
        "realtime_midpoints": realtime_midpoints,
        "clock_pair_drift_ns": int(clock_offsets.max() - clock_offsets.min())
        if len(clock_offsets)
        else 0,
    }


def _validate_raw_timing(timing: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if len(timing["sequences"]) < 2:
        failures.append("raw_row_count_below_2")
    if np.any(timing["begins"] > timing["ends"]):
        failures.append("query_begin_after_end")
    if np.any(np.diff(timing["sequences"]) <= 0):
        failures.append("sequence_not_strictly_increasing")
    if np.any(np.diff(timing["midpoints"]) <= 0):
        failures.append("query_midpoint_not_strictly_increasing")
    if timing["clock_pair_drift_ns"] > MAX_CLOCK_PAIR_DRIFT_NS:
        failures.append("clock_pair_drift_gt_1ms")
    return failures


def _exportable_rows(
    rows: list[dict[str, str]], timing: dict[str, Any], serial: str
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    output_rows: list[dict[str, str]] = []
    rejected = Counter()
    max_orth_error = 0.0
    max_det_error = 0.0

    for i, row in enumerate(rows):
        if not _truthy(row["connected"]):
            rejected["not_connected"] += 1
            continue
        if not _truthy(row["pose_valid"]):
            rejected["pose_invalid"] += 1
            continue
        if _parse_int(row, "tracking_result") != TRACKING_RESULT_RUNNING_OK:
            rejected["tracking_result_not_200"] += 1
            continue
        if not _row_is_finite(row):
            rejected["nonfinite_numeric_field"] += 1
            continue

        matrix = np.asarray(
            [
                [_parse_float(row, "m00"), _parse_float(row, "m01"), _parse_float(row, "m02")],
                [_parse_float(row, "m10"), _parse_float(row, "m11"), _parse_float(row, "m12")],
                [_parse_float(row, "m20"), _parse_float(row, "m21"), _parse_float(row, "m22")],
            ],
            dtype=float,
        )
        rotation_ok, orth_error, det = _rotation_quality(matrix)
        max_orth_error = max(max_orth_error, orth_error)
        max_det_error = max(max_det_error, abs(det - 1.0))
        if not rotation_ok:
            rejected["bad_rotation_matrix"] += 1
            continue

        qx, qy, qz, qw = Rotation.from_matrix(matrix).as_quat()
        output_rows.append(
            {
                "host_monotonic_ns": str(int(timing["midpoints"][i])),
                "host_realtime_ns": str(int(timing["realtime_midpoints"][i])),
                "device_time_s": "",
                "name": "WM0",
                "serial": serial,
                "px_m": row["px"],
                "py_m": row["py"],
                "pz_m": row["pz"],
                "qw": repr(float(qw)),
                "qx": repr(float(qx)),
                "qy": repr(float(qy)),
                "qz": repr(float(qz)),
                "source": "steamvr_official",
                "timestamp_source": "host_query_midpoint_not_sensor",
                "pose_frame": "steamvr_standing_tracker",
            }
        )

    diagnostics = {
        "input_rows": len(rows),
        "exported_rows": len(output_rows),
        "rejected_counts": dict(sorted(rejected.items())),
        "sample_good_fraction": (len(output_rows) / len(rows)) if rows else 0.0,
        "max_rotation_orthogonality_error": max_orth_error,
        "max_rotation_det_error": max_det_error,
    }
    return output_rows, diagnostics


def _validate_export(output_rows: list[dict[str, str]], diagnostics: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if len(output_rows) < 2:
        failures.append("good_pose_count_below_2")
    if diagnostics["sample_good_fraction"] < MIN_SAMPLE_GOOD_FRACTION:
        failures.append("sample_good_fraction_below_99_9_percent")
    if len(output_rows) >= 2:
        good_times = np.asarray(
            [int(row["host_monotonic_ns"]) for row in output_rows], dtype=np.int64
        )
        max_gap_ns = int(np.diff(good_times).max())
        diagnostics["good_pose_max_gap_ns"] = max_gap_ns
        if max_gap_ns > MAX_GOOD_POSE_GAP_NS:
            failures.append("good_pose_max_gap_gt_30ms")
    else:
        diagnostics["good_pose_max_gap_ns"] = None
    return failures


def _write_tracker(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _write_report(path: Path, report: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")


def adapt_trace(
    raw: Path | str,
    output: Path | str,
    report: Path | str,
    serial: str = DEFAULT_SERIAL,
) -> dict[str, Any]:
    """Adapt an official OpenVR raw CSV into handeye tracker.csv format.

    Existing output or report paths are never overwritten.  On validation
    failure the report is written, but the tracker output is not created.
    """

    raw_path = Path(raw)
    output_path = Path(output)
    report_path = Path(report)
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {output_path}")
    if report_path.exists():
        raise FileExistsError(f"refusing to overwrite existing report: {report_path}")

    rows, raw_columns = _read_raw(raw_path)
    timing = _raw_timing(rows)
    output_rows, diagnostics = _exportable_rows(rows, timing, serial)
    failures = _validate_raw_timing(timing)
    failures.extend(_validate_export(output_rows, diagnostics))

    status = "PASS" if not failures else "FAIL"
    report_doc: dict[str, Any] = {
        "status": status,
        "result": status,
        "failures": failures,
        "provenance": {
            "adapter": "steamvr_trace_adapter.py",
            "raw_csv": str(raw_path.resolve()),
            "output_csv": str(output_path.resolve()),
            "serial": serial,
            "name_alias": "WM0",
            "source": "steamvr_official",
            "timestamp_source": "host_query_midpoint_not_sensor",
            "pose_frame": "steamvr_standing_tracker",
            "caveat": "OpenVR exposes host query timing here, not tracker sensor hardware time; device_time_s is intentionally empty.",
            "raw_columns": raw_columns,
        },
        "hashes": {
            "input_sha256": sha256_file(raw_path),
            "output_sha256": None,
        },
        "validation": {
            **diagnostics,
            "sequence_strictly_increasing": bool(np.all(np.diff(timing["sequences"]) > 0))
            if len(timing["sequences"]) >= 2
            else False,
            "query_midpoint_strictly_increasing": bool(
                np.all(np.diff(timing["midpoints"]) > 0)
            )
            if len(timing["midpoints"]) >= 2
            else False,
            "query_begin_le_end": bool(np.all(timing["begins"] <= timing["ends"]))
            if len(timing["begins"])
            else False,
            "clock_pair_drift_ns": timing["clock_pair_drift_ns"],
            "thresholds": {
                "sample_good_fraction_min": MIN_SAMPLE_GOOD_FRACTION,
                "good_pose_max_gap_ns_max": MAX_GOOD_POSE_GAP_NS,
                "clock_pair_drift_ns_max": MAX_CLOCK_PAIR_DRIFT_NS,
                "rotation_tolerance": ROTATION_TOLERANCE,
            },
        },
    }

    if failures:
        _write_report(report_path, report_doc)
        return report_doc

    _write_tracker(output_path, output_rows)
    report_doc["hashes"]["output_sha256"] = sha256_file(output_path)
    _write_report(report_path, report_doc)
    return report_doc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Adapt official SteamVR/OpenVR raw pose CSV into tracker.csv."
    )
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--serial", default=DEFAULT_SERIAL)
    args = parser.parse_args(argv)

    try:
        result = adapt_trace(args.raw, args.output, args.report, serial=args.serial)
    except Exception as exc:
        print(f"steamvr_trace_adapter: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
