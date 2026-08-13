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
REQUIRED_PROVENANCE_FILES = {
    "runner",
    "run_config",
    "left_calibration",
    "right_calibration",
    "vins_executable",
    "loop_executable",
    "replay_executable",
    "capture_acceptance",
    "camera_timestamps",
    "imu_samples",
}


def validate_run_provenance(report: dict) -> list[str]:
    provenance = report.get("provenance")
    if not isinstance(provenance, dict):
        return ["run provenance is missing"]
    files = provenance.get("files")
    if not isinstance(files, dict):
        return ["run provenance files are missing"]
    failures = []
    for name in sorted(REQUIRED_PROVENANCE_FILES):
        item = files.get(name)
        if not isinstance(item, dict):
            failures.append(f"provenance file is missing: {name}")
            continue
        digest = item.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            failures.append(f"provenance sha256 is invalid: {name}")
    revisions = provenance.get("git_revisions")
    if not isinstance(revisions, dict) or not all(
        isinstance(revisions.get(name), str) and len(revisions[name]) == 40
        for name in ("ego_vio_humble", "vins_fusion_ros2")
    ):
        failures.append("run git revisions are missing or invalid")
    return failures


def classify_loop_stage(report: dict, *, expected_loop: bool) -> str:
    accepts = int(report.get("automatic_loop_accepts", 0))
    if not expected_loop:
        return "FALSE_LOOP_ACCEPTED" if accepts else "SAFETY_CONTROL_CLEAN"
    if accepts:
        return "LOOP_ACCEPTED"
    retrieval = report.get("loop_retrieval", {})
    eligible_max = retrieval.get("eligible", {}).get("max")
    if not isinstance(eligible_max, (int, float)):
        return "RETRIEVAL_UNOBSERVABLE"
    if eligible_max <= 0:
        return "NO_ELIGIBLE_RETRIEVAL"
    pnp = report.get("pnp_quality", {})
    if int(pnp.get("samples", 0)) == 0:
        return "NO_USABLE_PNP"
    if int(pnp.get("geometry_pass_samples", 0)) == 0:
        return "GEOMETRY_REJECTED"
    stages = report.get("loop_stage_counts", {})
    if int(stages.get("correction_rejected", 0)) > 0:
        return "CORRECTION_SAFETY_REJECTED"
    if int(stages.get("pending", 0)) > 0:
        return "GEOMETRY_PASSED_CONFIRMATION_INCOMPLETE"
    return "GEOMETRY_PASSED_NOT_ACCEPTED"


def resolve_project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def all_datasets(manifest: dict) -> list[dict]:
    return [*manifest.get("datasets", []), *manifest.get("safety_controls", [])]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def select_session_db3(session: Path) -> Path:
    candidates = [path for path in session.glob("*.db3") if path.stat().st_size > 0]
    if not candidates:
        raise FileNotFoundError(f"no non-empty DB3 in {session}")
    return max(candidates, key=lambda path: path.stat().st_size)


