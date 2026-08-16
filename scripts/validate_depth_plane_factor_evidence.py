#!/usr/bin/env python3
"""Validate that a depth-plane postprocessor only applies its declared Z factor."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

from validate_slam_product_release import validate_plane_factor_safety


POSE_COLUMNS = ("t_sec", "x", "y", "z", "qw", "qx", "qy", "qz")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_rows(path: Path) -> list[dict[str, float]]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not set(POSE_COLUMNS).issubset(reader.fieldnames):
            raise ValueError(f"trajectory lacks required pose columns: {path}")
        rows = []
        for line_number, row in enumerate(reader, start=2):
            try:
                values = {name: float(row[name]) for name in POSE_COLUMNS}
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid pose at {path}:{line_number}") from exc
            if not all(math.isfinite(value) for value in values.values()):
                raise ValueError(f"non-finite pose at {path}:{line_number}")
            rows.append(values)
    if len(rows) < 2:
        raise ValueError(f"trajectory needs at least two poses: {path}")
    return rows


def validate_factor_evidence(
    analysis_report: Path, raw_trajectory: Path, corrected_trajectory: Path
) -> dict:
    analysis = json.loads(analysis_report.read_text(encoding="utf-8"))
    factor = analysis.get("plane_factor")
    failures = []
    if not isinstance(factor, dict):
        failures.append("analysis report lacks plane_factor")
        factor = {}
    else:
        failures.extend(validate_plane_factor_safety(factor, "depth_plane"))

    raw = load_rows(raw_trajectory)
    corrected = load_rows(corrected_trajectory)
    if len(raw) != len(corrected):
        failures.append("raw and corrected trajectory lengths differ")
        sample_count = min(len(raw), len(corrected))
    else:
        sample_count = len(raw)

    z_deltas = []
    immutable_fields = ("t_sec", "x", "y", "qw", "qx", "qy", "qz")
    for index, (before, after) in enumerate(zip(raw, corrected)):
        if any(before[name] != after[name] for name in immutable_fields):
            failures.append(f"sample {index}: non-Z pose field changed")
            break
        z_deltas.append(after["z"] - before["z"])
    measured_max = max((abs(value) for value in z_deltas), default=0.0)
    declared_max = factor.get("applied_correction_max_abs_m", 0.0)
    if not isinstance(declared_max, (int, float)) or not math.isclose(
        measured_max, float(declared_max), rel_tol=0.0, abs_tol=1e-9
    ):
        failures.append("measured Z correction does not match factor report")
    if factor.get("status") == "DISABLED" and measured_max != 0.0:
        failures.append("disabled factor changed the trajectory")
    maximum = factor.get("max_correction_m")
    if factor.get("status") == "ACTIVE" and (
        not isinstance(maximum, (int, float))
        or measured_max > float(maximum) + 1e-12
    ):
        failures.append("measured Z correction exceeds factor bound")

    return {
        "result": "PASS" if not failures else "FAIL",
        "scope": "depth_plane_factor_safety_evidence",
        "truth_usage": "none",
        "analysis_report": str(analysis_report.resolve()),
        "analysis_report_sha256": sha256_file(analysis_report),
        "raw_trajectory": str(raw_trajectory.resolve()),
        "raw_trajectory_sha256": sha256_file(raw_trajectory),
        "corrected_trajectory": str(corrected_trajectory.resolve()),
        "corrected_trajectory_sha256": sha256_file(corrected_trajectory),
        "sample_count": sample_count,
        "measured_correction_max_abs_m": measured_max,
        "plane_factor": factor,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="验收Depth平面因子的因果有界Z修正")
    parser.add_argument("--analysis-report", type=Path, required=True)
    parser.add_argument("--raw-trajectory", type=Path, required=True)
    parser.add_argument("--corrected-trajectory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = validate_factor_evidence(
        args.analysis_report, args.raw_trajectory, args.corrected_trajectory
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["result"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
