#!/usr/bin/env python3
"""Build an immutable three-variant SLAM evaluation evidence bundle.

This tool only scores already-completed trajectories. External ground truth is
read after SLAM has finished and is never used to modify any trajectory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from evaluate_slam_ground_truth import (
    body_trajectory_to_camera,
    interpolate_ground_truth,
    load_opencv_matrix,
    load_trajectory,
    pose_errors,
)
from slam_benchmark_environment import validate_environment_report
from slam_run_health import evaluate_slam_health, trajectory_diagnostics_from_csv


VARIANT_SOURCES = {
    "raw_vins": "vio_raw.csv",
    "auto_loop": "vio_corrected_stream.csv",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_artifact(source: Path, destination: Path) -> Path:
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)
    shutil.copy2(source, destination)
    return destination.resolve()


def write_json(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path.resolve()


def evaluate_trajectory(
    estimate: Path,
    ground_truth: Path,
    max_interpolation_gap_s: float,
    rpe_delta_samples: int,
    body_t_camera_yaml: Path | None,
    body_t_camera_key: str,
) -> dict:
    estimate_time, estimate_position, estimate_quaternion = load_trajectory(estimate)
    estimate_frame = "as_recorded"
    if body_t_camera_yaml is not None:
        body_t_camera = load_opencv_matrix(body_t_camera_yaml, body_t_camera_key)
        estimate_position, estimate_quaternion = body_trajectory_to_camera(
            estimate_position, estimate_quaternion, body_t_camera
        )
        estimate_frame = f"camera_via_{body_t_camera_key}"

    gt_time, gt_position, gt_quaternion = load_trajectory(ground_truth)
    inside, valid, interpolated, interpolated_quaternion = interpolate_ground_truth(
        estimate_time,
        gt_time,
        gt_position,
        gt_quaternion,
        max_interpolation_gap_s,
    )
    selected_position = estimate_position[inside][valid]
    selected_quaternion = estimate_quaternion[inside][valid]
    if len(selected_position) < max(20, rpe_delta_samples + 1):
        raise ValueError("insufficient timestamp-overlapped trajectory samples")

    metrics = pose_errors(
        selected_position,
        selected_quaternion,
        interpolated[:, 1:],
        interpolated_quaternion,
        rpe_delta_samples,
    )
    metrics.update(
        {
            "scope": "external_ground_truth_product_evaluation",
            "estimate": str(estimate.resolve()),
            "ground_truth": str(ground_truth.resolve()),
            "max_interpolation_gap_s": max_interpolation_gap_s,
            "estimate_frame": estimate_frame,
            "truth_usage": "post_run_scoring_only",
        }
    )
    return metrics


def build_variant_evidence(
    *,
    dataset_id: str,
    run_dir: Path,
    depth_trajectory: Path,
    depth_factor_report: Path,
    ground_truth: Path,
    session_inputs: Path,
    output_dir: Path,
    max_interpolation_gap_s: float = 0.1,
    rpe_delta_samples: int = 30,
    body_t_camera_yaml: Path | None = None,
    body_t_camera_key: str = "body_T_cam0",
) -> dict:
    run_dir = run_dir.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")

    base_report_path = run_dir / "run_acceptance.json"
    if not base_report_path.is_file():
        raise FileNotFoundError(base_report_path)
    base_report = json.loads(base_report_path.read_text(encoding="utf-8"))
    if (
        base_report.get("result") != "PASS"
        or base_report.get("failure_scope") != "SLAM"
    ):
        raise ValueError("source run is not a valid SLAM evaluation")
    if validate_environment_report(base_report.get("benchmark_environment", {})):
        raise ValueError("source run lacks a passing benchmark environment preflight")
    if base_report.get("health") != evaluate_slam_health(base_report):
        raise ValueError("source run health report is missing or inconsistent")
    if base_report["health"].get("state") != "SLAM_HEALTHY":
        raise ValueError("source run health is not SLAM_HEALTHY")
    frozen_inputs = json.loads(session_inputs.read_text(encoding="utf-8"))
    if frozen_inputs.get("frozen_before_slam") is not True:
        raise ValueError("source session inputs were not frozen before SLAM")
    if frozen_inputs.get("truth_usage_policy") != (
        "withheld_from_slam_until_post_run_scoring"
    ):
        raise ValueError("source session truth isolation policy is invalid")
    if Path(frozen_inputs.get("session", "")).resolve() != Path(
        base_report.get("session", "")
    ).resolve():
        raise ValueError("frozen session inputs do not match source run session")
    factor_report = json.loads(depth_factor_report.read_text(encoding="utf-8"))
    if factor_report.get("result") != "PASS":
        raise ValueError("depth factor evidence is not PASS")
    if factor_report.get("scope") != "depth_plane_factor_safety_evidence":
        raise ValueError("depth factor evidence scope is invalid")
    if factor_report.get("truth_usage") != "none":
        raise ValueError("depth factor evidence used ground truth")
    if Path(factor_report.get("raw_trajectory", "")).resolve() != (
        run_dir / VARIANT_SOURCES["raw_vins"]
    ).resolve():
        raise ValueError("depth factor evidence scored a different raw trajectory")
    if Path(factor_report.get("corrected_trajectory", "")).resolve() != Path(
        depth_trajectory
    ).resolve():
        raise ValueError("depth factor evidence scored a different corrected trajectory")
    bundled_session_inputs = copy_artifact(
        session_inputs, output_dir / "session_inputs.json"
    )
    bundled_ground_truth = copy_artifact(
        ground_truth, output_dir / "external_ground_truth.csv"
    )

    sources = {
        variant: run_dir / filename for variant, filename in VARIANT_SOURCES.items()
    }
    sources["depth_plane"] = depth_trajectory
    entries: dict[str, dict] = {}
    raw_artifact_facts: dict | None = None

    for variant, source in sources.items():
        variant_dir = output_dir / variant
        trajectory = copy_artifact(source, variant_dir / "trajectory.csv")
        trajectory_facts = trajectory_diagnostics_from_csv(trajectory)
        if variant == "raw_vins":
            raw_artifact_facts = trajectory_facts
        if raw_artifact_facts is None:
            raise RuntimeError("raw VINS trajectory must be processed first")

        run_report = dict(base_report)
        run_report.update(
            {
                "variant": variant,
                "trajectory": str(trajectory),
                "trajectory_sha256": sha256(trajectory),
                "source_run_report": str(base_report_path.resolve()),
                "source_run_report_sha256": sha256(base_report_path),
                "truth_usage": "none_during_slam",
            }
        )
        run_report["raw_odometry_samples"] = raw_artifact_facts["sample_count"]
        run_report["corrected_odometry_samples"] = trajectory_facts["sample_count"]
        expected_samples = int(run_report["expected_pose_samples_after_skip"])
        run_report["pose_coverage"] = min(
            raw_artifact_facts["sample_count"], trajectory_facts["sample_count"]
        ) / expected_samples
        run_report["raw_trajectory_diagnostics"] = raw_artifact_facts["diagnostics"]
        run_report["corrected_trajectory_diagnostics"] = trajectory_facts[
            "diagnostics"
        ]
        raw_z_span = raw_artifact_facts["diagnostics"]["z_span_m"]
        corrected_z_span = trajectory_facts["diagnostics"]["z_span_m"]
        run_report["z_span_retention_ratio"] = (
            corrected_z_span / raw_z_span
            if raw_z_span is not None
            and corrected_z_span is not None
            and raw_z_span >= 0.10
            else None
        )
        if variant == "raw_vins":
            run_report["observed_automatic_loop_accepts"] = int(
                base_report.get("automatic_loop_accepts", 0)
            )
            run_report["automatic_loop_accepts"] = 0
            run_report["pose_graph_health"] = {
                "optimizations": 0,
                "usable_optimizations": 0,
                "rejected_optimizations": 0,
            }
            pnp_quality = dict(run_report.get("pnp_quality", {}))
            pnp_quality["accepted_edges"] = []
            run_report["pnp_quality"] = pnp_quality
        if variant == "depth_plane":
            run_report["depth_factor_result"] = factor_report.get("result")
            if factor_report.get("result") != "PASS":
                run_report["result"] = "FAIL"
                failures = list(run_report.get("failures", []))
                failures.append("depth plane factor report is not PASS")
                run_report["failures"] = failures
        run_report["health"] = evaluate_slam_health(run_report)

        run_report_path = write_json(variant_dir / "run_report.json", run_report)
        metrics = evaluate_trajectory(
            trajectory,
            bundled_ground_truth,
            max_interpolation_gap_s,
            rpe_delta_samples,
            body_t_camera_yaml,
            body_t_camera_key,
        )
        metrics["variant"] = variant
        metrics_path = write_json(
            variant_dir / "ground_truth_report.json", metrics
        )
        entry = {
            "run_report": str(run_report_path),
            "run_report_sha256": sha256(run_report_path),
            "trajectory": str(trajectory),
            "trajectory_sha256": sha256(trajectory),
            "ground_truth_report": str(metrics_path),
            "ground_truth_report_sha256": sha256(metrics_path),
        }
        if variant == "depth_plane":
            copied_factor = copy_artifact(
                depth_factor_report, variant_dir / "factor_report.json"
            )
            entry.update(
                {
                    "factor_report": str(copied_factor),
                    "factor_report_sha256": sha256(copied_factor),
                }
            )
        entries[variant] = entry

    fragment = {
        "dataset_id": dataset_id,
        "truth_usage": "post_run_scoring_only",
        "ground_truth": str(bundled_ground_truth),
        "ground_truth_sha256": sha256(bundled_ground_truth),
        "session_inputs": str(bundled_session_inputs),
        "session_inputs_sha256": sha256(bundled_session_inputs),
        "variant_reports": entries,
    }
    write_json(output_dir / "manifest_fragment.json", fragment)
    return fragment


def main() -> int:
    parser = argparse.ArgumentParser(
        description="封存 raw/自动回环/Depth 平面三版本盲测证据（真值仅事后评分）"
    )
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--depth-trajectory", type=Path, required=True)
    parser.add_argument("--depth-factor-report", type=Path, required=True)
    parser.add_argument("--ground-truth", type=Path, required=True)
    parser.add_argument("--session-inputs", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-interpolation-gap-s", type=float, default=0.1)
    parser.add_argument("--rpe-delta-samples", type=int, default=30)
    parser.add_argument("--estimate-body-t-camera-yaml", type=Path)
    parser.add_argument("--body-t-camera-key", default="body_T_cam0")
    args = parser.parse_args()
    fragment = build_variant_evidence(
        dataset_id=args.dataset_id,
        run_dir=args.run_dir,
        depth_trajectory=args.depth_trajectory,
        depth_factor_report=args.depth_factor_report,
        ground_truth=args.ground_truth,
        session_inputs=args.session_inputs,
        output_dir=args.output_dir,
        max_interpolation_gap_s=args.max_interpolation_gap_s,
        rpe_delta_samples=args.rpe_delta_samples,
        body_t_camera_yaml=args.estimate_body_t_camera_yaml,
        body_t_camera_key=args.body_t_camera_key,
    )
    print(json.dumps(fragment, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