def freeze_dataset_inputs(manifest: dict, output: Path) -> dict:
    datasets = []
    for dataset in all_datasets(manifest):
        session = resolve_project_path(dataset["session"]).resolve()
        files = {
            "capture_acceptance": session / "acceptance.json",
            "camera_db3": select_session_db3(session),
            "camera_timestamps": session / "d405_frames.csv",
            "imu_samples": session / "external_imu" / "imu.bin",
        }
        frozen_files = {}
        for name, path in files.items():
            before = path.stat()
            digest = sha256_file(path)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (
                after.st_size,
                after.st_mtime_ns,
            ):
                raise RuntimeError(f"input changed while hashing: {path}")
            frozen_files[name] = {
                "path": str(path),
                "size_bytes": after.st_size,
                "mtime_ns": after.st_mtime_ns,
                "sha256": digest,
            }
        datasets.append({"id": dataset["id"], "files": frozen_files})
    result = {
        "schema_version": 1,
        "truth_usage": "post_run_scoring_only",
        "datasets": datasets,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def validate_frozen_dataset(dataset: dict) -> list[str]:
    failures = []
    for name, item in dataset["files"].items():
        path = Path(item["path"])
        try:
            stat = path.stat()
        except FileNotFoundError:
            failures.append(f"frozen input disappeared: {name}")
            continue
        if stat.st_size != item["size_bytes"] or stat.st_mtime_ns != item["mtime_ns"]:
            failures.append(f"frozen input changed: {name}")
    return failures


def validate_manifest(manifest: dict) -> list[str]:
    failures: list[str] = []
    policy = manifest.get("truth_policy", {})
    if policy.get("usage") != "post_run_scoring_only":
        failures.append("truth usage must be post_run_scoring_only")
    if policy.get("never_input_to_slam") is not True:
        failures.append("manifest must forbid feeding truth to SLAM")
    ids: set[str] = set()
    for dataset in all_datasets(manifest):
        dataset_id = dataset.get("id", "")
        if not dataset_id or dataset_id in ids:
            failures.append(f"invalid or duplicate dataset id: {dataset_id!r}")
        ids.add(dataset_id)
        if not isinstance(dataset.get("expected_loop"), bool):
            failures.append(f"{dataset_id}: expected_loop must be true or false")
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


def score_run(
    report: dict,
    *,
    expected_loop: bool,
    max_endpoint_m: float,
    min_coverage: float,
    expected_max_candidates: int | None = None,
) -> dict:
    diagnostics = report.get("corrected_trajectory_diagnostics", {})
    endpoint = diagnostics.get("endpoint_delta_m")
    accepts = int(report.get("automatic_loop_accepts", 0))
    coverage = report.get("pose_coverage")
    failures: list[str] = []
    if report.get("result") != "PASS":
        failures.append("run report is not PASS")
    if expected_loop and accepts < 1:
        failures.append("no automatic loop was accepted")
    if not expected_loop and accepts:
        failures.append(f"false automatic loops accepted: {accepts}")
    if expected_loop and not isinstance(endpoint, (int, float)):
        failures.append("endpoint error is missing")
    elif expected_loop and endpoint >= max_endpoint_m:
        failures.append(
            f"endpoint error {endpoint:.6f}m is not below {max_endpoint_m:.6f}m"
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
    if (
        expected_max_candidates is not None
        and report.get("max_loop_candidates") != expected_max_candidates
    ):
        failures.append(
            "effective max_loop_candidates does not match the regression contract"
        )
    retrieval = report.get("loop_retrieval", {})
    if expected_max_candidates is not None and int(retrieval.get("frames", 0)) < 1:
        failures.append("loop retrieval diagnostics are missing")
    if expected_max_candidates is not None:
        failures.extend(validate_run_provenance(report))
    health = report.get("health")
    if health is not None and health.get("state") != "SLAM_HEALTHY":
        failures.append(f"runtime health is {health.get('state')}")
    return {
        "result": "PASS" if not failures else "FAIL",
        "loop_stage": classify_loop_stage(report, expected_loop=expected_loop),
        "automatic_loop_accepts": accepts,
        "endpoint_error_m": endpoint,
        "pose_coverage": coverage,
        "loop_retrieval": retrieval,
        "failures": failures,
    }


def write_summary(path: Path, summary: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def write_markdown_report(path: Path, summary: dict) -> None:
    lines = [
        "# 无提示自动回环重复性报告",
        "",
        f"- 总结论：**{summary['result']}**",
        "- 真值用途：仅在SLAM完成后评分；未输入估计器、回环检测或位姿图。",
        "- 门槛：真闭环每轮至少接受1条自动回环、首尾误差<10 mm、轨迹覆盖率>=98%；负样本必须0误回环。",
        "",
        "| 数据 | 轮次 | 结果 | 阶段 | 自动回环 | 首尾误差(mm) | 覆盖率 | DBoW候选/有效候选(均值) | 失败原因 |",
        "|---|---:|---|---|---:|---:|---:|---:|---|",
    ]
    for dataset in summary.get("datasets", []):
        for run in dataset.get("runs", []):
            endpoint = run.get("endpoint_error_m")
            endpoint_text = "—" if endpoint is None else f"{1000.0 * endpoint:.2f}"
            coverage = run.get("pose_coverage")
            coverage_text = "—" if coverage is None else f"{100.0 * coverage:.2f}%"
            retrieval = run.get("loop_retrieval", {})
            returned = retrieval.get("returned", {}).get("mean")
            eligible = retrieval.get("eligible", {}).get("mean")
            retrieval_text = (
                "—"
                if returned is None or eligible is None
                else f"{returned:.1f}/{eligible:.1f}"
            )
            failure_text = "；".join(run.get("failures", [])) or "—"
            lines.append(
                f"| {dataset['id']} | {run['repetition']} | {run['result']} | "
                f"{run.get('loop_stage', '—')} | "
                f"{run.get('automatic_loop_accepts', 0)} | {endpoint_text} | "
                f"{coverage_text} | {retrieval_text} | {failure_text} |"
            )
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def inventory(manifest: dict) -> dict:
    thresholds = manifest["thresholds"]
    rows = []
    for dataset in all_datasets(manifest):
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
                    expected_loop=dataset["expected_loop"],
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


def execute(
    manifest: dict,
    out_root: Path,
    repetitions: int,
    *,
    wait_for_environment: bool,
    poll_seconds: float,
) -> dict:
    environment = evaluate_environment(capture_environment())
    if environment["result"] != "PASS":
        return {
            "mode": "REPEATED_REGRESSION",
            "result": "INFRASTRUCTURE_BLOCKED",
            "benchmark_environment": environment,
            "datasets": [],
        }

    dataset_inputs_path = out_root / "dataset_inputs.sha256.json"
    frozen_inputs = freeze_dataset_inputs(manifest, dataset_inputs_path)
    frozen_by_id = {item["id"]: item for item in frozen_inputs["datasets"]}

    thresholds = manifest["thresholds"]
    datasets = []
    contract = manifest.get("algorithm_contract", {})
    for dataset_index, dataset in enumerate(all_datasets(manifest)):
        runs = []
        for repetition in range(1, repetitions + 1):
            input_failures = validate_frozen_dataset(frozen_by_id[dataset["id"]])
            if input_failures:
                runs.append(
                    {
                        "repetition": repetition,
                        "result": "FAIL",
                        "return_code": None,
                        "failures": input_failures,
                    }
                )
                break
            while True:
                per_run_environment = evaluate_environment(capture_environment())
                if per_run_environment["result"] == "PASS":
                    break
                if not wait_for_environment:
                    return {
                        "mode": "REPEATED_REGRESSION",
                        "result": "INFRASTRUCTURE_BLOCKED",
                        "benchmark_environment": per_run_environment,
                        "datasets": datasets,
                    }
                print(
                    "benchmark environment changed; waiting before next run: "
                    + "; ".join(per_run_environment["failures"]),
                    flush=True,
                )
                time.sleep(poll_seconds)
            run_dir = out_root / dataset["id"] / f"run_{repetition:02d}"
            command = [
                sys.executable,
                str(ROOT / "scripts/test_vins_auto_loop.py"),
                str(resolve_project_path(dataset["session"])),
                "--out-dir",
                str(run_dir),
                "--expect-loop",
                "yes" if dataset["expected_loop"] else "no",
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
                expected_loop=dataset["expected_loop"],
                max_endpoint_m=float(thresholds["max_endpoint_error_m"]),
                min_coverage=float(thresholds["min_pose_coverage"]),
                expected_max_candidates=contract.get("max_loop_candidates"),
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
        "positive_dataset_count": len(manifest.get("datasets", [])),
        "safety_control_count": len(manifest.get("safety_controls", [])),
        "benchmark_environment": environment,
        "dataset_inputs": str(dataset_inputs_path),
        "dataset_inputs_sha256": sha256_file(dataset_inputs_path),
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
    summary = execute(
        manifest,
        out_root,
        repetitions,
        wait_for_environment=args.wait_for_environment,
        poll_seconds=args.poll_seconds,
    )
    write_summary(out_root / "summary.json", summary)
    write_markdown_report(out_root / "report.md", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["result"] == "PASS" else 4 if summary["result"] == "INFRASTRUCTURE_BLOCKED" else 3


if __name__ == "__main__":
    raise SystemExit(main())
