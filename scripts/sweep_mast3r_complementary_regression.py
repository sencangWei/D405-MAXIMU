#!/usr/bin/env python3
"""Evaluate fixed complementary-fusion candidates on restored UMI-only graphs."""

from __future__ import annotations

import argparse
import json
from argparse import Namespace
from pathlib import Path

import numpy as np

try:
    from scripts.evaluate_slam_ground_truth import (
        acceptance,
        interpolate_ground_truth,
        load_trajectory,
        pose_errors,
    )
    from scripts.fuse_docker2_mast3r_complementary import run as run_fusion
    from scripts.run_mast3r_fusion_regression_corpus import precision_summary
except ModuleNotFoundError:  # Direct execution places scripts/ on sys.path.
    from evaluate_slam_ground_truth import (
        acceptance,
        interpolate_ground_truth,
        load_trajectory,
        pose_errors,
    )
    from fuse_docker2_mast3r_complementary import run as run_fusion
    from run_mast3r_fusion_regression_corpus import precision_summary


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "config/mast3r_fusion_regression_corpus_13.json"
DEFAULT_BASELINE_ROOT = ROOT / "reports/mast3r_fusion_regression_13/baseline_current"
DEFAULT_GRAPH_ROOT = ROOT / "reports/mast3r_fusion_regression_13/backend_restored_v1"
DEFAULT_OUTPUT_ROOT = ROOT / "reports/mast3r_fusion_regression_13/complementary_sweep_v1"
VINS_CONFIG = Path(
    "/home/robot/umi_docker2_product_1.0.0-20260829/"
    "docker2_release/formal_runtime_calibration/vins_config.yaml"
)

# smoothing_s, local_weight, scale_weight, adaptive_local_weight
CANDIDATES = {
    "graph_only": (8.0, 0.0, 0.0, False),
    "scale0475": (8.0, 0.0, 0.475, False),
    "scale085": (15.0, 0.0, 0.85, False),
    "s8_l010_k0475": (8.0, 0.10, 0.475, True),
    "s8_l020_k0475": (8.0, 0.20, 0.475, True),
    "s8_l035_k0475": (8.0, 0.35, 0.475, True),
    "s15_l010_k085": (15.0, 0.10, 0.85, True),
    "s15_l020_k0475": (15.0, 0.20, 0.475, True),
    "s15_l020_k085": (15.0, 0.20, 0.85, True),
    "historical_restore": (15.0, 0.35, 0.85, True),
}


def build_fusion_args(
    baseline: Path,
    backend: Path,
    output: Path,
    candidate: tuple[float, float, float, bool],
) -> Namespace:
    smoothing_s, local_weight, scale_weight, adaptive = candidate
    return Namespace(
        mast3r=backend / "trajectory_graph.csv",
        docker2=baseline / "vins/vio_corrected_stream.csv",
        docker2_report=baseline / "vins/run_acceptance.json",
        body_t_camera_yaml=VINS_CONFIG,
        scale_horizon_s=1.0,
        smoothing_s=smoothing_s,
        docker2_local_weight=local_weight,
        docker2_scale_weight=scale_weight,
        adaptive_local_weight=adaptive,
        roughness_threshold_mm=9.0,
        adaptive_weight_strength=0.45,
        use_docker2_orientation_for_lever_arm=True,
        output=output / "trajectory_fused.csv",
        report=output / "fusion_report.json",
    )


def evaluate(estimate: Path, ground_truth: Path, thresholds: dict) -> dict:
    estimate_time, estimate_position, estimate_quaternion = load_trajectory(estimate)
    gt_time, gt_position, gt_quaternion = load_trajectory(ground_truth)
    inside, valid, interpolated, interpolated_quaternion = interpolate_ground_truth(
        estimate_time, gt_time, gt_position, gt_quaternion, 0.1
    )
    selected_position = estimate_position[inside][valid]
    selected_quaternion = estimate_quaternion[inside][valid]
    metrics = pose_errors(
        selected_position,
        selected_quaternion,
        interpolated[:, 1:],
        interpolated_quaternion,
        30,
    )
    metrics.update(
        {
            "scope": "external_ground_truth_product_evaluation",
            "estimate": str(estimate.resolve()),
            "ground_truth": str(ground_truth.resolve()),
            "timestamp_overlap_ratio": float(len(selected_position) / len(estimate_time)),
        }
    )
    metrics.update(
        acceptance(
            metrics,
            thresholds["max_ate_rmse_mm"],
            thresholds["max_ate_p95_mm"],
            thresholds["max_ate_max_mm"],
            thresholds["min_within_10mm_ratio"],
            thresholds["max_rotation_rmse_deg"],
            thresholds["min_timestamp_overlap_ratio"],
        )
    )
    return metrics


def aggregate(results: list[dict]) -> dict:
    return {
        "pass_count": sum(item["precision"]["result"] == "PASS" for item in results),
        "mean_rmse_mm": float(np.mean([item["precision"]["rmse_mm"] for item in results])),
        "mean_p95_mm": float(np.mean([item["precision"]["p95_mm"] for item in results])),
        "worst_max_mm": float(max(item["precision"]["max_mm"] for item in results)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--baseline-root", type=Path, default=DEFAULT_BASELINE_ROOT)
    parser.add_argument("--graph-root", type=Path, default=DEFAULT_GRAPH_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    backend_summary = json.loads(
        (args.graph_root / "summary.json").read_text(encoding="utf-8")
    )
    dataset_ids = {
        item["id"]
        for item in backend_summary["results"]
        if item.get("status") == "SCORED"
    }
    datasets = [item for item in manifest["datasets"] if item["id"] in dataset_ids]
    candidate_results: dict[str, list[dict]] = {}

    for name, candidate in CANDIDATES.items():
        results = []
        for dataset in datasets:
            baseline = args.baseline_root / dataset["id"]
            backend = args.graph_root / dataset["id"]
            output = args.output_root / name / dataset["id"]
            output.mkdir(parents=True, exist_ok=True)
            fusion_args = build_fusion_args(baseline, backend, output, candidate)
            run_fusion(fusion_args)
            metrics = evaluate(
                fusion_args.output,
                baseline / "score/lighthouse_body_ground_truth.csv",
                manifest["thresholds"],
            )
            precision_path = output / "precision.json"
            precision_path.write_text(
                json.dumps(metrics, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            results.append(
                {
                    "id": dataset["id"],
                    "fold": dataset["fold"],
                    "precision": precision_summary(precision_path),
                }
            )
        candidate_results[name] = results

    summary = {
        "schema": "umi_mast3r_complementary_sweep_v1",
        "slam_supervision": False,
        "external_ground_truth_usage": "post_slam_aggregate_development_scoring_only",
        "candidate_parameters": {
            name: {
                "smoothing_s": values[0],
                "docker2_local_weight": values[1],
                "docker2_scale_weight": values[2],
                "adaptive_local_weight": values[3],
            }
            for name, values in CANDIDATES.items()
        },
        "candidate_aggregate": {
            name: aggregate(results) for name, results in candidate_results.items()
        },
        "candidate_results": candidate_results,
    }
    args.output_root.mkdir(parents=True, exist_ok=True)
    summary_path = args.output_root / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary["candidate_aggregate"], indent=2))
    print(f"Complementary sweep summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
