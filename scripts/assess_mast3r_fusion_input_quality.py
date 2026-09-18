#!/usr/bin/env python3
"""Reject fusion inputs with independently corroborated onboard inconsistency."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def assess(
    graph_report: dict,
    fusion_report: dict,
    *,
    max_stereo_edge_rmse_m: float = 0.004,
    max_input_disagreement_p95_m: float = 0.025,
    max_severe_input_disagreement_p95_m: float = 0.050,
    max_metric_scale_disagreement: float = 0.12,
    max_full_rate_correction_m: float = 0.010,
) -> dict:
    if graph_report.get("schema") != "umi_mast3r_stereo_imu_fusion_v2":
        raise ValueError("invalid MASt3R graph report schema")
    if fusion_report.get("schema") != "umi_docker2_mast3r_complementary_v1":
        raise ValueError("invalid complementary fusion report schema")
    for name, report in (("graph", graph_report), ("fusion", fusion_report)):
        if report.get("result") != "PASS":
            raise ValueError(f"{name} report did not pass internal checks")
        if report.get("slam_supervision") is not False:
            raise ValueError(f"{name} report must disable supervision")
        if report.get("external_ground_truth_used") is not False:
            raise ValueError(f"{name} report must not use external ground truth")

    graph_output = Path(graph_report["output"]).resolve()
    fusion_graph_input = Path(
        fusion_report["inputs"]["mast3r_camera_trajectory"]
    ).resolve()
    if graph_output != fusion_graph_input:
        raise ValueError("fusion report does not consume the supplied graph output")

    stereo_rmse = float(
        graph_report["stereo_translation_fusion"]["stereo_edge_rmse_after_m"]
    )
    input_disagreement = float(
        fusion_report["fusion"]["input_disagreement_p95_mm"]
    ) / 1000.0
    metric_scale_disagreement = float(
        graph_report["metric_scale_consistency"]["relative_difference"]
    )
    full_rate_correction = float(
        graph_report["full_rate_imu_position_refinement"][
            "correction_requested_max_m"
        ]
    )
    fusion_metrics = fusion_report["fusion"]
    position_branch_weight = float(
        fusion_metrics.get(
            "effective_local_weight_max",
            fusion_metrics.get("local_weight", 1.0),
        )
    )
    values = (
        stereo_rmse,
        input_disagreement,
        metric_scale_disagreement,
        full_rate_correction,
        position_branch_weight,
        max_stereo_edge_rmse_m,
        max_input_disagreement_p95_m,
        max_severe_input_disagreement_p95_m,
        max_metric_scale_disagreement,
        max_full_rate_correction_m,
    )
    if not all(math.isfinite(value) and value >= 0.0 for value in values):
        raise ValueError("quality metrics and thresholds must be finite and non-negative")

    stereo_geometry_inconsistent = stereo_rmse >= max_stereo_edge_rmse_m
    independent_trajectories_disagree = (
        input_disagreement >= max_input_disagreement_p95_m
    )
    severe_shape_disagreement = (
        stereo_geometry_inconsistent and independent_trajectories_disagree
    )
    unobservable_onboard_branch = (
        input_disagreement >= max_severe_input_disagreement_p95_m
        and position_branch_weight > 0.0
    )
    primary_shape_not_independently_supported = (
        input_disagreement >= max_severe_input_disagreement_p95_m
        and position_branch_weight == 0.0
        and stereo_rmse >= 0.875 * max_stereo_edge_rmse_m
    )
    metric_and_inertial_estimates_conflict = (
        metric_scale_disagreement >= max_metric_scale_disagreement
        and full_rate_correction >= max_full_rate_correction_m
    )
    rejected = (
        severe_shape_disagreement
        or unobservable_onboard_branch
        or primary_shape_not_independently_supported
        or metric_and_inertial_estimates_conflict
    )
    reason = None
    if unobservable_onboard_branch:
        reason = "independent_onboard_trajectory_branch_unobservable"
    elif primary_shape_not_independently_supported:
        reason = "primary_shape_not_independently_supported"
    elif severe_shape_disagreement:
        reason = "stereo_geometry_inconsistent_and_independent_trajectories_disagree"
    elif metric_and_inertial_estimates_conflict:
        reason = "stereo_imu_scale_and_local_inertial_shape_inconsistent"
    return {
        "schema": "umi_mast3r_fusion_input_quality_v1",
        "result": "REJECT" if rejected else "PASS",
        "slam_supervision": False,
        "external_ground_truth_used": False,
        "reason": reason,
        "stereo_edge_rmse_after_m": stereo_rmse,
        "maximum_stereo_edge_rmse_m": max_stereo_edge_rmse_m,
        "input_disagreement_p95_m": input_disagreement,
        "maximum_input_disagreement_p95_m": max_input_disagreement_p95_m,
        "maximum_severe_input_disagreement_p95_m": (
            max_severe_input_disagreement_p95_m
        ),
        "metric_scale_relative_difference": metric_scale_disagreement,
        "maximum_metric_scale_relative_difference": max_metric_scale_disagreement,
        "full_rate_imu_requested_correction_m": full_rate_correction,
        "maximum_full_rate_imu_requested_correction_m": max_full_rate_correction_m,
        "position_branch_used": position_branch_weight > 0.0,
        "position_branch_weight_max": position_branch_weight,
        "primary_shape_independently_supported": (
            not primary_shape_not_independently_supported
        ),
        "policy": (
            "reject when a position branch used by the output is unobservable, when "
            "an isolated primary branch also has marginal stereo geometry, when "
            "D405/stereo and "
            "visual-inertial geometry both fail, or when stereo/IMU metric scale and "
            "full-rate inertial shape correction independently exceed their "
            "consistency limits"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph-report", type=Path, required=True)
    parser.add_argument("--fusion-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-stereo-edge-rmse-mm", type=float, default=4.0)
    parser.add_argument(
        "--max-input-disagreement-p95-mm", type=float, default=25.0
    )
    parser.add_argument(
        "--max-severe-input-disagreement-p95-mm", type=float, default=50.0
    )
    parser.add_argument(
        "--max-metric-scale-disagreement-percent", type=float, default=12.0
    )
    parser.add_argument(
        "--max-full-rate-correction-mm", type=float, default=10.0
    )
    args = parser.parse_args()
    report = assess(
        json.loads(args.graph_report.read_text(encoding="utf-8")),
        json.loads(args.fusion_report.read_text(encoding="utf-8")),
        max_stereo_edge_rmse_m=args.max_stereo_edge_rmse_mm / 1000.0,
        max_input_disagreement_p95_m=(
            args.max_input_disagreement_p95_mm / 1000.0
        ),
        max_severe_input_disagreement_p95_m=(
            args.max_severe_input_disagreement_p95_mm / 1000.0
        ),
        max_metric_scale_disagreement=(
            args.max_metric_scale_disagreement_percent / 100.0
        ),
        max_full_rate_correction_m=args.max_full_rate_correction_mm / 1000.0,
    )
    report["inputs"] = {
        "graph_report": str(args.graph_report.resolve()),
        "fusion_report": str(args.fusion_report.resolve()),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if report["result"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
