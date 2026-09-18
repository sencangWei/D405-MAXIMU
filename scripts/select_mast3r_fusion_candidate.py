#!/usr/bin/env python3
"""Select a MASt3R fusion candidate using onboard consistency only."""

from __future__ import annotations

import argparse
import json
import math
import shutil
from pathlib import Path


def load_json_report(path: Path) -> dict:
    def reject_nonfinite(value: str):
        raise ValueError(f"non-finite JSON value {value} in {path}")

    report = json.loads(
        path.read_text(encoding="utf-8"), parse_constant=reject_nonfinite
    )
    if not isinstance(report, dict):
        raise ValueError(f"report must be a JSON object: {path}")
    return report


def load_graph_report(path: Path) -> dict:
    report = load_json_report(path)
    if report.get("schema") != "umi_mast3r_stereo_imu_fusion_v2":
        raise ValueError(f"unexpected graph report schema: {path}")
    if report.get("result") != "PASS":
        raise ValueError(f"candidate report did not pass internal checks: {path}")
    if report.get("slam_supervision") is not False:
        raise ValueError(f"candidate report does not prove supervision is disabled: {path}")
    if report.get("external_ground_truth_used") is not False:
        raise ValueError(f"graph report does not prove GT independence: {path}")
    return report


def load_fusion_report(
    path: Path, trajectory: Path, graph_report: dict
) -> dict:
    report = load_json_report(path)
    if report.get("schema") != "umi_docker2_mast3r_complementary_v1":
        raise ValueError(f"unexpected fusion report schema: {path}")
    if report.get("result") != "PASS":
        raise ValueError(f"fusion candidate did not pass internal checks: {path}")
    if report.get("slam_supervision") is not False:
        raise ValueError(f"fusion candidate does not disable supervision: {path}")
    if report.get("external_ground_truth_used") is not False:
        raise ValueError(f"fusion candidate does not prove GT independence: {path}")
    reported_output = Path(report.get("output", "")).resolve()
    if reported_output != trajectory.resolve():
        raise ValueError(f"fusion report output does not match trajectory: {path}")
    graph_output_value = graph_report.get("output")
    if not isinstance(graph_output_value, str) or not graph_output_value:
        raise ValueError("graph report is missing its output trajectory")
    fusion_inputs = report.get("inputs")
    if not isinstance(fusion_inputs, dict):
        raise ValueError(f"fusion report inputs are missing: {path}")
    fusion_graph_input_value = fusion_inputs.get("mast3r_camera_trajectory")
    if not isinstance(fusion_graph_input_value, str) or not fusion_graph_input_value:
        raise ValueError(f"fusion report graph input is missing: {path}")
    graph_output = Path(graph_output_value).resolve()
    fusion_graph_input = Path(fusion_graph_input_value).resolve()
    if fusion_graph_input != graph_output:
        raise ValueError(f"fusion report does not consume graph report output: {path}")
    return report


