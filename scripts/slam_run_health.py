#!/usr/bin/env python3
"""Deterministically classify one SLAM run without external ground truth."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

from slam_benchmark_environment import validate_environment_report


SCHEMA_VERSION = 2
MIN_POSE_COVERAGE = 0.98
MIN_TRUE_ELEVATION_RETENTION = 0.90
MIN_ABSOLUTE_JUMP_LIMIT_M = 0.03
RAW_STEP_MULTIPLIER = 3.5


def _number(value: object) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def trajectory_diagnostics_from_csv(path: Path) -> dict:
    """Recompute the health-critical trajectory facts from a sealed CSV."""
    points: list[tuple[float, float, float]] = []
    previous_timestamp: float | None = None
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not {"t_sec", "x", "y", "z"}.issubset(
            reader.fieldnames
        ):
            raise ValueError(f"trajectory lacks t_sec/x/y/z columns: {path}")
        for line_number, row in enumerate(reader, start=2):
            try:
                timestamp = float(row["t_sec"])
                point = tuple(float(row[axis]) for axis in ("x", "y", "z"))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"invalid trajectory point at {path}:{line_number}"
                ) from exc
            if not math.isfinite(timestamp) or not all(
                math.isfinite(value) for value in point
            ):
                raise ValueError(
                    f"non-finite trajectory point at {path}:{line_number}"
                )
            if previous_timestamp is not None and timestamp <= previous_timestamp:
                raise ValueError(
                    f"non-increasing trajectory timestamp at {path}:{line_number}"
                )
            previous_timestamp = timestamp
            points.append(point)

    if len(points) < 2:
        diagnostics = {
            "max_step_m": None,
            "z_span_m": None,
            "endpoint_delta_m": None,
        }
    else:
        steps = [
            math.dist(previous, current)
            for previous, current in zip(points, points[1:])
        ]
        z_values = [point[2] for point in points]
        diagnostics = {
            "max_step_m": max(steps),
            "z_span_m": max(z_values) - min(z_values),
            "endpoint_delta_m": math.dist(points[0], points[-1]),
        }
    return {"sample_count": len(points), "diagnostics": diagnostics}


def evaluate_slam_health(report: dict) -> dict:
    environment_failures = validate_environment_report(
        report.get("benchmark_environment", {})
    )
    failure_scope = report.get("failure_scope")
    if failure_scope == "INFRASTRUCTURE" or environment_failures:
        return {
            "schema_version": SCHEMA_VERSION,
            "state": "INFRASTRUCTURE_BLOCKED",
            "product_usable": False,
            "thresholds": {
                "min_pose_coverage": MIN_POSE_COVERAGE,
                "min_true_elevation_retention": MIN_TRUE_ELEVATION_RETENTION,
                "min_absolute_jump_limit_m": MIN_ABSOLUTE_JUMP_LIMIT_M,
                "raw_step_multiplier": RAW_STEP_MULTIPLIER,
            },
            "checks": [],
            "failures": [
                *(environment_failures or []),
                *(
                    ["run classified as infrastructure failure"]
                    if failure_scope == "INFRASTRUCTURE"
                    else []
                ),
            ],
        }

    checks = []

    def add(name: str, passed: bool, measured: object, threshold: object) -> None:
        checks.append(
            {
                "name": name,
                "result": "PASS" if passed else "FAIL",
                "measured": measured,
                "threshold": threshold,
            }
        )

    add(
        "declared_run_result",
        report.get("result") == "PASS",
        report.get("result"),
        "PASS",
    )
    add(
        "failure_scope",
        failure_scope == "SLAM",
        failure_scope,
        "SLAM",
    )
    declared_failures = report.get("failures")
    add(
        "declared_failures_empty",
        isinstance(declared_failures, list) and not declared_failures,
        declared_failures,
        [],
    )
    runtime_error = report.get("runtime_error")
    add("runtime_complete", runtime_error in (None, ""), runtime_error, None)

    raw_samples = _positive_int(report.get("raw_odometry_samples"))
    corrected_samples = _positive_int(report.get("corrected_odometry_samples"))
    expected_samples = _positive_int(report.get("expected_pose_samples_after_skip"))
    sample_counts_valid = all(
        value is not None for value in (raw_samples, corrected_samples, expected_samples)
    )
    add(
        "trajectory_sample_counts",
        sample_counts_valid,
        {
            "raw": raw_samples,
            "corrected": corrected_samples,
            "expected": expected_samples,
        },
        "all positive integers",
    )
    pose_coverage = _number(report.get("pose_coverage"))
    calculated_coverage = (
        min(raw_samples, corrected_samples) / expected_samples
        if sample_counts_valid
        else None
    )
    add(
        "pose_coverage_consistency",
        pose_coverage is not None
        and calculated_coverage is not None
        and math.isclose(
            pose_coverage, calculated_coverage, rel_tol=0.0, abs_tol=1e-12
        ),
        {"reported": pose_coverage, "calculated": calculated_coverage},
        {"absolute_tolerance": 1e-12},
    )
    add(
        "pose_coverage",
        pose_coverage is not None and pose_coverage >= MIN_POSE_COVERAGE,
        pose_coverage,
        {"minimum": MIN_POSE_COVERAGE},
    )
    for name in (
        "loop_input_drop_events",
        "estimator_keyframe_queue_drop_events",
    ):
        value = report.get(name)
        add(name, isinstance(value, int) and value == 0, value, {"maximum": 0})

    rejected_optimizations = report.get("pose_graph_health", {}).get(
        "rejected_optimizations"
    )
    add(
        "pose_graph_rejected_optimizations",
        isinstance(rejected_optimizations, int) and rejected_optimizations == 0,
        rejected_optimizations,
        {"maximum": 0},
    )

    raw_diagnostics = report.get("raw_trajectory_diagnostics", {})
    corrected_diagnostics = report.get("corrected_trajectory_diagnostics", {})
    raw_max_step = _number(raw_diagnostics.get("max_step_m"))
    corrected_max_step = _number(corrected_diagnostics.get("max_step_m"))
    jump_limit = (
        max(MIN_ABSOLUTE_JUMP_LIMIT_M, RAW_STEP_MULTIPLIER * raw_max_step)
        if raw_max_step is not None
        else None
    )
    add(
        "corrected_trajectory_jump",
        jump_limit is not None
        and corrected_max_step is not None
        and corrected_max_step <= jump_limit,
        corrected_max_step,
        {"maximum_m": jump_limit},
    )

    raw_z_span = _number(raw_diagnostics.get("z_span_m"))
    z_retention = _number(report.get("z_span_retention_ratio"))
    if raw_z_span is not None and raw_z_span >= 0.10:
        add(
            "true_elevation_retention",
            z_retention is not None
            and z_retention >= MIN_TRUE_ELEVATION_RETENTION,
            z_retention,
            {"minimum": MIN_TRUE_ELEVATION_RETENTION},
        )
    else:
        checks.append(
            {
                "name": "true_elevation_retention",
                "result": "SKIPPED",
                "measured": z_retention,
                "threshold": {"raw_z_span_activation_m": 0.10},
            }
        )

    failures = [check["name"] for check in checks if check["result"] == "FAIL"]
    healthy = not failures
    return {
        "schema_version": SCHEMA_VERSION,
        "state": "SLAM_HEALTHY" if healthy else "SLAM_FAILED",
        "product_usable": healthy,
        "thresholds": {
            "min_pose_coverage": MIN_POSE_COVERAGE,
            "min_true_elevation_retention": MIN_TRUE_ELEVATION_RETENTION,
            "min_absolute_jump_limit_m": MIN_ABSOLUTE_JUMP_LIMIT_M,
            "raw_step_multiplier": RAW_STEP_MULTIPLIER,
        },
        "checks": checks,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="SLAM运行健康状态复算")
    parser.add_argument("--run-report", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = json.loads(args.run_report.read_text(encoding="utf-8"))
    health = evaluate_slam_health(report)
    text = json.dumps(health, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    if health["state"] == "SLAM_HEALTHY":
        return 0
    return 4 if health["state"] == "INFRASTRUCTURE_BLOCKED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
