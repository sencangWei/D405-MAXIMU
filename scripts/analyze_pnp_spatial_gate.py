#!/usr/bin/env python3
"""Select a PnP spatial-support gate without using hidden-test data."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path

from slam_benchmark_environment import validate_environment_report


QUALITY_PATTERN = re.compile(
    r"\[AUTO_LOOP_PNP_QUALITY\] current=(\d+) matched=(\d+) "
    r"inliers=(\d+) rmse_px=([0-9.inf]+) p95_px=([0-9.inf]+) "
    r"current_hull=([0-9.]+) old_hull=([0-9.]+)"
)
GEOMETRY_PATTERN = re.compile(
    r"\[AUTO_LOOP_GEOMETRY_PASS\] current=(\d+) matched=(\d+)"
)
ACCEPT_PATTERN = re.compile(r"\[AUTO_LOOP_ACCEPT\] current=(\d+) matched=(\d+)")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def confirmation_windows(loop_log: str, confirmation_frames: int) -> list[dict]:
    samples = {}
    for match in QUALITY_PATTERN.finditer(loop_log):
        current, matched, inliers = map(int, match.groups()[:3])
        rmse, p95, current_hull, old_hull = map(float, match.groups()[3:])
        samples[(current, matched)] = {
            "current": current,
            "matched": matched,
            "inliers": inliers,
            "rmse_px": rmse,
            "p95_px": p95,
            "current_hull_fraction": current_hull,
            "old_hull_fraction": old_hull,
            "spatial_support": min(current_hull, old_hull),
        }
    geometry = sorted(
        {
            (int(match.group(1)), int(match.group(2)))
            for match in GEOMETRY_PATTERN.finditer(loop_log)
            if (int(match.group(1)), int(match.group(2))) in samples
        }
    )
    accepted = {
        (int(match.group(1)), int(match.group(2)))
        for match in ACCEPT_PATTERN.finditer(loop_log)
    }
    by_matched: dict[int, list[int]] = {}
    for current, matched in geometry:
        by_matched.setdefault(matched, []).append(current)

    windows = []
    for matched, current_frames in by_matched.items():
        current_frames = sorted(set(current_frames))
        for start in range(len(current_frames) - confirmation_frames + 1):
            frames = current_frames[start : start + confirmation_frames]
            if any(right != left + 1 for left, right in zip(frames, frames[1:])):
                continue
            records = [samples[(current, matched)] for current in frames]
            if not all(
                math.isfinite(record["rmse_px"]) and math.isfinite(record["p95_px"])
                for record in records
            ):
                continue
            windows.append(
                {
                    "matched": matched,
                    "current_frames": frames,
                    "minimum_spatial_support": min(
                        record["spatial_support"] for record in records
                    ),
                    "maximum_rmse_px": max(record["rmse_px"] for record in records),
                    "maximum_p95_px": max(record["p95_px"] for record in records),
                    "minimum_inliers": min(record["inliers"] for record in records),
                    "ends_in_accept": (frames[-1], matched) in accepted,
                }
            )
    return windows


def analyze_manifest(manifest_path: Path) -> dict:
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported PnP spatial gate manifest schema")
    confirmation_frames = int(manifest.get("confirmation_frames", 4))
    if confirmation_frames < 1:
        raise ValueError("confirmation_frames must be positive")
    minimum_requirements = {
        "min_positive_runs": 2,
        "min_negative_runs": 2,
        "min_validation_positive_runs": 1,
        "min_validation_negative_runs": 1,
    }
    requested_requirements = manifest.get("requirements", {})
    unknown_requirements = set(requested_requirements) - set(minimum_requirements)
    if unknown_requirements:
        raise ValueError(
            "unsupported evidence requirement: " + sorted(unknown_requirements)[0]
        )
    requirements = {
        name: max(minimum, int(requested_requirements.get(name, minimum)))
        for name, minimum in minimum_requirements.items()
    }
    minimum_support_margin = max(
        0.02, float(manifest.get("minimum_support_margin", 0.02))
    )
    datasets = []
    exploratory_positive = []
    exploratory_negative = []
    qualified_positive = []
    qualified_negative = []

    for entry in manifest.get("datasets", []):
        role = entry.get("role")
        if role == "hidden_test":
            raise ValueError("hidden-test data cannot tune the PnP spatial gate")
        if role not in {"development", "validation"}:
            raise ValueError(f"invalid dataset role: {role}")
        session = entry.get("session")
        if not session:
            raise ValueError(f"{entry.get('id')}: session is required")
        if not isinstance(entry.get("expected_loop"), bool):
            raise ValueError(f"{entry.get('id')}: expected_loop must be predeclared")
        log_path = resolve(manifest_path.parent, entry["loop_log"])
        report_path = resolve(manifest_path.parent, entry["run_report"])
        expected_log_hash = entry.get("loop_log_sha256")
        expected_report_hash = entry.get("run_report_sha256")
        if sha256(log_path) != expected_log_hash:
            raise ValueError(f"{entry.get('id')}: loop log hash mismatch")
        if sha256(report_path) != expected_report_hash:
            raise ValueError(f"{entry.get('id')}: run report hash mismatch")
        log_text = log_path.read_text(errors="replace")
        run_report = json.loads(report_path.read_text(encoding="utf-8"))
        if run_report.get("session") != session:
            raise ValueError(f"{entry.get('id')}: run report session mismatch")
        windows = confirmation_windows(log_text, confirmation_frames)
        log_accepts = len(ACCEPT_PATTERN.findall(log_text))
        reported_accepts = int(run_report.get("automatic_loop_accepts", -1))
        accepts_match_expectation = (
            log_accepts == reported_accepts
            and ((log_accepts >= 1) if entry["expected_loop"] else (log_accepts == 0))
        )
        gate_windows = (
            [window for window in windows if window["ends_in_accept"]]
            if entry["expected_loop"]
            else windows
        )
        complete = (
            run_report.get("runtime_error") in (None, "")
            and float(run_report.get("pose_coverage", 0.0)) >= 0.98
            and int(run_report.get("loop_input_drop_events", 0)) == 0
            and int(run_report.get("estimator_keyframe_queue_drop_events", 0)) == 0
            and accepts_match_expectation
        )
        environment_failures = validate_environment_report(
            run_report.get("benchmark_environment", {})
        )
        qualified = (
            complete
            and run_report.get("result") == "PASS"
            and run_report.get("failure_scope") == "SLAM"
            and not environment_failures
        )
        dataset = {
            "id": entry["id"],
            "role": role,
            "session": session,
            "expected_loop": entry["expected_loop"],
            "loop_log": entry["loop_log"],
            "loop_log_sha256": sha256(log_path),
            "run_report": entry["run_report"],
            "run_report_sha256": sha256(report_path),
            "complete": complete,
            "log_accepts": log_accepts,
            "reported_accepts": reported_accepts,
            "accepts_match_expectation": accepts_match_expectation,
            "environment_qualified": not environment_failures,
            "environment_failures": environment_failures,
            "qualified_for_freeze": qualified,
            "confirmation_windows": windows,
            "gate_windows": gate_windows,
        }
        datasets.append(dataset)
        if not complete or not gate_windows:
            continue
        target = exploratory_positive if entry["expected_loop"] else exploratory_negative
        target.append(dataset)
        if qualified:
            target = qualified_positive if entry["expected_loop"] else qualified_negative
            target.append(dataset)

    def support_bounds(positive: list[dict], negative: list[dict]) -> dict | None:
        if not positive or not negative:
            return None
        positive_floor = min(
            window["minimum_spatial_support"]
            for dataset in positive
            for window in dataset["gate_windows"]
        )
        negative_ceiling = max(
            window["minimum_spatial_support"]
            for dataset in negative
            for window in dataset["gate_windows"]
        )
        return {
            "lower_exclusive": negative_ceiling,
            "upper_inclusive": positive_floor,
            "separable": negative_ceiling < positive_floor,
            "midpoint_candidate": (
                (negative_ceiling + positive_floor) / 2
                if negative_ceiling < positive_floor
                else None
            ),
        }

    def unique_sessions(datasets: list[dict], role: str | None = None) -> set[str]:
        return {
            dataset["session"]
            for dataset in datasets
            if role is None or dataset["role"] == role
        }

    qualified_counts = {
        "positive_runs": len(unique_sessions(qualified_positive)),
        "negative_runs": len(unique_sessions(qualified_negative)),
        "validation_positive_runs": len(
            unique_sessions(qualified_positive, "validation")
        ),
        "validation_negative_runs": len(
            unique_sessions(qualified_negative, "validation")
        ),
    }
    requirement_counts = {
        "min_positive_runs": "positive_runs",
        "min_negative_runs": "negative_runs",
        "min_validation_positive_runs": "validation_positive_runs",
        "min_validation_negative_runs": "validation_negative_runs",
    }
    count_failures = []
    for requirement, minimum in requirements.items():
        count_name = requirement_counts.get(requirement)
        if count_name is None:
            raise ValueError(f"unsupported evidence requirement: {requirement}")
        count = qualified_counts[count_name]
        if count < minimum:
            count_failures.append(f"{count_name} {count} < {minimum}")
    qualified_bounds = support_bounds(qualified_positive, qualified_negative)
    if qualified_bounds is not None and not qualified_bounds["separable"]:
        count_failures.append("qualified positive and negative windows are not separable")
    if (
        qualified_bounds is not None
        and qualified_bounds["separable"]
        and qualified_bounds["upper_inclusive"]
        - qualified_bounds["lower_exclusive"]
        < minimum_support_margin
    ):
        count_failures.append(
            "qualified spatial-support margin is below "
            f"{minimum_support_margin:.4f}"
        )
    freeze_allowed = not count_failures and qualified_bounds is not None
    selected_threshold = (
        qualified_bounds["midpoint_candidate"] if freeze_allowed else None
    )
    return {
        "result": "PASS" if freeze_allowed else "INSUFFICIENT_EVIDENCE",
        "threshold_freeze_allowed": freeze_allowed,
        "truth_policy": "development_and_validation_only_hidden_forbidden",
        "confirmation_frames": confirmation_frames,
        "requirements": requirements,
        "minimum_support_margin": minimum_support_margin,
        "qualified_counts": qualified_counts,
        "qualified_interval": qualified_bounds,
        "selected_threshold": selected_threshold,
        "exploratory_interval": support_bounds(
            exploratory_positive, exploratory_negative
        ),
        "failures": count_failures,
        "datasets": datasets,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="PnP空间覆盖阈值盲测前分析")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = analyze_manifest(args.manifest)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if report["result"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
