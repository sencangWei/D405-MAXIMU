#!/usr/bin/env python3
"""Repeat user-declared closed-loop sessions without feeding truth to SLAM."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from slam_benchmark_environment import capture_environment, evaluate_environment


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "config/slam_declared_loop_regression.json"


def resolve_project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def validate_manifest(manifest: dict) -> list[str]:
    failures: list[str] = []
    policy = manifest.get("truth_policy", {})
    if policy.get("usage") != "post_run_scoring_only":
        failures.append("truth usage must be post_run_scoring_only")
    if policy.get("never_input_to_slam") is not True:
        failures.append("manifest must forbid feeding truth to SLAM")
    ids: set[str] = set()
    for dataset in manifest.get("datasets", []):
        dataset_id = dataset.get("id", "")
        if not dataset_id or dataset_id in ids:
            failures.append(f"invalid or duplicate dataset id: {dataset_id!r}")
        ids.add(dataset_id)
        if dataset.get("expected_loop") is not True:
            failures.append(f"{dataset_id}: regression dataset is not a true loop")
        session = resolve_project_path(dataset.get("session", ""))
        acceptance = session / "acceptance.json"
        if not acceptance.is_file():
            failures.append(f"{dataset_id}: missing capture acceptance {acceptance}")
            continue
        actual_hash = hashlib.sha256(acceptance.read_bytes()).hexdigest()
        if actual_hash != dataset.get("acceptance_sha256"):
            failures.append(f"{dataset_id}: capture acceptance hash changed")
        capture = json.loads(acceptance.read_text(encoding="utf-8"))
        if capture.get("result") != "PASS":
            failures.append(f"{dataset_id}: capture acceptance is not PASS")
    return failures


def score_run(report: dict, *, max_endpoint_m: float, min_coverage: float) -> dict:
    diagnostics = report.get("corrected_trajectory_diagnostics", {})
    endpoint = diagnostics.get("endpoint_delta_m")
    accepts = int(report.get("automatic_loop_accepts", 0))
    coverage = report.get("pose_coverage")
    failures: list[str] = []
    if report.get("result") != "PASS":
        failures.append("run report is not PASS")
    if accepts < 1:
        failures.append("no automatic loop was accepted")
    if not isinstance(endpoint, (int, float)):
        failures.append("endpoint error is missing")
    elif endpoint > max_endpoint_m:
        failures.append(
            f"endpoint error {endpoint:.6f}m exceeds {max_endpoint_m:.6f}m"
        )
    if not isinstance(coverage, (int, float)) or coverage < min_coverage:
        failures.append(f"pose coverage is below {min_coverage:.3f}")
    if int(report.get("loop_input_drop_events", 0)) != 0:
        failures.append("loop input drops are nonzero")
    if int(report.get("estimator_keyframe_queue_drop_events", 0)) != 0:
        failures.append("estimator keyframe queue drops are nonzero")
    pose_graph = report.get("pose_graph_health", {})
    if int(pose_graph.get("rejected_optimizations", 0)) != 0:
        failures.append("pose graph rejected an optimization")
    health = report.get("health")
    if health is not None and health.get("state") != "SLAM_HEALTHY":
        failures.append(f"runtime health is {health.get('state')}")
    return {
        "result": "PASS" if not failures else "FAIL",
        "automatic_loop_accepts": accepts,
        "endpoint_error_m": endpoint,
        "pose_coverage": coverage,
        "failures": failures,
    }


def write_summary(path: Path, summary: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def inventory(manifest: dict) -> dict:
    thresholds = manifest["thresholds"]
    rows = []
    for dataset in manifest["datasets"]:
        report_value = dataset.get("reference_report")
        if not report_value:
            rows.append({"id": dataset["id"], "result": "PENDING", "report": None})
            continue
        report_path = resolve_project_path(report_value)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        rows.append(
            {
                "id": dataset["id"],
                "report": str(report_path),
                **score_run(
                    report,
                    max_endpoint_m=float(thresholds["max_endpoint_error_m"]),
                    min_coverage=float(thresholds["min_pose_coverage"]),
                ),
            }
        )
    passing = sum(row["result"] == "PASS" for row in rows)
    return {
        "mode": "REFERENCE_INVENTORY",
        "result": "PASS" if passing == len(rows) else "INCOMPLETE",
        "passing_datasets": passing,
        "total_datasets": len(rows),
        "datasets": rows,
    }


def execute(manifest: dict, out_root: Path, repetitions: int) -> dict:
    environment = evaluate_environment(capture_environment())
    if environment["result"] != "PASS":
        return {
            "mode": "REPEATED_REGRESSION",
            "result": "INFRASTRUCTURE_BLOCKED",
            "benchmark_environment": environment,
            "datasets": [],
        }

    thresholds = manifest["thresholds"]
    datasets = []
    for dataset_index, dataset in enumerate(manifest["datasets"]):
        runs = []
        for repetition in range(1, repetitions + 1):
            run_dir = out_root / dataset["id"] / f"run_{repetition:02d}"
            command = [
                sys.executable,
                str(ROOT / "scripts/test_vins_auto_loop.py"),
                str(resolve_project_path(dataset["session"])),
                "--out-dir",
                str(run_dir),
                "--expect-loop",
                "yes",
                "--max-loop-closure-m",
                str(thresholds["max_endpoint_error_m"]),
                "--min-pose-coverage",
                str(thresholds["min_pose_coverage"]),
                "--ros-domain-id",
                str(77 + dataset_index),
            ]
            completed = subprocess.run(command, cwd=ROOT, check=False)
            report_path = run_dir / "run_acceptance.json"
            if not report_path.is_file():
                runs.append(
                    {
                        "repetition": repetition,
                        "result": "FAIL",
                        "return_code": completed.returncode,
                        "failures": ["run_acceptance.json was not produced"],
                    }
                )
                continue
            report = json.loads(report_path.read_text(encoding="utf-8"))
            scored = score_run(
                report,
                max_endpoint_m=float(thresholds["max_endpoint_error_m"]),
                min_coverage=float(thresholds["min_pose_coverage"]),
            )
            runs.append(
                {
                    "repetition": repetition,
                    "return_code": completed.returncode,
                    "report": str(report_path),
                    **scored,
                }
            )
            if report.get("failure_scope") == "INFRASTRUCTURE":
                break
        datasets.append(
            {
                "id": dataset["id"],
                "motion": dataset["motion"],
                "result": "PASS"
                if len(runs) == repetitions and all(run["result"] == "PASS" for run in runs)
                else "FAIL",
                "runs": runs,
            }
        )
    return {
        "mode": "REPEATED_REGRESSION",
        "result": "PASS" if all(row["result"] == "PASS" for row in datasets) else "FAIL",
        "required_repetitions_per_dataset": repetitions,
        "benchmark_environment": environment,
        "datasets": datasets,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument(
        "--wait-for-environment",
        action="store_true",
        help="wait until benchmark preflight passes instead of exiting immediately",
    )
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--repetitions", type=int)
    parser.add_argument("--out-root", type=Path)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    failures = validate_manifest(manifest)
    if failures:
        print(json.dumps({"result": "FAIL", "failures": failures}, ensure_ascii=False, indent=2))
        return 2
    if args.inventory_only:
        summary = inventory(manifest)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    repetitions = args.repetitions or int(
        manifest["thresholds"]["required_repetitions_per_dataset"]
    )
    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    out_root = args.out_root or (
        ROOT
        / "reports"
        / f"declared_loop_regression_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    if args.wait_for_environment:
        if args.poll_seconds <= 0:
            raise ValueError("poll-seconds must be positive")
        while True:
            environment = evaluate_environment(capture_environment())
            if environment["result"] == "PASS":
                break
            print(
                "benchmark environment not ready; waiting: "
                + "; ".join(environment["failures"]),
                flush=True,
            )
            time.sleep(args.poll_seconds)
    summary = execute(manifest, out_root, repetitions)
    write_summary(out_root / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["result"] == "PASS" else 4 if summary["result"] == "INFRASTRUCTURE_BLOCKED" else 3


if __name__ == "__main__":
    raise SystemExit(main())