def select_candidate(
    sparse_report: dict,
    tight_report: dict,
    max_scale_disagreement: float,
    min_tight_stereo_improvement: float,
) -> tuple[str, dict]:
    sparse_scale_disagreement = float(
        sparse_report["metric_scale_consistency"]["relative_difference"]
    )
    tight_scale_disagreement = float(
        tight_report["metric_scale_consistency"]["relative_difference"]
    )
    sparse_stereo_rmse = float(
        sparse_report["stereo_translation_fusion"]["stereo_edge_rmse_after_m"]
    )
    tight_stereo_rmse = float(
        tight_report["stereo_translation_fusion"]["stereo_edge_rmse_after_m"]
    )
    relative_alignment = sparse_report.get("relative_motion_alignment") or {}
    sparse_position_disagreement_p95_m = relative_alignment.get(
        "position_disagreement_p95_m"
    )
    if sparse_position_disagreement_p95_m is not None:
        sparse_position_disagreement_p95_m = float(
            sparse_position_disagreement_p95_m
        )
    values = (
        sparse_scale_disagreement,
        tight_scale_disagreement,
        sparse_stereo_rmse,
        tight_stereo_rmse,
        max_scale_disagreement,
        min_tight_stereo_improvement,
    )
    if not all(math.isfinite(value) for value in values):
        raise ValueError("candidate selection metrics must be finite")
    if (
        sparse_position_disagreement_p95_m is not None
        and not math.isfinite(sparse_position_disagreement_p95_m)
    ):
        raise ValueError("candidate selection metrics must be finite")
    if max_scale_disagreement < 0.0 or min_tight_stereo_improvement < 0.0:
        raise ValueError("candidate selection thresholds must be non-negative")
    if sparse_stereo_rmse <= 0.0 or tight_stereo_rmse <= 0.0:
        raise ValueError("stereo edge RMSE must be positive")
    tight_stereo_improvement = 1.0 - tight_stereo_rmse / sparse_stereo_rmse
    scale_is_observable = max(
        sparse_scale_disagreement, tight_scale_disagreement
    ) <= max_scale_disagreement
    maximum_tight_scale_regression = 0.020
    tight_scale_consistency_is_acceptable = (
        tight_scale_disagreement
        <= sparse_scale_disagreement + maximum_tight_scale_regression
    )
    tight_improves_geometry = (
        tight_stereo_improvement >= min_tight_stereo_improvement
    )
    tight_branch_is_observable = (
        sparse_position_disagreement_p95_m is None
        or sparse_position_disagreement_p95_m <= 0.050
    )
    sparse_branch_pressure = (
        sparse_position_disagreement_p95_m is not None
        and 0.015 <= sparse_position_disagreement_p95_m <= 0.050
        and sparse_scale_disagreement >= 0.030
        and sparse_stereo_rmse <= 0.004
        and tight_stereo_rmse <= 0.005
    )
    selected = (
        "tight"
        if scale_is_observable
        and tight_scale_consistency_is_acceptable
        and tight_branch_is_observable
        and (tight_improves_geometry or sparse_branch_pressure)
        else "sparse"
    )
    return selected, {
        "sparse_stereo_imu_scale_relative_difference": sparse_scale_disagreement,
        "tight_stereo_imu_scale_relative_difference": tight_scale_disagreement,
        "maximum_scale_relative_difference": max_scale_disagreement,
        "scale_is_observable": scale_is_observable,
        "maximum_tight_scale_regression": maximum_tight_scale_regression,
        "tight_scale_consistency_is_acceptable": (
            tight_scale_consistency_is_acceptable
        ),
        "sparse_stereo_edge_rmse_after_m": sparse_stereo_rmse,
        "tight_stereo_edge_rmse_after_m": tight_stereo_rmse,
        "tight_stereo_edge_rmse_improvement_ratio": tight_stereo_improvement,
        "minimum_tight_stereo_improvement_ratio": min_tight_stereo_improvement,
        "tight_improves_geometry": tight_improves_geometry,
        "tight_branch_is_observable": tight_branch_is_observable,
        "maximum_tight_branch_disagreement_m": 0.050,
        "sparse_position_disagreement_p95_m": (
            sparse_position_disagreement_p95_m
        ),
        "sparse_branch_pressure": sparse_branch_pressure,
        "sparse_branch_pressure_policy": (
            "tight motion keyframes when stereo/IMU scale differs by at least 3%, "
            "independent onboard trajectory disagreement is 15-50 mm, and both "
            "stereo graph residuals remain usable"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sparse-graph-report", type=Path, required=True)
    parser.add_argument("--tight-graph-report", type=Path, required=True)
    parser.add_argument("--sparse-fusion-report", type=Path, required=True)
    parser.add_argument("--tight-fusion-report", type=Path, required=True)
    parser.add_argument("--sparse-trajectory", type=Path, required=True)
    parser.add_argument("--tight-trajectory", type=Path, required=True)
    parser.add_argument("--max-scale-disagreement", type=float, default=0.15)
    parser.add_argument("--min-tight-stereo-improvement", type=float, default=0.10)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    sparse_report = load_graph_report(args.sparse_graph_report)
    tight_report = load_graph_report(args.tight_graph_report)
    selected, evidence = select_candidate(
        sparse_report,
        tight_report,
        args.max_scale_disagreement,
        args.min_tight_stereo_improvement,
    )
    trajectories = {
        "sparse": args.sparse_trajectory.resolve(),
        "tight": args.tight_trajectory.resolve(),
    }
    load_fusion_report(
        args.sparse_fusion_report, trajectories["sparse"], sparse_report
    )
    load_fusion_report(
        args.tight_fusion_report, trajectories["tight"], tight_report
    )
    selected_path = trajectories[selected]
    if not selected_path.is_file():
        raise FileNotFoundError(selected_path)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(selected_path, args.output)
    report = {
        "schema": "umi_mast3r_fusion_candidate_selection_v1",
        "result": "PASS",
        "slam_supervision": False,
        "external_ground_truth_used": False,
        "selection_policy": (
            "tight only when onboard stereo/IMU scale and the independent onboard "
            "trajectory branch are observable, tight does not materially regress "
            "stereo/IMU scale consistency, and either tight keyframes materially reduce "
            "stereo residual or branch pressure indicates insufficient sparse motion "
            "keyframes"
        ),
        "selected_candidate": selected,
        "evidence": evidence,
        "inputs": {
            "sparse_graph_report": str(args.sparse_graph_report.resolve()),
            "tight_graph_report": str(args.tight_graph_report.resolve()),
            "sparse_fusion_report": str(args.sparse_fusion_report.resolve()),
            "tight_fusion_report": str(args.tight_fusion_report.resolve()),
            "sparse_trajectory": str(trajectories["sparse"]),
            "tight_trajectory": str(trajectories["tight"]),
        },
        "output": str(args.output.resolve()),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
