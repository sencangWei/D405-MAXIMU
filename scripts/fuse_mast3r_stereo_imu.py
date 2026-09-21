#!/usr/bin/env python3
"""Fuse MASt3R, D405 stereo and the UMI 400 Hz IMU offline.

The estimator is deliberately self-contained: it consumes only UMI sensor
products and fixed calibration.  Robot/TCP and Lighthouse trajectories are
not accepted as inputs and remain evaluation-only.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.interpolate import PchipInterpolator
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix
from scipy.sparse.linalg import lsqr
from scipy.spatial.transform import Rotation, Slerp
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ego_vio.imu.vins_transform import load_vins_imu_rotation


IMU_DTYPE = np.dtype(
    [
        ("ts", "<f8"),
        ("counter", "<u4"),
        ("gx", "<f4"),
        ("gy", "<f4"),
        ("gz", "<f4"),
        ("ax", "<f4"),
        ("ay", "<f4"),
        ("az", "<f4"),
        ("temp", "<f4"),
    ]
)
POSE_FIELDS = ("t_sec", "x", "y", "z", "qw", "qx", "qy", "qz")
STANDARD_GRAVITY = 9.80665
MAX_ORIENTATION_CORRECTION_DEG = 2.0
ADAPTIVE_MAX_ORIENTATION_CORRECTION_DEG = 3.0
STRONG_CONSENSUS_MAX_ORIENTATION_CORRECTION_DEG = 4.0
VISUAL_ORIENTATION_SIGMA_DEG = 0.50
DOWNWEIGHTED_VISUAL_ORIENTATION_SIGMA_DEG = 100.0
IMU_ORIENTATION_SIGMA_DEG = 0.25
STEREO_ORIENTATION_SIGMA_DEG = 0.50
INCREMENTAL_STEREO_BLEND = 0.30
MAX_INCREMENTAL_POSITION_CORRECTION_M = 0.012
MAX_JOINT_POSITION_CORRECTION_M = 0.020
MAX_FULL_RATE_POSITION_CORRECTION_M = 0.012
MAX_TRAJECTORY_FRAME_ORIENTATION_CORRECTION_DEG = 2.0
MIN_TRAJECTORY_FRAME_DIRECTION_MEDIAN_IMPROVEMENT_RATIO = 0.005
AUTO_PROJECTED_MAX_SCALE_DISAGREEMENT_RATIO = 0.05
MAX_INERTIAL_RMSE_REGRESSION_RATIO = 0.01


def load_json_report(path: Path) -> dict:
    def reject_nonfinite(value: str):
        raise ValueError(f"non-finite JSON value {value} in {path}")

    report = json.loads(
        path.read_text(encoding="utf-8"), parse_constant=reject_nonfinite
    )
    if not isinstance(report, dict):
        raise ValueError(f"report must be a JSON object: {path}")
    return report


def relative_rmse_regression(before: float, after: float) -> float:
    if before <= 0.0 or not np.isfinite(before) or not np.isfinite(after):
        raise ValueError("RMSE values must be finite and the baseline must be positive")
    return after / before - 1.0


def validate_onboard_report(
    report: dict, path: Path, expected_schema: str
) -> None:
    if report.get("schema") != expected_schema:
        raise ValueError(f"unexpected report schema: {path}")
    if report.get("result") != "PASS":
        raise ValueError(f"input report did not pass: {path}")
    if report.get("slam_supervision") is not False:
        raise ValueError(f"input report does not disable supervision: {path}")
    if report.get("external_ground_truth_used") is not False:
        raise ValueError(f"input report does not prove GT independence: {path}")


def validate_relative_motion_report(
    report: dict,
    path: Path,
    trajectory: Path,
    session: Path,
    sample_count: int,
) -> dict:
    if report.get("schema") != "umi_docker2_run_acceptance_v1":
        raise ValueError(f"unexpected report schema: {path}")
    if report.get("slam_supervision") is not False:
        raise ValueError(f"input report does not disable supervision: {path}")
    if report.get("external_ground_truth_used") is not False:
        raise ValueError(f"input report does not prove GT independence: {path}")
    source_result = report.get("result")
    watchdog_failures = report.get("runtime_watchdog", {}).get("failures", [])
    degraded_raw_jump = (
        source_result == "FAIL"
        and report.get("failure_scope") == "SLAM"
        and report.get("runtime_error") is None
        and watchdog_failures == ["raw_trajectory_jump"]
    )
    if source_result != "PASS" and not degraded_raw_jump:
        raise ValueError(f"input report did not pass: {path}")
    if Path(report.get("session", "")).resolve() != session.resolve():
        raise ValueError("relative motion report session does not match fusion session")
    if Path(report.get("corrected_trajectory", "")).resolve() != trajectory.resolve():
        raise ValueError("relative motion report does not match trajectory")
    if int(report.get("corrected_odometry_samples", -1)) != sample_count:
        raise ValueError("relative motion report sample count does not match trajectory")
    return {
        "source_result": source_result,
        "source_failures": list(watchdog_failures),
        "policy": (
            "degraded_raw_jump_robust_downweight"
            if degraded_raw_jump
            else "accepted_passed_relative_odometry"
        ),
    }


def orientation_correction_limit(stereo_edges: list[dict]) -> tuple[float, str]:
    if not stereo_edges:
        return MAX_ORIENTATION_CORRECTION_DEG, "visual_imu_only"
    visual_p95 = float(
        np.percentile([edge["visual_error_deg"] for edge in stereo_edges], 95)
    )
    imu_p95 = float(
        np.percentile([edge["imu_error_deg"] for edge in stereo_edges], 95)
    )
    if imu_p95 <= 1.0 and visual_p95 - imu_p95 >= 0.3:
        return (
            STRONG_CONSENSUS_MAX_ORIENTATION_CORRECTION_DEG,
            "stereo_strongly_supports_imu",
        )
    return ADAPTIVE_MAX_ORIENTATION_CORRECTION_DEG, "stereo_consensus_standard"


def regular_node_indices(sample_count: int, stride: int) -> np.ndarray:
    if sample_count < 2:
        raise ValueError("at least two trajectory samples are required")
    if stride < 1:
        raise ValueError("orientation node stride must be positive")
    indices = np.arange(0, sample_count, stride, dtype=int)
    if indices[-1] != sample_count - 1:
        indices = np.append(indices, sample_count - 1)
    return indices


def interpolation_stencil(
    query_indices: np.ndarray, node_indices: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    query_indices = np.asarray(query_indices, dtype=int)
    node_indices = np.asarray(node_indices, dtype=int)
    if len(node_indices) < 2 or np.any(np.diff(node_indices) <= 0):
        raise ValueError("correction node indices must be strictly increasing")
    if np.any(query_indices < node_indices[0]) or np.any(
        query_indices > node_indices[-1]
    ):
        raise ValueError("correction nodes must cover every queried frame")
    right = np.searchsorted(node_indices, query_indices, side="left")
    exact = node_indices[right] == query_indices
    left = np.where(exact, right, right - 1)
    alpha = np.zeros(len(query_indices), dtype=float)
    between = ~exact
    alpha[between] = (
        query_indices[between] - node_indices[left[between]]
    ) / (node_indices[right[between]] - node_indices[left[between]])
    return left, right, alpha


def load_mast3r_keyframe_indices(
    keyframe_dir: Path, trajectory_times: np.ndarray
) -> tuple[np.ndarray, dict]:
    timestamps = []
    for path in keyframe_dir.glob("*.png"):
        try:
            timestamps.append(float(path.stem))
        except ValueError:
            continue
    if len(timestamps) < 2:
        raise ValueError("MASt3R keyframe directory has fewer than two timestamps")
    relative_times = np.asarray(trajectory_times, dtype=float) - trajectory_times[0]
    matched = []
    errors = []
    for timestamp in sorted(timestamps):
        right = int(np.searchsorted(relative_times, timestamp, side="left"))
        candidates = [min(right, len(relative_times) - 1)]
        if right > 0:
            candidates.append(right - 1)
        index = min(candidates, key=lambda item: abs(relative_times[item] - timestamp))
        matched.append(index)
        errors.append(abs(relative_times[index] - timestamp))
    max_error = float(max(errors))
    if max_error > 0.020:
        raise ValueError(
            f"MASt3R keyframe timestamps do not match trajectory: {max_error:.6f}s"
        )
    correction_nodes = np.asarray(
        sorted(set(matched) | {0, len(trajectory_times) - 1}), dtype=int
    )
    return correction_nodes, {
        "source": str(keyframe_dir.resolve()),
        "saved_keyframes": int(len(timestamps)),
        "matched_keyframes": int(len(set(matched))),
        "correction_nodes": int(len(correction_nodes)),
        "max_timestamp_match_error_s": max_error,
    }


def densify_correction_nodes(
    keyframe_indices: np.ndarray, sample_count: int, stride: int
) -> tuple[np.ndarray, dict]:
    keyframe_indices = np.asarray(keyframe_indices, dtype=int)
    regular_indices = regular_node_indices(sample_count, stride)
    correction_nodes = np.union1d(keyframe_indices, regular_indices)
    return correction_nodes, {
        "keyframe_nodes": int(len(keyframe_indices)),
        "regular_nodes": int(len(regular_indices)),
        "correction_nodes": int(len(correction_nodes)),
        "maximum_gap_frames": int(np.max(np.diff(correction_nodes))),
    }


def select_keyframe_correction_nodes(
    keyframe_indices: np.ndarray,
    sample_count: int,
    stride: int,
    *,
    keyframe_only: bool,
) -> tuple[np.ndarray, dict]:
    """Choose graph states without inventing unsupported visual nodes."""
    keyframe_indices = np.asarray(keyframe_indices, dtype=int)
    if not keyframe_only:
        return densify_correction_nodes(keyframe_indices, sample_count, stride)
    return keyframe_indices, {
        "keyframe_nodes": int(len(keyframe_indices)),
        "regular_nodes": 0,
        "correction_nodes": int(len(keyframe_indices)),
        "maximum_gap_frames": int(np.max(np.diff(keyframe_indices))),
    }


def cap_interpolated_position_corrections(
    node_corrections: np.ndarray,
    frame_left: np.ndarray,
    frame_right: np.ndarray,
    frame_alpha: np.ndarray,
    maximum_norm_m: float,
    mode: str,
    interpolation_mode: str = "linear",
    node_indices: np.ndarray | None = None,
    frame_indices: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Limit graph corrections while preserving the requested cap policy."""
    def interpolate(nodes: np.ndarray) -> np.ndarray:
        if interpolation_mode == "linear":
            return (
                (1.0 - frame_alpha[:, None]) * nodes[frame_left]
                + frame_alpha[:, None] * nodes[frame_right]
            )
        if interpolation_mode == "pchip":
            if node_indices is None or frame_indices is None:
                raise ValueError("pchip interpolation requires node and frame indices")
            return PchipInterpolator(node_indices, nodes, axis=0)(frame_indices)
        raise ValueError(
            f"unsupported correction interpolation mode: {interpolation_mode}"
        )

    requested = interpolate(node_corrections)
    requested_norm = np.linalg.norm(requested, axis=1)
    if mode == "global":
        minimum_scale = min(
            1.0,
            maximum_norm_m / max(float(np.max(requested_norm)), 1e-12),
        )
        return minimum_scale * requested, requested_norm, float(minimum_scale)
    if mode == "per-frame":
        scales = np.minimum(
            1.0, maximum_norm_m / np.maximum(requested_norm, 1e-12)
        )
        return scales[:, None] * requested, requested_norm, float(np.min(scales))
    if mode == "per-node":
        node_norm = np.linalg.norm(node_corrections, axis=1)
        node_scales = np.minimum(
            1.0, maximum_norm_m / np.maximum(node_norm, 1e-12)
        )
        capped_nodes = node_scales[:, None] * node_corrections
        capped = interpolate(capped_nodes)
        if interpolation_mode != "linear":
            capped_norm = np.linalg.norm(capped, axis=1)
            interpolation_scales = np.minimum(
                1.0, maximum_norm_m / np.maximum(capped_norm, 1e-12)
            )
            capped = interpolation_scales[:, None] * capped
        return capped, requested_norm, float(np.min(node_scales))
    raise ValueError(f"unsupported correction cap mode: {mode}")


def scale_consistency(
    imu_scale: float,
    stereo_scale: float,
    maximum_relative_difference: float = 0.15,
) -> dict:
    if imu_scale <= 0.0 or stereo_scale <= 0.0:
        raise ValueError("metric scale estimates must be positive")
    midpoint = 0.5 * (imu_scale + stereo_scale)
    relative_difference = abs(imu_scale - stereo_scale) / midpoint
    return {
        "imu_scale_m_per_mast3r_unit": float(imu_scale),
        "stereo_scale_m_per_mast3r_unit": float(stereo_scale),
        "joint_scale_m_per_mast3r_unit": float(
            np.sqrt(imu_scale * stereo_scale)
        ),
        "relative_difference": float(relative_difference),
        "maximum_relative_difference": float(maximum_relative_difference),
        "consistent": bool(relative_difference <= maximum_relative_difference),
    }


TOLERABLE_FAILURE_BY_POLICY = {
    # 尺度不一致降级为诊断（2026-09-22）。依据：09-11 Codex 明说的「尺度由双红外
    # 负责、IMU 只修姿态、冲突只作诊断」，此前只接进了 compare)
    # （--metric-scale-mode stereo）。**只摘这一个名字**，其余失败一律照旧阻断，
    # 且**不改尺度估计**（仍取 joint 对数均值）。见 README §33/§35。
    "fail": (),
    "diagnose": ("imu_stereo_metric_scale_disagreement",),
}


def blocking_failures(failures: list, policy: str) -> list:
    """按策略摘掉可容忍的失败名，得到真正的阻断集。默认策略下恒等于 failures。"""
    if policy not in TOLERABLE_FAILURE_BY_POLICY:
        raise ValueError(f"unknown scale disagreement policy: {policy}")
    tolerated = set(TOLERABLE_FAILURE_BY_POLICY[policy])
    return [name for name in failures if name not in tolerated]


def select_position_mode(
    requested_mode: str, metric_scale_quality: dict | None
) -> tuple[str, str]:
    if requested_mode != "auto":
        return requested_mode, "explicit_cli_selection"
    if metric_scale_quality is None:
        raise ValueError("automatic position fusion requires --imu-scale-report")
    relative_difference = float(metric_scale_quality["relative_difference"])
    if relative_difference <= AUTO_PROJECTED_MAX_SCALE_DISAGREEMENT_RATIO:
        return "projected", "stereo_imu_scale_consistent_preserve_visual_shape"
    return "joint-inertial", "stereo_imu_scale_disagreement_use_joint_inertial"


def align_orientations_to_translation_frame(
    positions: np.ndarray,
    camera_rotations: Rotation,
    observations: list[dict],
    min_displacement_m: float = 0.003,
    max_correction_deg: float = MAX_TRAJECTORY_FRAME_ORIENTATION_CORRECTION_DEG,
    min_median_improvement_ratio: float = (
        MIN_TRAJECTORY_FRAME_DIRECTION_MEDIAN_IMPROVEMENT_RATIO
    ),
) -> tuple[Rotation, dict]:
    """Estimate the constant world-frame attitude offset from stereo motion."""
    target_directions = []
    source_directions = []
    weights = []
    for observation in observations:
        if not observation.get("accepted"):
            continue
        if "metric_displacement_camera_i_m" not in observation:
            continue
        first = int(observation["first_index"])
        second = int(observation["second_index"])
        if not 0 <= first < second < len(positions):
            raise ValueError("stereo translation edge index lies outside trajectory")
        target = positions[second] - positions[first]
        source = camera_rotations[first].apply(
            np.asarray(observation["metric_displacement_camera_i_m"], dtype=float)
        )
        target_norm = float(np.linalg.norm(target))
        source_norm = float(np.linalg.norm(source))
        if min(target_norm, source_norm) < min_displacement_m:
            continue
        target_directions.append(target / target_norm)
        source_directions.append(source / source_norm)
        confidence = min(
            1.0, float(observation.get("pnp_inlier_ratio", 0.5)) / 0.8
        )
        weights.append(confidence * min(target_norm, 0.05))
    if len(target_directions) < 4:
        return camera_rotations, {
            "method": "robust_stereo_translation_direction_wahba",
            "accepted": False,
            "rejection_reason": "insufficient_edges",
            "edges": int(len(target_directions)),
            "inliers": int(len(target_directions)),
            "before_median_deg": None,
            "before_p95_deg": None,
            "after_median_deg": None,
            "after_p95_deg": None,
            "correction_rotvec_deg": [0.0, 0.0, 0.0],
            "correction_requested_deg": 0.0,
            "correction_applied_deg": 0.0,
            "correction_limit_deg": max_correction_deg,
            "correction_limit_scale": 1.0,
            "correction_applied_scale": 0.0,
            "correction_limited": False,
            "requested_after_median_deg": None,
            "median_improvement_ratio": None,
            "minimum_median_improvement_ratio": min_median_improvement_ratio,
            "external_ground_truth_used": False,
        }

    targets = np.asarray(target_directions)
    sources = np.asarray(source_directions)
    weights_array = np.asarray(weights)
    inliers = np.ones(len(targets), dtype=bool)
    requested_correction = Rotation.identity()
    for _ in range(4):
        requested_correction, _ = Rotation.align_vectors(
            targets[inliers], sources[inliers], weights=weights_array[inliers]
        )
        angular_errors_deg = np.degrees(
            np.arccos(
                np.clip(
                    np.sum(targets * requested_correction.apply(sources), axis=1),
                    -1.0,
                    1.0,
                )
            )
        )
        median = float(np.median(angular_errors_deg[inliers]))
        mad = float(np.median(np.abs(angular_errors_deg[inliers] - median)))
        updated = angular_errors_deg <= median + 3.0 * max(1.4826 * mad, 0.5)
        if np.count_nonzero(updated) < 4:
            break
        inliers = updated

    requested_deg = float(np.degrees(requested_correction.magnitude()))
    before_deg = np.degrees(
        np.arccos(np.clip(np.sum(targets * sources, axis=1), -1.0, 1.0))
    )
    requested_after_deg = np.degrees(
        np.arccos(
            np.clip(
                np.sum(
                    targets * requested_correction.apply(sources), axis=1
                ),
                -1.0,
                1.0,
            )
        )
    )
    before_median_deg = float(np.median(before_deg[inliers]))
    requested_after_median_deg = float(np.median(requested_after_deg[inliers]))
    median_improvement_ratio = (
        (before_median_deg - requested_after_median_deg)
        / max(before_median_deg, 1e-12)
    )
    observable = median_improvement_ratio >= min_median_improvement_ratio
    limit_scale = min(1.0, max_correction_deg / max(requested_deg, 1e-12))
    applied_scale = limit_scale if observable else 0.0
    correction = Rotation.from_rotvec(
        requested_correction.as_rotvec() * applied_scale
    )
    after_deg = np.degrees(
        np.arccos(
            np.clip(
                np.sum(targets * correction.apply(sources), axis=1), -1.0, 1.0
            )
        )
    )
    return correction * camera_rotations, {
        "method": "robust_stereo_translation_direction_wahba",
        "accepted": observable,
        "rejection_reason": None if observable else "insufficient_observability",
        "edges": int(len(targets)),
        "inliers": int(np.count_nonzero(inliers)),
        "before_median_deg": before_median_deg,
        "before_p95_deg": float(np.percentile(before_deg[inliers], 95)),
        "after_median_deg": float(np.median(after_deg[inliers])),
        "after_p95_deg": float(np.percentile(after_deg[inliers], 95)),
        "correction_rotvec_deg": np.degrees(correction.as_rotvec()).tolist(),
        "correction_requested_deg": requested_deg,
        "correction_applied_deg": float(np.degrees(correction.magnitude())),
        "correction_limit_deg": max_correction_deg,
        "correction_limit_scale": limit_scale,
        "correction_applied_scale": applied_scale,
        "correction_limited": limit_scale < 1.0,
        "requested_after_median_deg": requested_after_median_deg,
        "median_improvement_ratio": median_improvement_ratio,
        "minimum_median_improvement_ratio": min_median_improvement_ratio,
        "external_ground_truth_used": False,
    }


def load_trajectory(path: Path) -> tuple[np.ndarray, np.ndarray, Rotation, list[dict]]:
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    if not rows or not set(POSE_FIELDS).issubset(rows[0]):
        raise ValueError(f"invalid pose trajectory: {path}")
    times = np.asarray([float(row["t_sec"]) for row in rows])
    positions = np.asarray(
        [[float(row[key]) for key in ("x", "y", "z")] for row in rows]
    )
    quaternions = np.asarray(
        [[float(row[key]) for key in ("qx", "qy", "qz", "qw")] for row in rows]
    )
    if np.any(np.diff(times) <= 0):
        raise ValueError("trajectory timestamps must be strictly increasing")
    return times, positions, Rotation.from_quat(quaternions), rows


def rigid_align_positions(
    source: np.ndarray, target: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Return the proper rigid transform mapping source positions to target."""
    source_center = np.mean(source, axis=0)
    target_center = np.mean(target, axis=0)
    covariance = (source - source_center).T @ (target - target_center)
    u, _, vt = np.linalg.svd(covariance)
    rotation = vt.T @ u.T
    if np.linalg.det(rotation) < 0.0:
        vt[-1] *= -1.0
        rotation = vt.T @ u.T
    translation = target_center - rotation @ source_center
    return rotation, translation


def robust_rigid_align_positions(
    source: np.ndarray, target: np.ndarray, max_iterations: int = 8
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Fit an SE(3) position alignment while excluding gross trajectory spikes."""
    if len(source) != len(target) or len(source) < 3:
        raise ValueError("robust rigid alignment requires at least three paired positions")
    inliers = np.ones(len(source), dtype=bool)
    for _ in range(max_iterations):
        rotation, translation = rigid_align_positions(source[inliers], target[inliers])
        residual = np.linalg.norm(source @ rotation.T + translation - target, axis=1)
        median = float(np.median(residual))
        mad = float(np.median(np.abs(residual - median)))
        threshold = median + max(3.0 * 1.4826 * mad, 1e-9)
        updated = residual <= threshold
        if np.count_nonzero(updated) < 3 or np.array_equal(updated, inliers):
            break
        inliers = updated
    rotation, translation = rigid_align_positions(source[inliers], target[inliers])
    return rotation, translation, inliers


def align_relative_motion_positions(
    query_times: np.ndarray,
    target_positions: np.ndarray,
    reference_times: np.ndarray,
    reference_positions: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """Interpolate and rigidly align a second UMI odometry for relative factors."""
    valid = (query_times >= reference_times[0]) & (
        query_times <= reference_times[-1]
    )
    if np.count_nonzero(valid) < 2:
        raise ValueError("relative motion trajectory has insufficient timestamp overlap")
    interpolated = np.column_stack(
        [
            np.interp(query_times, reference_times, reference_positions[:, axis])
            for axis in range(3)
        ]
    )
    rotation, translation, alignment_inliers = robust_rigid_align_positions(
        interpolated[valid], target_positions[valid]
    )
    aligned = interpolated @ rotation.T + translation
    residual = np.linalg.norm(aligned[valid] - target_positions[valid], axis=1)
    inlier_residual = residual[alignment_inliers]
    return aligned, valid, {
        "alignment": "robust_SE3_relative_motion_to_visual_body_no_scale",
        "rotation": rotation.tolist(),
        "translation_m": translation.tolist(),
        "position_disagreement_median_m": float(np.median(residual)),
        "position_disagreement_p95_m": float(np.percentile(residual, 95)),
        "inlier_position_disagreement_median_m": float(np.median(inlier_residual)),
        "inlier_position_disagreement_p95_m": float(
            np.percentile(inlier_residual, 95)
        ),
        "alignment_inliers": int(np.count_nonzero(alignment_inliers)),
        "alignment_outliers": int(len(alignment_inliers) - np.count_nonzero(alignment_inliers)),
        "overlap_samples": int(np.count_nonzero(valid)),
        "overlap_ratio": float(np.mean(valid)),
    }


def select_visual_position_sigma(
    requested_sigma_m: float,
    relative_motion_alignment: dict | None,
    *,
    enabled: bool = False,
    trigger_disagreement_m: float = 0.050,
    weak_visual_sigma_m: float = 0.040,
) -> tuple[float, dict]:
    """Relax the absolute visual prior only for onboard branch disagreement."""
    if requested_sigma_m <= 0.0 or trigger_disagreement_m <= 0.0:
        raise ValueError("visual sigma and disagreement trigger must be positive")
    if weak_visual_sigma_m < requested_sigma_m:
        raise ValueError("weak visual sigma must not be smaller than requested sigma")
    disagreement_m = (
        float(relative_motion_alignment["position_disagreement_p95_m"])
        if relative_motion_alignment is not None
        else None
    )
    selected_sigma_m = requested_sigma_m
    reason = "disabled"
    if enabled:
        if disagreement_m is None:
            raise ValueError(
                "automatic visual sigma requires an aligned relative-motion trajectory"
            )
        if disagreement_m >= trigger_disagreement_m:
            selected_sigma_m = weak_visual_sigma_m
            reason = "independent_onboard_trajectory_branch_disagreement"
        else:
            reason = "independent_onboard_trajectories_consistent"
    return selected_sigma_m, {
        "enabled": enabled,
        "requested_sigma_m": requested_sigma_m,
        "selected_sigma_m": selected_sigma_m,
        "weak_visual_sigma_m": weak_visual_sigma_m,
        "position_disagreement_p95_m": disagreement_m,
        "trigger_disagreement_m": trigger_disagreement_m,
        "relaxed": selected_sigma_m > requested_sigma_m,
        "reason": reason,
        "external_ground_truth_used": False,
    }


def refine_positions_full_rate_imu(
    camera_positions: np.ndarray,
    camera_rotations: Rotation,
    visual_times_mono: np.ndarray,
    imu_times: np.ndarray,
    accel_body: np.ndarray,
    body_t_camera: np.ndarray,
    td_s: float,
    gravity_world: np.ndarray,
    max_correction_m: float = MAX_FULL_RATE_POSITION_CORRECTION_M,
    visual_sigma_base_m: float = 0.0015,
    visual_activation_gain: float = 5.0,
    dynamic_sigma_mps2: float = 0.75,
) -> tuple[np.ndarray, dict]:
    """Correct local visual spikes with full-rate IMU acceleration evidence."""
    if len(camera_positions) < 3:
        raise ValueError("full-rate IMU refinement requires at least three poses")
    if max_correction_m <= 0.0:
        raise ValueError("full-rate position correction limit must be positive")
    if visual_sigma_base_m <= 0.0 or dynamic_sigma_mps2 <= 0.0:
        raise ValueError("full-rate IMU sigmas must be positive")
    if visual_activation_gain < 0.0:
        raise ValueError("full-rate visual activation gain must be non-negative")
    body_from_camera = Rotation.from_matrix(body_t_camera[:3, :3])
    body_rotations = camera_rotations * body_from_camera.inv()
    body_positions = camera_positions - body_rotations.apply(body_t_camera[:3, 3])
    imu_rate_hz = 1.0 / float(np.median(np.diff(imu_times)))
    filtered_accel = gaussian_filter1d(
        accel_body,
        sigma=max(1.0, 0.025 * imu_rate_hz),
        axis=0,
        mode="nearest",
    )
    query_times = visual_times_mono + td_s
    interpolated_accel = np.column_stack(
        [
            np.interp(query_times, imu_times, filtered_accel[:, axis])
            for axis in range(3)
        ]
    )
    target_acceleration = body_rotations.apply(interpolated_accel) + gravity_world
    dt_before = visual_times_mono[1:-1] - visual_times_mono[:-2]
    dt_after = visual_times_mono[2:] - visual_times_mono[1:-1]
    if np.any(dt_before <= 0.0) or np.any(dt_after <= 0.0):
        raise ValueError("visual timestamps must be strictly increasing")
    interval_sum = dt_before + dt_after
    coefficient_before = 2.0 / (dt_before * interval_sum)
    coefficient_after = 2.0 / (dt_after * interval_sum)
    coefficient_center = -(coefficient_before + coefficient_after)
    visual_acceleration = (
        coefficient_before[:, None] * body_positions[:-2]
        + coefficient_center[:, None] * body_positions[1:-1]
        + coefficient_after[:, None] * body_positions[2:]
    )
    target_center = target_acceleration[1:-1]
    inconsistency = np.linalg.norm(visual_acceleration - target_center, axis=1)
    frame_inconsistency = np.zeros(len(body_positions))
    frame_inconsistency[1:-1] = inconsistency
    activation = 1.0 / (1.0 + np.exp(-(frame_inconsistency - 2.0) / 0.5))
    visual_sigma_m = visual_sigma_base_m * (
        1.0 + visual_activation_gain * activation
    )
    anchor_sigma_m = 0.0001
    dynamic_weights = np.ones(len(body_positions) - 2)

    def build_system() -> tuple:
        row_count = 3 * len(body_positions) + 6 + 3 * len(dynamic_weights)
        unknowns = 3 * len(body_positions)
        design = lil_matrix((row_count, unknowns), dtype=float)
        target = np.zeros(row_count)
        row = 0
        for index, sigma in enumerate(visual_sigma_m):
            design[row : row + 3, 3 * index : 3 * index + 3] = (
                np.eye(3) / sigma
            )
            row += 3
        design[row : row + 3, :3] = np.eye(3) / anchor_sigma_m
        row += 3
        design[row : row + 3, -3:] = np.eye(3) / anchor_sigma_m
        row += 3
        for index, weight_value in enumerate(dynamic_weights, start=1):
            scale = np.sqrt(weight_value) / dynamic_sigma_mps2
            design[row : row + 3, 3 * (index - 1) : 3 * index] = (
                coefficient_before[index - 1] * scale * np.eye(3)
            )
            design[row : row + 3, 3 * index : 3 * (index + 1)] = (
                coefficient_center[index - 1] * scale * np.eye(3)
            )
            design[row : row + 3, 3 * (index + 1) : 3 * (index + 2)] = (
                coefficient_after[index - 1] * scale * np.eye(3)
            )
            target[row : row + 3] = (
                target_center[index - 1] - visual_acceleration[index - 1]
            ) * scale
            row += 3
        return design.tocsr(), target

    correction = np.zeros_like(body_positions)
    for _ in range(4):
        design, target = build_system()
        correction = lsqr(
            design, target, atol=1e-10, btol=1e-10, iter_lim=5000
        )[0].reshape(-1, 3)
        corrected_acceleration = (
            visual_acceleration
            + coefficient_before[:, None] * correction[:-2]
            + coefficient_center[:, None] * correction[1:-1]
            + coefficient_after[:, None] * correction[2:]
        )
        residual = np.linalg.norm(corrected_acceleration - target_center, axis=1)
        dynamic_weights = np.minimum(1.0, 1.5 / np.maximum(residual, 1e-12))

    requested_norm = np.linalg.norm(correction, axis=1)
    correction_scale = min(
        1.0, max_correction_m / max(float(np.max(requested_norm)), 1e-12)
    )
    correction *= correction_scale
    corrected_acceleration = (
        visual_acceleration
        + coefficient_before[:, None] * correction[:-2]
        + coefficient_center[:, None] * correction[1:-1]
        + coefficient_after[:, None] * correction[2:]
    )
    before = np.linalg.norm(visual_acceleration - target_center, axis=1)
    after = np.linalg.norm(corrected_acceleration - target_center, axis=1)
    correction_norm = np.linalg.norm(correction, axis=1)
    return camera_positions + correction, {
        "mode": "full_rate_imu_acceleration_robust_position_refinement",
        "frames": int(len(camera_positions)),
        "imu_rate_hz": float(imu_rate_hz),
        "visual_downweighted_frames": int(np.count_nonzero(activation >= 0.5)),
        "acceleration_residual_before_p95_mps2": float(np.percentile(before, 95)),
        "acceleration_residual_after_p95_mps2": float(np.percentile(after, 95)),
        "acceleration_residual_before_max_mps2": float(np.max(before)),
        "acceleration_residual_after_max_mps2": float(np.max(after)),
        "correction_requested_max_m": float(np.max(requested_norm)),
        "correction_median_m": float(np.median(correction_norm)),
        "correction_p95_m": float(np.percentile(correction_norm, 95)),
        "correction_max_m": float(np.max(correction_norm)),
        "correction_limit_m": float(max_correction_m),
        "correction_scale": float(correction_scale),
        "robust_dynamic_inliers": int(np.count_nonzero(dynamic_weights >= 0.5)),
        "visual_sigma_base_m": float(visual_sigma_base_m),
        "visual_activation_gain": float(visual_activation_gain),
        "dynamic_sigma_mps2": float(dynamic_sigma_mps2),
    }


def camera_epoch_to_monotonic(
    frame_csv: Path, stream: str, query_epoch: np.ndarray
) -> np.ndarray:
    rows = list(csv.DictReader(frame_csv.open(newline="", encoding="utf-8")))
    epoch_key = f"{stream}_device_ms"
    mono_key = f"{stream}_mono"
    pairs = [
        (float(row[epoch_key]) / 1000.0, float(row[mono_key]))
        for row in rows
        if row.get(epoch_key) and row.get(mono_key)
    ]
    if len(pairs) < 2:
        raise ValueError(f"insufficient {stream} timestamps in {frame_csv}")
    epoch, monotonic = np.asarray(pairs).T
    if query_epoch[0] < epoch[0] or query_epoch[-1] > epoch[-1]:
        raise ValueError("trajectory lies outside D405 timestamp table")
    return np.interp(query_epoch, epoch, monotonic)


def load_vins_config(path: Path, expected_td_s: float | None) -> dict:
    storage = cv2.FileStorage(str(path), cv2.FileStorage_READ)
    body_t_camera = storage.getNode("body_T_cam0").mat()
    td_s = float(storage.getNode("td").real())
    estimate_td = int(round(storage.getNode("estimate_td").real()))
    storage.release()
    if body_t_camera is None or body_t_camera.shape != (4, 4):
        raise ValueError(f"missing body_T_cam0 in {path}")
    if estimate_td != 0:
        raise ValueError("offline fusion requires fixed estimate_td=0")
    if expected_td_s is not None and abs(td_s - expected_td_s) > 1e-9:
        raise ValueError(
            f"wrong Docker2 td: config={td_s:.9f}, expected={expected_td_s:.9f}"
        )
    return {"body_T_camera": body_t_camera, "td_s": td_s, "estimate_td": estimate_td}


def body_t_trajectory_camera_from_stereo_report(
    body_t_left_ir: np.ndarray, stereo_report: dict
) -> np.ndarray:
    """Return the body extrinsic for the camera frame used by MASt3R."""
    observation_frame = stereo_report.get("observation_frame")
    if observation_frame == "infrared_left_camera_i":
        return body_t_left_ir.copy()
    if observation_frame != "color_camera_i":
        raise ValueError(
            "stereo observations must identify the MASt3R camera frame"
        )
    calibration = stereo_report.get("factory_stereo_calibration", {})
    color_rotation_from_left = np.asarray(
        calibration.get("color_rotation_from_left"), dtype=float
    )
    color_translation_from_left = np.asarray(
        calibration.get("color_translation_from_left_m"), dtype=float
    )
    if color_rotation_from_left.shape != (3, 3):
        raise ValueError("stereo report lacks the factory left-IR to color rotation")
    if color_translation_from_left.shape != (3,):
        raise ValueError("stereo report lacks the factory left-IR to color translation")
    color_t_left = np.eye(4)
    color_t_left[:3, :3] = color_rotation_from_left
    color_t_left[:3, 3] = color_translation_from_left
    return body_t_left_ir @ np.linalg.inv(color_t_left)


def body_t_color_from_stereo_report(
    body_t_left_ir: np.ndarray, stereo_report: dict
) -> np.ndarray:
    """Backward-compatible RGB-specific wrapper."""
    if stereo_report.get("observation_frame") != "color_camera_i":
        raise ValueError(
            "stereo observations must be expressed in the MASt3R color camera frame"
        )
    return body_t_trajectory_camera_from_stereo_report(
        body_t_left_ir, stereo_report
    )


def load_calibrated_imu(
    imu_bin: Path, calibration_path: Path
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    records = np.fromfile(imu_bin, dtype=IMU_DTYPE)
    if len(records) < 20 or np.any(np.diff(records["ts"]) <= 0):
        raise ValueError(f"invalid or non-monotonic IMU stream: {imu_bin}")
    calibration = yaml.safe_load(calibration_path.read_text(encoding="utf-8"))
    acceptance = calibration.get("acceptance", {})
    if acceptance.get("status") == "FAIL" or acceptance.get("runtime_applied") is False:
        raise ValueError(f"IMU calibration failed runtime gate: {calibration_path}")
    accel_cal = calibration["accelerometer"]
    gyro_cal = calibration["gyroscope"]
    accel_matrix = np.asarray(accel_cal["matrix"], dtype=float)
    accel_offset = np.asarray(accel_cal["offset_g"], dtype=float)
    gyro_matrix = np.asarray(gyro_cal["matrix"], dtype=float)
    gyro_bias = np.asarray(gyro_cal["bias_deg_s"], dtype=float)
    raw_accel = np.column_stack(
        [records[name].astype(float) for name in ("ax", "ay", "az")]
    )
    raw_gyro = np.column_stack(
        [records[name].astype(float) for name in ("gx", "gy", "gz")]
    )
    accel = (raw_accel @ accel_matrix.T + accel_offset) * STANDARD_GRAVITY
    gyro = np.radians((raw_gyro - gyro_bias) @ gyro_matrix.T)
    # The formal body_T_cam0 is calibrated against the same runtime IMU frame
    # published to VINS, so reproduce that fixed axis transform exactly once.
    level_from_imu = load_vins_imu_rotation()
    accel = accel @ level_from_imu.T
    gyro = gyro @ level_from_imu.T
    return records["ts"].astype(float), gyro, accel, {
        "calibration_id": calibration.get("calibration_id", calibration_path.stem),
        "runtime_axis_rotation": level_from_imu.tolist(),
        "samples": int(len(records)),
        "median_rate_hz": float(1.0 / np.median(np.diff(records["ts"]))),
    }


def integrate_gyro(
    imu_times: np.ndarray,
    gyro_body: np.ndarray,
    start_s: float,
    end_s: float,
) -> Rotation:
    if not imu_times[0] <= start_s < end_s <= imu_times[-1]:
        raise ValueError("gyro integration interval lies outside IMU coverage")
    inside = imu_times[(imu_times > start_s) & (imu_times < end_s)]
    grid = np.concatenate(([start_s], inside, [end_s]))
    angular_rate = np.column_stack(
        [np.interp(grid, imu_times, gyro_body[:, axis]) for axis in range(3)]
    )
    delta = Rotation.identity()
    for index, dt in enumerate(np.diff(grid)):
        average_rate = 0.5 * (angular_rate[index] + angular_rate[index + 1])
        delta = delta * Rotation.from_rotvec(average_rate * dt)
    return delta


def refine_orientations(
    visual_rotations_camera: Rotation,
    node_indices: np.ndarray,
    visual_times_mono: np.ndarray,
    imu_times: np.ndarray,
    gyro_body: np.ndarray,
    body_from_camera: Rotation,
    td_s: float,
    stereo_observations: list[dict] | None = None,
) -> tuple[Rotation, dict]:
    body_rotations = visual_rotations_camera * body_from_camera.inv()
    imu_deltas = [
        integrate_gyro(
            imu_times,
            gyro_body,
            visual_times_mono[first] + td_s,
            visual_times_mono[second] + td_s,
        )
        for first, second in zip(node_indices[:-1], node_indices[1:])
    ]
    visual_deltas = [
        body_rotations[first].inv() * body_rotations[second]
        for first, second in zip(node_indices[:-1], node_indices[1:])
    ]
    before_deg = np.degrees(
        [
            (imu_delta.inv() * visual_delta).magnitude()
            for imu_delta, visual_delta in zip(imu_deltas, visual_deltas)
        ]
    )
    node_body = body_rotations[node_indices]
    node_count = len(node_indices)
    visual_sigma = np.full(
        node_count, np.radians(VISUAL_ORIENTATION_SIGMA_DEG)
    )
    imu_sigma = np.radians(IMU_ORIENTATION_SIGMA_DEG)
    stereo_sigma = np.radians(STEREO_ORIENTATION_SIGMA_DEG)
    stereo_edges = []
    downweighted_nodes = np.zeros(node_count, dtype=bool)

    def interpolation_spec(frame_index: int) -> tuple[int, int, float]:
        right = int(np.searchsorted(node_indices, frame_index, side="right"))
        right = min(max(right, 1), node_count - 1)
        left = right - 1
        fraction = (frame_index - node_indices[left]) / float(
            node_indices[right] - node_indices[left]
        )
        return left, right, fraction

    for observation in stereo_observations or []:
        if not observation.get("accepted"):
            continue
        if "pnp_rotation_quaternion_xyzw" not in observation:
            continue
        first = int(observation["first_index"])
        second = int(observation["second_index"])
        if not 0 <= first < second < len(visual_times_mono):
            raise ValueError("stereo rotation edge index lies outside trajectory")
        pnp_rotation = Rotation.from_quat(
            observation["pnp_rotation_quaternion_xyzw"]
        )
        edge_imu_delta = integrate_gyro(
            imu_times,
            gyro_body,
            visual_times_mono[first] + td_s,
            visual_times_mono[second] + td_s,
        )
        visual_pnp_rotation = (
            visual_rotations_camera[second].inv()
            * visual_rotations_camera[first]
        )
        imu_pnp_rotation = (
            body_from_camera.inv()
            * edge_imu_delta.inv()
            * body_from_camera
        )
        visual_error_deg = float(
            np.degrees((pnp_rotation.inv() * visual_pnp_rotation).magnitude())
        )
        imu_error_deg = float(
            np.degrees((pnp_rotation.inv() * imu_pnp_rotation).magnitude())
        )
        nearest_node = int(
            np.argmin(np.abs(node_indices - (first + second) / 2.0))
        )
        if imu_error_deg <= visual_error_deg:
            downweighted_nodes[nearest_node] = True
        stereo_edges.append(
            {
                "first": first,
                "second": second,
                "first_spec": interpolation_spec(first),
                "second_spec": interpolation_spec(second),
                "pnp_rotation": pnp_rotation,
                "confidence": float(observation.get("pnp_inlier_ratio", 0.5)),
                "visual_error_deg": visual_error_deg,
                "imu_error_deg": imu_error_deg,
            }
        )

    if np.any(downweighted_nodes):
        preferred = np.flatnonzero(downweighted_nodes)
        downweighted_nodes[
            np.maximum(preferred - 1, 0)
        ] = True
        downweighted_nodes[
            np.minimum(preferred + 1, node_count - 1)
        ] = True
        visual_sigma[downweighted_nodes] = np.radians(
            DOWNWEIGHTED_VISUAL_ORIENTATION_SIGMA_DEG
        )

    def interpolated_correction(
        corrections: np.ndarray, spec: tuple[int, int, float]
    ) -> np.ndarray:
        left, right, fraction = spec
        return (1.0 - fraction) * corrections[left] + fraction * corrections[right]

    def residual(flat: np.ndarray) -> np.ndarray:
        corrections = flat.reshape(-1, 3)
        corrected = node_body * Rotation.from_rotvec(corrections)
        values = [(corrections / visual_sigma[:, None]).ravel()]
        for index, imu_delta in enumerate(imu_deltas):
            predicted = corrected[index].inv() * corrected[index + 1]
            values.append((imu_delta.inv() * predicted).as_rotvec() / imu_sigma)
        for edge in stereo_edges:
            first_rotation = (
                body_rotations[edge["first"]]
                * Rotation.from_rotvec(
                    interpolated_correction(corrections, edge["first_spec"])
                )
                * body_from_camera
            )
            second_rotation = (
                body_rotations[edge["second"]]
                * Rotation.from_rotvec(
                    interpolated_correction(corrections, edge["second_spec"])
                )
                * body_from_camera
            )
            predicted_pnp = second_rotation.inv() * first_rotation
            weight = np.sqrt(max(edge["confidence"], 0.1)) / stereo_sigma
            values.append(
                (edge["pnp_rotation"].inv() * predicted_pnp).as_rotvec() * weight
            )
        values.append(corrections[0] / np.radians(0.02))
        return np.concatenate(values)

    residual_rows = 6 * node_count + 3 * len(stereo_edges)
    jacobian_sparsity = lil_matrix(
        (residual_rows, 3 * node_count), dtype=int
    )
    jacobian_sparsity[: 3 * node_count, : 3 * node_count] = np.eye(
        3 * node_count, dtype=int
    )
    for index in range(node_count - 1):
        row = 3 * node_count + 3 * index
        jacobian_sparsity[row : row + 3, 3 * index : 3 * index + 6] = 1
    stereo_row = 3 * node_count + 3 * (node_count - 1)
    for edge_index, edge in enumerate(stereo_edges):
        row = stereo_row + 3 * edge_index
        involved_nodes = {
            edge["first_spec"][0],
            edge["first_spec"][1],
            edge["second_spec"][0],
            edge["second_spec"][1],
        }
        for node in involved_nodes:
            jacobian_sparsity[row : row + 3, 3 * node : 3 * node + 3] = 1
    anchor_row = stereo_row + 3 * len(stereo_edges)
    jacobian_sparsity[anchor_row : anchor_row + 3, :3] = 1
    fit = least_squares(
        residual,
        np.zeros(3 * len(node_indices)),
        loss="soft_l1",
        f_scale=1.0,
        jac_sparsity=jacobian_sparsity.tocsr(),
        max_nfev=1000,
    )
    requested_node_corrections = fit.x.reshape(-1, 3)
    requested_max_rad = float(
        np.max(np.linalg.norm(requested_node_corrections, axis=1))
    )
    correction_limit_deg, correction_limit_policy = orientation_correction_limit(
        stereo_edges
    )
    maximum_correction_rad = np.radians(correction_limit_deg)
    correction_scale = min(
        1.0, maximum_correction_rad / max(requested_max_rad, np.finfo(float).eps)
    )
    node_corrections = requested_node_corrections * correction_scale
    correction_components = np.column_stack(
        [
            np.interp(
                np.arange(len(visual_times_mono)), node_indices, node_corrections[:, axis]
            )
            for axis in range(3)
        ]
    )
    corrected_body = body_rotations * Rotation.from_rotvec(correction_components)
    corrected_camera = corrected_body * body_from_camera
    corrected_nodes = corrected_body[node_indices]
    after_deg = np.degrees(
        [
            (
                imu_delta.inv()
                * (corrected_nodes[index].inv() * corrected_nodes[index + 1])
            ).magnitude()
            for index, imu_delta in enumerate(imu_deltas)
        ]
    )
    stereo_after_deg = []
    for edge in stereo_edges:
        first_rotation = corrected_camera[edge["first"]]
        second_rotation = corrected_camera[edge["second"]]
        predicted_pnp = second_rotation.inv() * first_rotation
        stereo_after_deg.append(
            np.degrees(
                (edge["pnp_rotation"].inv() * predicted_pnp).magnitude()
            )
        )
    correction_deg = np.degrees(np.linalg.norm(correction_components, axis=1))
    return corrected_camera, {
        "windows": int(len(imu_deltas)),
        "before_median_deg": float(np.median(before_deg)),
        "before_p95_deg": float(np.percentile(before_deg, 95)),
        "after_median_deg": float(np.median(after_deg)),
        "after_p95_deg": float(np.percentile(after_deg, 95)),
        "orientation_correction_requested_max_deg": float(
            np.degrees(requested_max_rad)
        ),
        "orientation_correction_scale": float(correction_scale),
        "orientation_correction_median_deg": float(np.median(correction_deg)),
        "orientation_correction_max_deg": float(np.max(correction_deg)),
        "orientation_correction_limit_deg": correction_limit_deg,
        "orientation_correction_limit_policy": correction_limit_policy,
        "stereo_rotation_edges": int(len(stereo_edges)),
        "visual_downweighted_nodes": int(np.count_nonzero(downweighted_nodes)),
        "stereo_visual_before_median_deg": (
            float(np.median([edge["visual_error_deg"] for edge in stereo_edges]))
            if stereo_edges
            else None
        ),
        "stereo_visual_before_p95_deg": (
            float(
                np.percentile(
                    [edge["visual_error_deg"] for edge in stereo_edges], 95
                )
            )
            if stereo_edges
            else None
        ),
        "stereo_imu_before_median_deg": (
            float(np.median([edge["imu_error_deg"] for edge in stereo_edges]))
            if stereo_edges
            else None
        ),
        "stereo_imu_before_p95_deg": (
            float(
                np.percentile(
                    [edge["imu_error_deg"] for edge in stereo_edges], 95
                )
            )
            if stereo_edges
            else None
        ),
        "stereo_rotation_after_median_deg": (
            float(np.median(stereo_after_deg)) if stereo_after_deg else None
        ),
        "stereo_rotation_after_p95_deg": (
            float(np.percentile(stereo_after_deg, 95)) if stereo_after_deg else None
        ),
        "optimizer_success": bool(fit.success),
        "optimizer_status": int(fit.status),
        "optimizer_message": str(fit.message),
        "optimizer_nfev": int(fit.nfev),
        "optimizer_cost": float(fit.cost),
        "optimizer_optimality": float(fit.optimality),
    }


def accepted_stereo_edges(stereo_report: dict) -> list[dict]:
    edges = []
    for observation in stereo_report.get("observations", []):
        if not observation.get("accepted"):
            continue
        if "metric_displacement_camera_i_m" not in observation:
            raise ValueError(
                "stereo report predates local displacement output; rerun stereo scale"
            )
        edges.append(observation)
    if len(edges) < 4:
        raise ValueError("insufficient accepted stereo translation edges")
    return edges


def filter_stereo_observations(
    observations: list[dict], minimum_sample_hop: int
) -> list[dict]:
    if minimum_sample_hop < 1:
        raise ValueError("minimum stereo sample hop must be positive")
    return [
        observation
        for observation in observations
        if int(observation.get("sample_hop", 1)) >= minimum_sample_hop
    ]


def merge_stereo_reports(primary: dict, additions: list[dict]) -> dict:
    """Combine independently sampled edges from the same UMI recording."""
    merged = dict(primary)
    observations = list(primary.get("observations", []))
    sources = [primary.get("report_path")]
    primary_scale = float(primary["scale_m_per_mast3r_unit"])
    primary_baseline = float(
        primary["factory_stereo_calibration"]["baseline_m"]
    )
    for addition in additions:
        if addition.get("session") != primary.get("session"):
            raise ValueError("stereo reports come from different sessions")
        if addition.get("trajectory") != primary.get("trajectory"):
            raise ValueError("stereo reports use different source trajectories")
        if addition.get("observation_frame") != primary.get("observation_frame"):
            raise ValueError("stereo reports use different observation frames")
        baseline = float(addition["factory_stereo_calibration"]["baseline_m"])
        if abs(baseline - primary_baseline) > 1e-9:
            raise ValueError("stereo reports use different factory baselines")
        scale = float(addition["scale_m_per_mast3r_unit"])
        relative_scale_difference = abs(scale - primary_scale) / (
            0.5 * (scale + primary_scale)
        )
        if relative_scale_difference > 0.05:
            raise ValueError("stereo report scales disagree by more than 5%")
        observations.extend(addition.get("observations", []))
        sources.append(addition.get("report_path"))
    merged["observations"] = observations
    merged["merged_report_paths"] = sources
    merged["merged_report_count"] = 1 + len(additions)
    return merged


def stereo_observation_confidence(
    observation: dict, reference_scale: float
) -> float:
    """Continuous UMI-only confidence for a stereo motion edge."""
    inlier_ratio = float(observation.get("pnp_inlier_ratio", 0.5))
    rotation_error_deg = float(observation.get("rotation_error_deg", 3.0))
    scale = float(observation.get("scale", reference_scale))
    scale_log_error = abs(np.log(max(scale, 1e-9) / reference_scale))
    bidirectional_value = observation.get(
        "bidirectional_relative_disagreement", 0.0
    )
    bidirectional_error = float(
        0.0 if bidirectional_value is None else bidirectional_value
    )
    rotation_quality = 1.0 / (1.0 + (rotation_error_deg / 1.5) ** 2)
    scale_quality = np.exp(-0.5 * (scale_log_error / 0.15) ** 2)
    bidirectional_quality = np.exp(
        -0.5 * (bidirectional_error / 0.15) ** 2
    )
    return float(
        np.clip(
            inlier_ratio
            * rotation_quality
            * scale_quality
            * bidirectional_quality,
            0.05,
            1.0,
        )
    )


def condition_learned_stereo_report(
    report: dict,
    reference_scale: float,
    minimum_confidence: float,
    translation_only: bool,
) -> dict:
    """Apply extra gates only to optional MASt3R-correspondence reports."""
    if report.get("correspondence_estimator") != "mast3r":
        return report
    conditioned = dict(report)
    observations = []
    rejected = 0
    for source in report.get("observations", []):
        observation = dict(source)
        if observation.get("accepted"):
            confidence = stereo_observation_confidence(
                observation, reference_scale
            )
            observation["learned_stereo_confidence"] = confidence
            if confidence < minimum_confidence:
                observation["accepted"] = False
                observation["reason"] = "learned_stereo_confidence_low"
                rejected += 1
            elif translation_only:
                observation.pop("pnp_rotation_quaternion_xyzw", None)
        observations.append(observation)
    conditioned["observations"] = observations
    conditioned["learned_stereo_conditioning"] = {
        "minimum_confidence": minimum_confidence,
        "translation_only": translation_only,
        "rejected_observations": rejected,
    }
    return conditioned


def condition_fixed_rotation_stereo_report(
    report: dict,
    maximum_free_rotation_delta_deg: float,
    maximum_reprojection_p95_px: float,
) -> dict:
    """Reject fixed-rotation PnP edges that contradict their image evidence."""
    if report.get("pnp_rotation_mode") != "trajectory-fixed":
        return report
    conditioned = dict(report)
    observations = []
    rejected_rotation = 0
    rejected_reprojection = 0
    for source in report.get("observations", []):
        observation = dict(source)
        if observation.get("accepted"):
            free_rotation_delta = float(
                observation.get("pnp_free_rotation_delta_deg", np.inf)
            )
            reprojection_p95 = float(
                observation.get("pnp_reprojection_p95_px", np.inf)
            )
            if free_rotation_delta > maximum_free_rotation_delta_deg:
                observation["accepted"] = False
                observation["reason"] = "fixed_pnp_rotation_delta_high"
                rejected_rotation += 1
            elif reprojection_p95 > maximum_reprojection_p95_px:
                observation["accepted"] = False
                observation["reason"] = "fixed_pnp_reprojection_high"
                rejected_reprojection += 1
        observations.append(observation)
    conditioned["observations"] = observations
    conditioned["fixed_rotation_conditioning"] = {
        "maximum_free_rotation_delta_deg": maximum_free_rotation_delta_deg,
        "maximum_reprojection_p95_px": maximum_reprojection_p95_px,
        "rejected_rotation": rejected_rotation,
        "rejected_reprojection": rejected_reprojection,
    }
    return conditioned


def local_stereo_scale_state(
    observations: list[dict],
    query_frame_indices: np.ndarray,
    radius_frames: float = 45.0,
    reference_scale: float | None = None,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """Estimate locally consistent stereo scale and visual-prior confidence.

    A coherent local scale change is evidence that the visual trajectory has
    warped, not that every stereo edge in the window is an outlier. Scattered
    scale measurements remain low-confidence and do not relax the visual prior.
    """
    valid = [
        observation
        for observation in observations
        if observation.get("accepted")
        and float(observation.get("scale", 0.0)) > 0.0
    ]
    if len(valid) < 5:
        query_count = len(query_frame_indices)
        return (
            np.ones(query_count),
            np.ones(query_count),
            {
                "observations": int(len(valid)),
                "downweighted_queries": 0,
                "visual_prior_weight_min": 1.0,
                "visual_prior_weight_median": 1.0,
                "local_scale_log_deviation_p95": 0.0,
                "local_scale_relative_span_p95": 0.0,
            },
        )
    midpoints = np.asarray(
        [
            0.5
            * (
                float(observation["first_index"])
                + float(observation["second_index"])
            )
            for observation in valid
        ]
    )
    scales = np.asarray([float(observation["scale"]) for observation in valid])
    global_scale = (
        float(reference_scale)
        if reference_scale is not None
        else float(np.median(scales))
    )
    if global_scale <= 0.0:
        raise ValueError("reference stereo scale must be positive")
    local_references = np.full(len(query_frame_indices), global_scale)
    visual_weights = np.ones(len(query_frame_indices))
    local_deviations = np.zeros(len(query_frame_indices))
    local_spans = np.zeros(len(query_frame_indices))
    for index, frame_index in enumerate(query_frame_indices):
        nearby = np.abs(midpoints - float(frame_index)) <= radius_frames
        if np.count_nonzero(nearby) < 5:
            continue
        local_scales = scales[nearby]
        local_scale = float(np.median(local_scales))
        relative_span = float(
            (np.percentile(local_scales, 90) - np.percentile(local_scales, 10))
            / max(local_scale, 1e-9)
        )
        consensus = float(np.exp(-0.5 * (relative_span / 0.35) ** 2))
        log_deviation = abs(np.log(local_scale / global_scale))
        deviation_activation = float(
            np.clip((log_deviation - 0.04) / 0.08, 0.0, 1.0)
        )
        local_references[index] = float(
            np.exp(
                consensus * np.log(local_scale)
                + (1.0 - consensus) * np.log(global_scale)
            )
        )
        visual_weights[index] = float(
            np.clip(1.0 - 0.85 * consensus * deviation_activation, 0.15, 1.0)
        )
        local_deviations[index] = log_deviation
        local_spans[index] = relative_span
    return local_references, visual_weights, {
        "observations": int(len(valid)),
        "global_scale_m_per_mast3r_unit": global_scale,
        "radius_frames": float(radius_frames),
        "downweighted_queries": int(np.count_nonzero(visual_weights < 0.5)),
        "visual_prior_weight_min": float(np.min(visual_weights)),
        "visual_prior_weight_median": float(np.median(visual_weights)),
        "local_scale_log_deviation_p95": float(
            np.percentile(local_deviations, 95)
        ),
        "local_scale_relative_span_p95": float(np.percentile(local_spans, 95)),
    }


def refine_positions(
    positions: np.ndarray,
    camera_rotations: Rotation,
    edges: list[dict],
    target_mode: str = "full",
) -> tuple[np.ndarray, dict]:
    if target_mode not in {"full", "projected"}:
        raise ValueError(f"unsupported stereo target mode: {target_mode}")
    node_indices = np.asarray(
        sorted(
            {
                int(edge["first_index"])
                for edge in edges
            }
            | {int(edge["second_index"]) for edge in edges}
        ),
        dtype=int,
    )
    node_lookup = {frame_index: node for node, frame_index in enumerate(node_indices)}
    initial = positions[node_indices]
    stereo_targets = []
    confidences = []
    for edge in edges:
        first = int(edge["first_index"])
        second = int(edge["second_index"])
        displacement_camera = np.asarray(
            edge["metric_displacement_camera_i_m"], dtype=float
        )
        target = camera_rotations[first].apply(displacement_camera)
        if target_mode == "projected":
            visual_delta = positions[second] - positions[first]
            visual_distance = np.linalg.norm(visual_delta)
            if visual_distance <= np.finfo(float).eps:
                raise ValueError("cannot project stereo edge onto zero visual motion")
            visual_direction = visual_delta / visual_distance
            target = visual_direction * np.dot(target, visual_direction)
        stereo_targets.append(target)
        confidences.append(float(edge.get("pnp_inlier_ratio", 0.5)))
    stereo_targets = np.asarray(stereo_targets)
    confidences = np.asarray(confidences)
    stereo_sigma_m = 0.004
    visual_prior_sigma_m = 0.020
    correction_smooth_sigma_m = 0.008
    anchor_sigma_m = 0.0001
    robust_weights = np.ones(len(edges))

    def build_system() -> tuple[np.ndarray, np.ndarray]:
        rows = []
        targets = []
        for node in range(len(node_indices)):
            row = np.zeros(len(node_indices))
            row[node] = 1.0 / visual_prior_sigma_m
            rows.append(row)
            targets.append(np.zeros(3))
        anchor = np.zeros(len(node_indices))
        anchor[0] = 1.0 / anchor_sigma_m
        rows.append(anchor)
        targets.append(np.zeros(3))
        for node in range(1, len(node_indices) - 1):
            row = np.zeros(len(node_indices))
            row[node - 1 : node + 2] = (
                np.asarray([1.0, -2.0, 1.0]) / correction_smooth_sigma_m
            )
            rows.append(row)
            targets.append(np.zeros(3))
        for edge_index, edge in enumerate(edges):
            first = node_lookup[int(edge["first_index"])]
            second = node_lookup[int(edge["second_index"])]
            weight = (
                np.sqrt(max(confidences[edge_index], 0.1))
                * np.sqrt(robust_weights[edge_index])
                / stereo_sigma_m
            )
            row = np.zeros(len(node_indices))
            row[first] = -weight
            row[second] = weight
            initial_delta = initial[second] - initial[first]
            rows.append(row)
            targets.append(weight * (stereo_targets[edge_index] - initial_delta))
        return np.asarray(rows), np.asarray(targets)

    correction = np.zeros_like(initial)
    for _ in range(4):
        design, target = build_system()
        correction, *_ = np.linalg.lstsq(design, target, rcond=None)
        residuals = []
        for edge_index, edge in enumerate(edges):
            first = node_lookup[int(edge["first_index"])]
            second = node_lookup[int(edge["second_index"])]
            predicted = (
                initial[second]
                + correction[second]
                - initial[first]
                - correction[first]
            )
            residuals.append(np.linalg.norm(predicted - stereo_targets[edge_index]))
        residuals = np.asarray(residuals)
        robust_weights = np.minimum(1.0, 0.008 / np.maximum(residuals, 1e-9))

    before = []
    after = []
    for edge_index, edge in enumerate(edges):
        first = node_lookup[int(edge["first_index"])]
        second = node_lookup[int(edge["second_index"])]
        before.append(np.linalg.norm((initial[second] - initial[first]) - stereo_targets[edge_index]))
        after.append(
            np.linalg.norm(
                (initial[second] + correction[second])
                - (initial[first] + correction[first])
                - stereo_targets[edge_index]
            )
        )
    correction_all = np.column_stack(
        [
            np.interp(np.arange(len(positions)), node_indices, correction[:, axis])
            for axis in range(3)
        ]
    )
    refined = positions + correction_all
    correction_norm = np.linalg.norm(correction_all, axis=1)
    return refined, {
        "mode": (
            "projected_length_refinement"
            if target_mode == "projected"
            else "full_vector_refinement"
        ),
        "nodes": int(len(node_indices)),
        "stereo_edges": int(len(edges)),
        "stereo_edge_rmse_before_m": float(np.sqrt(np.mean(np.square(before)))),
        "stereo_edge_rmse_after_m": float(np.sqrt(np.mean(np.square(after)))),
        "position_correction_median_m": float(np.median(correction_norm)),
        "position_correction_p95_m": float(np.percentile(correction_norm, 95)),
        "position_correction_max_m": float(np.max(correction_norm)),
        "robust_edge_inliers": int(np.count_nonzero(robust_weights >= 0.5)),
    }


def refine_positions_incremental(
    positions: np.ndarray,
    camera_rotations: Rotation,
    observations: list[dict],
    blend: float = INCREMENTAL_STEREO_BLEND,
    max_correction_m: float = MAX_INCREMENTAL_POSITION_CORRECTION_M,
) -> tuple[np.ndarray, dict]:
    if not 0.0 <= blend <= 1.0:
        raise ValueError("incremental stereo blend must lie in [0, 1]")
    if max_correction_m <= 0.0:
        raise ValueError("incremental position correction limit must be positive")
    if not observations:
        raise ValueError("stereo report has no candidate observations")
    candidate_observation_count = len(observations)
    selected_sample_hop = min(
        int(observation.get("sample_hop", 1)) for observation in observations
    )
    observations = [
        observation
        for observation in observations
        if int(observation.get("sample_hop", 1)) == selected_sample_hop
    ]
    nodes = [int(observations[0]["first_index"])]
    corrected_nodes = [positions[nodes[0]].copy()]
    accepted = []
    before = []
    after = []
    for observation in observations:
        first = int(observation["first_index"])
        second = int(observation["second_index"])
        if first != nodes[-1] or second <= first:
            raise ValueError("stereo observations must form a contiguous forward chain")
        visual_delta = positions[second] - positions[first]
        corrected_delta = visual_delta
        if observation.get("accepted"):
            stereo_delta = camera_rotations[first].apply(
                np.asarray(observation["metric_displacement_camera_i_m"], dtype=float)
            )
            confidence = min(
                1.0, float(observation.get("pnp_inlier_ratio", 0.5)) / 0.8
            )
            weight = blend * confidence
            corrected_delta = (1.0 - weight) * visual_delta + weight * stereo_delta
            accepted.append(observation)
            before.append(float(np.linalg.norm(visual_delta - stereo_delta)))
            after.append(float(np.linalg.norm(corrected_delta - stereo_delta)))
        corrected_nodes.append(corrected_nodes[-1] + corrected_delta)
        nodes.append(second)
    if len(accepted) < 4:
        raise ValueError("insufficient accepted stereo translation edges")
    nodes = np.asarray(nodes, dtype=int)
    node_corrections = np.asarray(corrected_nodes) - positions[nodes]
    requested_correction = np.column_stack(
        [
            np.interp(np.arange(len(positions)), nodes, node_corrections[:, axis])
            for axis in range(3)
        ]
    )
    requested_norm = np.linalg.norm(requested_correction, axis=1)
    requested_max = float(np.max(requested_norm))
    correction_scale = min(1.0, max_correction_m / max(requested_max, 1e-12))
    correction = correction_scale * requested_correction
    refined = positions + correction
    correction_norm = np.linalg.norm(correction, axis=1)
    after = []
    for observation in accepted:
        first = int(observation["first_index"])
        second = int(observation["second_index"])
        stereo_delta = camera_rotations[first].apply(
            np.asarray(observation["metric_displacement_camera_i_m"], dtype=float)
        )
        after.append(float(np.linalg.norm((refined[second] - refined[first]) - stereo_delta)))
    return refined, {
        "mode": "confidence_weighted_incremental_full_vector",
        "requested_blend": blend,
        "correction_scale": correction_scale,
        "effective_max_blend": blend * correction_scale,
        "candidate_observations": int(candidate_observation_count),
        "selected_sample_hop": int(selected_sample_hop),
        "selected_chain_observations": int(len(observations)),
        "nodes": int(len(nodes)),
        "stereo_edges": int(len(accepted)),
        "stereo_edge_rmse_before_m": float(np.sqrt(np.mean(np.square(before)))),
        "stereo_edge_rmse_after_m": float(np.sqrt(np.mean(np.square(after)))),
        "position_correction_requested_max_m": requested_max,
        "position_correction_median_m": float(np.median(correction_norm)),
        "position_correction_p95_m": float(np.percentile(correction_norm, 95)),
        "position_correction_max_m": float(np.max(correction_norm)),
        "position_correction_limit_m": max_correction_m,
        "robust_edge_inliers": int(len(accepted)),
    }


def preintegrate_acceleration(
    imu_times: np.ndarray,
    accel_body: np.ndarray,
    visual_times_mono: np.ndarray,
    body_rotations: Rotation,
    start_s: float,
    end_s: float,
    td_s: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Preintegrate specific force and its constant accelerometer-bias Jacobian."""
    inside = imu_times[(imu_times > start_s) & (imu_times < end_s)]
    grid = np.concatenate(([start_s], inside, [end_s]))
    accel = np.column_stack(
        [np.interp(grid, imu_times, accel_body[:, axis]) for axis in range(3)]
    )
    rotation_matrices = Slerp(visual_times_mono, body_rotations)(
        grid - td_s
    ).as_matrix()
    delta_position = np.zeros(3)
    delta_velocity = np.zeros(3)
    position_bias_jacobian = np.zeros((3, 3))
    velocity_bias_jacobian = np.zeros((3, 3))
    for index, dt in enumerate(np.diff(grid)):
        average_acceleration = 0.5 * (
            rotation_matrices[index] @ accel[index]
            + rotation_matrices[index + 1] @ accel[index + 1]
        )
        average_bias_jacobian = -0.5 * (
            rotation_matrices[index] + rotation_matrices[index + 1]
        )
        delta_position += (
            delta_velocity * dt + 0.5 * average_acceleration * dt * dt
        )
        position_bias_jacobian += (
            velocity_bias_jacobian * dt
            + 0.5 * average_bias_jacobian * dt * dt
        )
        delta_velocity += average_acceleration * dt
        velocity_bias_jacobian += average_bias_jacobian * dt
    return (
        delta_position,
        delta_velocity,
        position_bias_jacobian,
        velocity_bias_jacobian,
    )


def estimate_gravity_prior(
    imu_times: np.ndarray,
    gyro_body: np.ndarray,
    accel_body: np.ndarray,
    visual_times_mono: np.ndarray,
    body_rotations: Rotation,
    td_s: float,
) -> tuple[np.ndarray, int]:
    inside = (
        (imu_times >= visual_times_mono[0] + td_s)
        & (imu_times <= visual_times_mono[-1] + td_s)
    )
    sample_indices = np.flatnonzero(inside)[::4]
    if len(sample_indices) < 20:
        raise ValueError("insufficient IMU samples for gravity prior")
    selected_accel = accel_body[sample_indices]
    selected_gyro = gyro_body[sample_indices]
    static = (
        (np.linalg.norm(selected_gyro, axis=1) <= np.radians(12.0))
        & (
            np.abs(np.linalg.norm(selected_accel, axis=1) - STANDARD_GRAVITY)
            <= 0.6
        )
    )
    if np.count_nonzero(static) < 20:
        static = (
            np.abs(np.linalg.norm(selected_accel, axis=1) - STANDARD_GRAVITY)
            <= 0.6
        )
    if np.count_nonzero(static) < 20:
        raise ValueError("insufficient low-dynamic IMU samples for gravity prior")
    selected_times = imu_times[sample_indices][static]
    selected_rotations = Slerp(visual_times_mono, body_rotations)(
        selected_times - td_s
    )
    gravity = -np.median(
        selected_rotations.apply(selected_accel[static]), axis=0
    )
    gravity_norm = np.linalg.norm(gravity)
    if gravity_norm <= np.finfo(float).eps:
        raise ValueError("invalid zero gravity prior")
    return gravity * (STANDARD_GRAVITY / gravity_norm), int(np.count_nonzero(static))


def refine_positions_visual_inertial(
    positions: np.ndarray,
    camera_rotations: Rotation,
    observations: list[dict],
    visual_times_mono: np.ndarray,
    imu_times: np.ndarray,
    gyro_body: np.ndarray,
    accel_body: np.ndarray,
    body_t_camera: np.ndarray,
    td_s: float,
    node_stride: int = 10,
    max_correction_m: float = MAX_JOINT_POSITION_CORRECTION_M,
    correction_node_indices: np.ndarray | None = None,
    relative_motion_positions_body: np.ndarray | None = None,
    relative_motion_valid: np.ndarray | None = None,
    relative_motion_sigma_m: float = 0.008,
    reference_stereo_scale: float | None = None,
    visual_position_sigma_m: float = 0.020,
    correction_cap_mode: str = "global",
    correction_interpolation_mode: str = "linear",
) -> tuple[np.ndarray, dict]:
    """Jointly refine position, velocity, gravity and accelerometer bias.

    A second, rigidly aligned UMI-only odometry may contribute consecutive
    relative displacement factors through ``relative_motion_positions_body``.
    Its absolute origin is not constrained by this solver.
    """
    accepted = [
        observation
        for observation in observations
        if observation.get("accepted")
        and "metric_displacement_camera_i_m" in observation
    ]
    if len(accepted) < 4:
        raise ValueError("insufficient accepted stereo translation edges")
    if correction_node_indices is None:
        stereo_indices = {
            int(observation[key])
            for observation in accepted
            for key in ("first_index", "second_index")
        }
        node_indices = np.asarray(
            sorted(
                set(regular_node_indices(len(positions), node_stride))
                | stereo_indices
            ),
            dtype=int,
        )
        node_policy = "regular_plus_stereo"
    else:
        node_indices = np.asarray(correction_node_indices, dtype=int)
        if node_indices[0] != 0 or node_indices[-1] != len(positions) - 1:
            raise ValueError("keyframe correction nodes must include trajectory endpoints")
        node_policy = "mast3r_keyframes"
    node_count = len(node_indices)
    frame_left, frame_right, frame_alpha = interpolation_stencil(
        np.arange(len(positions)), node_indices
    )
    camera_position_in_body = body_t_camera[:3, 3]
    body_from_camera = Rotation.from_matrix(body_t_camera[:3, :3])
    body_rotations = camera_rotations * body_from_camera.inv()
    body_positions = positions - body_rotations.apply(camera_position_in_body)
    if relative_motion_sigma_m <= 0.0:
        raise ValueError("relative motion sigma must be positive")
    if visual_position_sigma_m <= 0.0:
        raise ValueError("visual position sigma must be positive")
    if relative_motion_positions_body is not None:
        relative_motion_positions_body = np.asarray(
            relative_motion_positions_body, dtype=float
        )
        if relative_motion_positions_body.shape != body_positions.shape:
            raise ValueError(
                "relative motion positions must match the visual trajectory shape"
            )
        if not np.all(np.isfinite(relative_motion_positions_body)):
            raise ValueError("relative motion positions contain non-finite values")
        if relative_motion_valid is None:
            relative_motion_valid = np.ones(len(body_positions), dtype=bool)
        relative_motion_valid = np.asarray(relative_motion_valid, dtype=bool)
        if relative_motion_valid.shape != (len(body_positions),):
            raise ValueError("relative motion validity must match trajectory length")
    gravity_prior, static_samples = estimate_gravity_prior(
        imu_times,
        gyro_body,
        accel_body,
        visual_times_mono,
        body_rotations,
        td_s,
    )
    preintegrations = []
    for first, second in zip(node_indices[:-1], node_indices[1:]):
        start_s = visual_times_mono[first] + td_s
        end_s = visual_times_mono[second] + td_s
        preintegrations.append(
            preintegrate_acceleration(
                imu_times,
                accel_body,
                visual_times_mono,
                body_rotations,
                start_s,
                end_s,
                td_s,
            )
        )

    position_offset = 0
    velocity_offset = 3 * node_count
    gravity_offset = 6 * node_count
    bias_offset = gravity_offset + 3
    unknowns = bias_offset + 3
    smooth_sigma_m = 0.010
    imu_position_sigma_m = 0.008
    imu_velocity_sigma_mps = 0.080
    stereo_sigma_m = 0.004
    gravity_sigma_mps2 = 0.15
    bias_sigma_mps2 = 0.20
    anchor_sigma_m = 0.0001
    stereo_weights = np.ones(len(accepted))
    edge_midpoints = np.asarray(
        [
            0.5
            * (
                float(observation["first_index"])
                + float(observation["second_index"])
            )
            for observation in accepted
        ]
    )
    edge_scale_references, _, edge_scale_quality = local_stereo_scale_state(
        accepted, edge_midpoints, reference_scale=reference_stereo_scale
    )
    _, visual_prior_weights, visual_scale_quality = local_stereo_scale_state(
        accepted,
        node_indices.astype(float),
        reference_scale=reference_stereo_scale,
    )
    stereo_prior_weights = np.asarray(
        [
            stereo_observation_confidence(observation, reference_scale)
            for observation, reference_scale in zip(
                accepted, edge_scale_references
            )
        ],
        dtype=float,
    )
    imu_position_weights = np.ones(node_count - 1)
    imu_velocity_weights = np.ones(node_count - 1)
    relative_motion_weights = (
        np.asarray(
            [
                relative_motion_valid[first] and relative_motion_valid[second]
                for first, second in zip(node_indices[:-1], node_indices[1:])
            ],
            dtype=float,
        )
        if relative_motion_positions_body is not None
        else np.empty(0)
    )
    relative_motion_initial_residuals = np.empty(0)
    if relative_motion_positions_body is not None:
        relative_motion_initial_residuals = np.asarray(
            [
                np.linalg.norm(
                    (body_positions[second] - body_positions[first])
                    - (
                        relative_motion_positions_body[second]
                        - relative_motion_positions_body[first]
                    )
                )
                for first, second in zip(node_indices[:-1], node_indices[1:])
            ],
            dtype=float,
        )
        relative_motion_weights *= np.minimum(
            1.0,
            0.008 / np.maximum(relative_motion_initial_residuals, 1e-12),
        )
    relative_motion_initial_weights = relative_motion_weights.copy()

    def add_identity(matrix, row: int, column: int, scale: float) -> None:
        matrix[row : row + 3, column : column + 3] = scale * np.eye(3)

    def add_frame_correction(
        matrix, row: int, frame_index: int, scale: float
    ) -> None:
        left = int(frame_left[frame_index])
        right = int(frame_right[frame_index])
        alpha = float(frame_alpha[frame_index])
        for axis in range(3):
            matrix[row + axis, position_offset + 3 * left + axis] += (
                scale * (1.0 - alpha)
            )
            if right != left:
                matrix[row + axis, position_offset + 3 * right + axis] += (
                    scale * alpha
                )

    def correction_at(frame_index: int, values: np.ndarray) -> np.ndarray:
        left = int(frame_left[frame_index])
        right = int(frame_right[frame_index])
        alpha = float(frame_alpha[frame_index])
        return (1.0 - alpha) * values[left] + alpha * values[right]

    def build_system():
        row_count = (
            3 * node_count
            + 3
            + 3 * max(node_count - 2, 0)
            + 3 * len(relative_motion_weights)
            + 6 * (node_count - 1)
            + 3 * len(accepted)
            + 6
        )
        design = lil_matrix((row_count, unknowns), dtype=float)
        target = np.zeros(row_count)
        row = 0
        for node in range(node_count):
            add_identity(
                design,
                row,
                position_offset + 3 * node,
                np.sqrt(visual_prior_weights[node]) / visual_position_sigma_m,
            )
            row += 3
        add_identity(design, row, position_offset, 1.0 / anchor_sigma_m)
        row += 3
        for node in range(1, node_count - 1):
            add_identity(
                design,
                row,
                position_offset + 3 * (node - 1),
                1.0 / smooth_sigma_m,
            )
            add_identity(
                design,
                row,
                position_offset + 3 * node,
                -2.0 / smooth_sigma_m,
            )
            add_identity(
                design,
                row,
                position_offset + 3 * (node + 1),
                1.0 / smooth_sigma_m,
            )
            row += 3
        if relative_motion_positions_body is not None:
            for node, (first, second) in enumerate(
                zip(node_indices[:-1], node_indices[1:])
            ):
                weight = (
                    np.sqrt(relative_motion_weights[node])
                    / relative_motion_sigma_m
                )
                add_identity(
                    design, row, position_offset + 3 * node, -weight
                )
                add_identity(
                    design, row, position_offset + 3 * (node + 1), weight
                )
                reference_delta = (
                    relative_motion_positions_body[second]
                    - relative_motion_positions_body[first]
                )
                visual_delta = body_positions[second] - body_positions[first]
                target[row : row + 3] = (
                    reference_delta - visual_delta
                ) * weight
                row += 3
        for node, preintegration in enumerate(preintegrations):
            first = int(node_indices[node])
            second = int(node_indices[node + 1])
            dt = visual_times_mono[second] - visual_times_mono[first]
            delta_position, delta_velocity, position_jacobian, velocity_jacobian = preintegration
            position_weight = np.sqrt(imu_position_weights[node]) / imu_position_sigma_m
            add_identity(design, row, position_offset + 3 * node, -position_weight)
            add_identity(design, row, position_offset + 3 * (node + 1), position_weight)
            add_identity(design, row, velocity_offset + 3 * node, -dt * position_weight)
            add_identity(design, row, gravity_offset, -0.5 * dt * dt * position_weight)
            design[row : row + 3, bias_offset : bias_offset + 3] = -position_jacobian * position_weight
            target[row : row + 3] = (
                delta_position - (body_positions[second] - body_positions[first])
            ) * position_weight
            row += 3
            velocity_weight = np.sqrt(imu_velocity_weights[node]) / imu_velocity_sigma_mps
            add_identity(design, row, velocity_offset + 3 * node, -velocity_weight)
            add_identity(design, row, velocity_offset + 3 * (node + 1), velocity_weight)
            add_identity(design, row, gravity_offset, -dt * velocity_weight)
            design[row : row + 3, bias_offset : bias_offset + 3] = -velocity_jacobian * velocity_weight
            target[row : row + 3] = delta_velocity * velocity_weight
            row += 3
        for edge_index, observation in enumerate(accepted):
            first = int(observation["first_index"])
            second = int(observation["second_index"])
            weight = (
                np.sqrt(
                    stereo_prior_weights[edge_index]
                    * stereo_weights[edge_index]
                )
                / stereo_sigma_m
            )
            stereo_target = camera_rotations[first].apply(
                np.asarray(observation["metric_displacement_camera_i_m"], dtype=float)
            )
            visual_delta = positions[second] - positions[first]
            add_frame_correction(design, row, first, -weight)
            add_frame_correction(design, row, second, weight)
            target[row : row + 3] = (stereo_target - visual_delta) * weight
            row += 3
        add_identity(design, row, gravity_offset, 1.0 / gravity_sigma_mps2)
        target[row : row + 3] = gravity_prior / gravity_sigma_mps2
        row += 3
        add_identity(design, row, bias_offset, 1.0 / bias_sigma_mps2)
        return design.tocsr(), target

    solution = np.zeros(unknowns)
    for _ in range(4):
        design, target = build_system()
        solution = lsqr(
            design, target, atol=1e-10, btol=1e-10, iter_lim=5000
        )[0]
        position_correction = solution[position_offset:velocity_offset].reshape(-1, 3)
        velocities = solution[velocity_offset:gravity_offset].reshape(-1, 3)
        gravity = solution[gravity_offset:bias_offset]
        bias = solution[bias_offset:bias_offset + 3]
        if relative_motion_positions_body is not None:
            for node, (first, second) in enumerate(
                zip(node_indices[:-1], node_indices[1:])
            ):
                if relative_motion_weights[node] == 0.0:
                    continue
                corrected_delta = (
                    body_positions[second]
                    + position_correction[node + 1]
                    - body_positions[first]
                    - position_correction[node]
                )
                reference_delta = (
                    relative_motion_positions_body[second]
                    - relative_motion_positions_body[first]
                )
                residual = np.linalg.norm(corrected_delta - reference_delta)
                relative_motion_weights[node] = min(
                    1.0, 0.008 / max(residual, 1e-12)
                )
        for node, preintegration in enumerate(preintegrations):
            first = int(node_indices[node])
            second = int(node_indices[node + 1])
            dt = visual_times_mono[second] - visual_times_mono[first]
            delta_position, delta_velocity, position_jacobian, velocity_jacobian = preintegration
            position_residual = (
                body_positions[second]
                + position_correction[node + 1]
                - body_positions[first]
                - position_correction[node]
                - velocities[node] * dt
                - 0.5 * gravity * dt * dt
                - delta_position
                - position_jacobian @ bias
            )
            velocity_residual = (
                velocities[node + 1]
                - velocities[node]
                - gravity * dt
                - delta_velocity
                - velocity_jacobian @ bias
            )
            imu_position_weights[node] = min(
                1.0, 0.015 / max(np.linalg.norm(position_residual), 1e-12)
            )
            imu_velocity_weights[node] = min(
                1.0, 0.10 / max(np.linalg.norm(velocity_residual), 1e-12)
            )
        for edge_index, observation in enumerate(accepted):
            first = int(observation["first_index"])
            second = int(observation["second_index"])
            stereo_target = camera_rotations[first].apply(
                np.asarray(observation["metric_displacement_camera_i_m"], dtype=float)
            )
            visual_delta = positions[second] - positions[first]
            residual = (
                visual_delta
                + correction_at(second, position_correction)
                - correction_at(first, position_correction)
                - stereo_target
            )
            stereo_weights[edge_index] = min(
                1.0, 0.008 / max(np.linalg.norm(residual), 1e-12)
            )

    correction, requested_norm, correction_scale = (
        cap_interpolated_position_corrections(
            position_correction,
            frame_left,
            frame_right,
            frame_alpha,
            max_correction_m,
            correction_cap_mode,
            correction_interpolation_mode,
            node_indices,
            np.arange(len(positions)),
        )
    )
    requested_correction = (
        (1.0 - frame_alpha[:, None]) * position_correction[frame_left]
        + frame_alpha[:, None] * position_correction[frame_right]
    )
    correction_changed = (
        np.linalg.norm(correction - requested_correction, axis=1) > 1e-12
    )
    refined = positions + correction
    before = []
    after = []
    for observation in accepted:
        first = int(observation["first_index"])
        second = int(observation["second_index"])
        stereo_target = camera_rotations[first].apply(
            np.asarray(observation["metric_displacement_camera_i_m"], dtype=float)
        )
        before.append(np.linalg.norm((positions[second] - positions[first]) - stereo_target))
        after.append(np.linalg.norm((refined[second] - refined[first]) - stereo_target))
    correction_norm = np.linalg.norm(correction, axis=1)
    relative_before = []
    relative_after = []
    if relative_motion_positions_body is not None:
        refined_body_positions = body_positions + correction
        for node, (first, second) in enumerate(
            zip(node_indices[:-1], node_indices[1:])
        ):
            if relative_motion_weights[node] == 0.0:
                continue
            reference_delta = (
                relative_motion_positions_body[second]
                - relative_motion_positions_body[first]
            )
            relative_before.append(
                np.linalg.norm(
                    (body_positions[second] - body_positions[first])
                    - reference_delta
                )
            )
            relative_after.append(
                np.linalg.norm(
                    (refined_body_positions[second] - refined_body_positions[first])
                    - reference_delta
                )
            )
    return refined, {
        "mode": (
            "keyframe_graph_visual_stereo_imu_preintegration"
            if correction_node_indices is not None
            else "joint_visual_stereo_imu_preintegration"
        ),
        "node_policy": node_policy,
        "anchor_policy": "first_node_only",
        "visual_position_sigma_m": float(visual_position_sigma_m),
        "nodes": int(node_count),
        "node_stride": int(node_stride),
        "stereo_edges": int(len(accepted)),
        "stereo_edge_rmse_before_m": float(np.sqrt(np.mean(np.square(before)))),
        "stereo_edge_rmse_after_m": float(np.sqrt(np.mean(np.square(after)))),
        "position_correction_requested_max_m": float(np.max(requested_norm)),
        "position_correction_median_m": float(np.median(correction_norm)),
        "position_correction_p95_m": float(np.percentile(correction_norm, 95)),
        "position_correction_max_m": float(np.max(correction_norm)),
        "position_correction_limit_m": float(max_correction_m),
        "correction_scale": float(correction_scale),
        "correction_cap_mode": correction_cap_mode,
        "correction_interpolation_mode": correction_interpolation_mode,
        "correction_clipped_frames": int(np.count_nonzero(correction_changed)),
        "gravity_prior_mps2": gravity_prior.tolist(),
        "gravity_solution_mps2": gravity.tolist(),
        "accel_bias_solution_mps2": bias.tolist(),
        "static_gravity_samples": static_samples,
        "robust_edge_inliers": int(np.count_nonzero(stereo_weights >= 0.5)),
        "stereo_prior_weight_median": float(np.median(stereo_prior_weights)),
        "stereo_prior_weight_p10": float(np.percentile(stereo_prior_weights, 10)),
        "local_stereo_scale_state": {
            **visual_scale_quality,
            "edge_reference_scale_log_deviation_p95": edge_scale_quality[
                "local_scale_log_deviation_p95"
            ],
        },
        "robust_imu_position_inliers": int(
            np.count_nonzero(imu_position_weights >= 0.5)
        ),
        "robust_imu_velocity_inliers": int(
            np.count_nonzero(imu_velocity_weights >= 0.5)
        ),
        "relative_motion_edges": int(np.count_nonzero(relative_motion_weights)),
        "relative_motion_sigma_m": float(relative_motion_sigma_m),
        "relative_motion_initial_disagreement_p95_m": (
            float(np.percentile(relative_motion_initial_residuals, 95))
            if len(relative_motion_initial_residuals)
            else None
        ),
        "initial_robust_relative_motion_inliers": int(
            np.count_nonzero(relative_motion_initial_weights >= 0.5)
        ),
        "relative_motion_rmse_before_m": (
            float(np.sqrt(np.mean(np.square(relative_before))))
            if relative_before
            else None
        ),
        "relative_motion_rmse_after_m": (
            float(np.sqrt(np.mean(np.square(relative_after))))
            if relative_after
            else None
        ),
        "robust_relative_motion_inliers": int(
            np.count_nonzero(relative_motion_weights >= 0.5)
        ),
        "linear_system_rows": int(design.shape[0]),
        "linear_system_unknowns": int(design.shape[1]),
    }


def diagnose_stereo_positions(
    positions: np.ndarray,
    camera_rotations: Rotation,
    edges: list[dict],
) -> dict:
    residuals = []
    for edge in edges:
        first = int(edge["first_index"])
        second = int(edge["second_index"])
        target = camera_rotations[first].apply(
            np.asarray(edge["metric_displacement_camera_i_m"], dtype=float)
        )
        residuals.append(
            np.linalg.norm((positions[second] - positions[first]) - target)
        )
    residuals = np.asarray(residuals)
    rmse = float(np.sqrt(np.mean(residuals**2)))
    return {
        "mode": "diagnostic_only",
        "nodes": 0,
        "stereo_edges": int(len(edges)),
        "stereo_edge_rmse_before_m": rmse,
        "stereo_edge_rmse_after_m": rmse,
        "position_correction_median_m": 0.0,
        "position_correction_p95_m": 0.0,
        "position_correction_max_m": 0.0,
        "robust_edge_inliers": int(len(edges)),
    }


def integrate_specific_force(
    imu_times: np.ndarray,
    accel_body: np.ndarray,
    visual_times_mono: np.ndarray,
    body_rotations: Rotation,
    start_s: float,
    end_s: float,
    td_s: float,
) -> tuple[np.ndarray, np.ndarray]:
    inside = imu_times[(imu_times > start_s) & (imu_times < end_s)]
    grid = np.concatenate(([start_s], inside, [end_s]))
    accel = np.column_stack(
        [np.interp(grid, imu_times, accel_body[:, axis]) for axis in range(3)]
    )
    rotations = Slerp(visual_times_mono, body_rotations)(grid - td_s)
    accel_world = rotations.apply(accel)
    delta_velocity = np.zeros(3)
    delta_position = np.zeros(3)
    for index, dt in enumerate(np.diff(grid)):
        average_accel = 0.5 * (accel_world[index] + accel_world[index + 1])
        delta_position += delta_velocity * dt + 0.5 * average_accel * dt * dt
        delta_velocity += average_accel * dt
    return delta_position, delta_velocity


def inertial_consistency(
    camera_positions: np.ndarray,
    camera_rotations: Rotation,
    node_indices: np.ndarray,
    visual_times_mono: np.ndarray,
    imu_times: np.ndarray,
    accel_body: np.ndarray,
    body_t_camera: np.ndarray,
    td_s: float,
) -> dict:
    body_from_camera = Rotation.from_matrix(body_t_camera[:3, :3])
    body_rotations = camera_rotations * body_from_camera.inv()
    body_positions = camera_positions - body_rotations.apply(body_t_camera[:3, 3])
    count = len(node_indices)
    gravity_offset = 3 * count
    rows = []
    targets = []
    for node in range(count - 1):
        first = int(node_indices[node])
        second = int(node_indices[node + 1])
        start_s = visual_times_mono[first] + td_s
        end_s = visual_times_mono[second] + td_s
        dt = end_s - start_s
        delta_p, delta_v = integrate_specific_force(
            imu_times,
            accel_body,
            visual_times_mono,
            body_rotations,
            start_s,
            end_s,
            td_s,
        )
        position_row = np.zeros((3, gravity_offset + 3))
        position_row[:, 3 * node : 3 * node + 3] = -dt * np.eye(3)
        position_row[:, gravity_offset:] = -0.5 * dt * dt * np.eye(3)
        rows.append(position_row)
        targets.append(delta_p - (body_positions[second] - body_positions[first]))
        velocity_row = np.zeros((3, gravity_offset + 3))
        velocity_row[:, 3 * node : 3 * node + 3] = -np.eye(3)
        velocity_row[:, 3 * (node + 1) : 3 * (node + 1) + 3] = np.eye(3)
        velocity_row[:, gravity_offset:] = -dt * np.eye(3)
        rows.append(velocity_row)
        targets.append(delta_v)
    design = np.vstack(rows)
    target = np.concatenate(targets)
    solution, _, rank, singular_values = np.linalg.lstsq(design, target, rcond=None)
    residual = design @ solution - target
    gravity = solution[gravity_offset:]
    return {
        "windows": int(count - 1),
        "gravity_mps2": gravity.tolist(),
        "gravity_norm_mps2": float(np.linalg.norm(gravity)),
        "equation_rmse": float(np.sqrt(np.mean(np.square(residual)))),
        "rank": int(rank),
        "unknowns": int(design.shape[1]),
        "condition_number": float(singular_values[0] / singular_values[-1]),
    }


def write_trajectory(
    path: Path,
    rows: list[dict],
    positions: np.ndarray,
    rotations: Rotation,
) -> None:
    quaternions = rotations.as_quat()
    if not (len(rows) == len(positions) == len(quaternions)):
        raise ValueError("trajectory output lengths do not match")
    if not np.all(np.isfinite(positions)) or not np.all(np.isfinite(quaternions)):
        raise ValueError("trajectory output contains non-finite poses")
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row, position, quaternion in zip(rows, positions, quaternions):
            updated = dict(row)
            for key, value in zip(("x", "y", "z"), position):
                updated[key] = f"{value:.9f}"
            updated["qw"] = f"{quaternion[3]:.9f}"
            for key, value in zip(("qx", "qy", "qz"), quaternion[:3]):
                updated[key] = f"{value:.9f}"
            writer.writerow(updated)


def run(args: argparse.Namespace) -> dict:
    times, positions, camera_rotations, rows = load_trajectory(args.trajectory)
    stereo_report = load_json_report(args.stereo_report)
    validate_onboard_report(
        stereo_report, args.stereo_report, "umi_mast3r_stereo_scale_v2"
    )
    if Path(stereo_report.get("session", "")).resolve() != args.session.resolve():
        raise ValueError("stereo scale report session does not match fusion session")
    stereo_scale = float(stereo_report.get("scale_m_per_mast3r_unit", np.nan))
    if not np.isfinite(stereo_scale) or stereo_scale <= 0.0:
        raise ValueError("stereo scale report contains an invalid scale")
    stereo_report["report_path"] = str(args.stereo_report.resolve())
    stereo_report = condition_fixed_rotation_stereo_report(
        stereo_report,
        args.maximum_fixed_pnp_free_rotation_delta_deg,
        args.maximum_fixed_pnp_reprojection_p95_px,
    )
    additional_stereo_reports = []
    for path in args.additional_stereo_report or []:
        report = load_json_report(path)
        validate_onboard_report(report, path, "umi_mast3r_stereo_scale_v2")
        report["report_path"] = str(path.resolve())
        report = condition_fixed_rotation_stereo_report(
            report,
            args.maximum_fixed_pnp_free_rotation_delta_deg,
            args.maximum_fixed_pnp_reprojection_p95_px,
        )
        report = condition_learned_stereo_report(
            report,
            stereo_scale,
            args.minimum_learned_stereo_confidence,
            args.learned_stereo_translation_only,
        )
        additional_stereo_reports.append(report)
    stereo_report = merge_stereo_reports(
        stereo_report, additional_stereo_reports
    )
    stereo_report["observations"] = filter_stereo_observations(
        stereo_report.get("observations", []), args.minimum_stereo_sample_hop
    )
    config = load_vins_config(args.vins_config, args.expected_td_s)
    body_t_camera = body_t_trajectory_camera_from_stereo_report(
        config["body_T_camera"], stereo_report
    )
    edges = accepted_stereo_edges(stereo_report)
    node_indices = regular_node_indices(
        len(times), args.orientation_node_stride
    )
    metric_scale_quality = None
    if args.imu_scale_report:
        imu_scale_report = load_json_report(args.imu_scale_report)
        validate_onboard_report(
            imu_scale_report,
            args.imu_scale_report,
            "umi_mast3r_imu_scale_v1",
        )
        if Path(imu_scale_report.get("output", "")).resolve() != args.trajectory.resolve():
            raise ValueError("fusion trajectory is not the IMU-scaled trajectory")
        imu_scale = float(imu_scale_report.get("scale", np.nan))
        if not np.isfinite(imu_scale) or imu_scale <= 0.0:
            raise ValueError("IMU scale report contains an invalid scale")
        metric_scale_quality = scale_consistency(
            imu_scale,
            stereo_scale,
            args.max_scale_disagreement_ratio,
        )
        if args.metric_scale_mode == "joint":
            selected_scale = metric_scale_quality[
                "joint_scale_m_per_mast3r_unit"
            ]
            selection_policy = "equal_weight_log_mean"
        elif args.metric_scale_mode == "stereo":
            selected_scale = metric_scale_quality[
                "stereo_scale_m_per_mast3r_unit"
            ]
            selection_policy = "d405_stereo_direct_metric"
        else:
            selected_scale = metric_scale_quality[
                "imu_scale_m_per_mast3r_unit"
            ]
            selection_policy = "imu_adjacent_windows"
        if args.metric_scale_mode in {"joint", "stereo"}:
            imu_scale = metric_scale_quality["imu_scale_m_per_mast3r_unit"]
            positions = positions[0] + (selected_scale / imu_scale) * (
                positions - positions[0]
            )
        metric_scale_quality["selected_scale_m_per_mast3r_unit"] = selected_scale
        metric_scale_quality["selection_policy"] = selection_policy
    elif args.metric_scale_mode in {"joint", "stereo"}:
        raise ValueError(f"{args.metric_scale_mode} metric scale requires --imu-scale-report")
    visual_times_mono = camera_epoch_to_monotonic(
        args.session / "d405_frames.csv", args.stream, times
    )
    imu_times, gyro, accel, imu_info = load_calibrated_imu(
        args.session / "external_imu" / "imu.bin", args.imu_calibration
    )
    body_from_camera = Rotation.from_matrix(body_t_camera[:3, :3])
    refined_rotations, rotation_quality = refine_orientations(
        camera_rotations,
        node_indices,
        visual_times_mono,
        imu_times,
        gyro,
        body_from_camera,
        config["td_s"],
        stereo_report.get("observations", []),
    )
    relative_motion_positions_body = None
    relative_motion_valid = None
    relative_motion_alignment = None
    relative_motion_source_quality = None
    if args.relative_motion_trajectory is not None:
        if args.position_mode not in {"joint-inertial", "keyframe-graph"}:
            raise ValueError(
                "relative motion trajectory requires joint-inertial or keyframe-graph mode"
            )
        reference_times, reference_positions, _, reference_rows = load_trajectory(
            args.relative_motion_trajectory
        )
        relative_motion_report = load_json_report(args.relative_motion_report)
        relative_motion_source_quality = validate_relative_motion_report(
            relative_motion_report,
            args.relative_motion_report,
            args.relative_motion_trajectory,
            args.session,
            len(reference_rows),
        )
        visual_body_rotations = refined_rotations * body_from_camera.inv()
        visual_body_positions = positions - visual_body_rotations.apply(
            body_t_camera[:3, 3]
        )
        (
            relative_motion_positions_body,
            relative_motion_valid,
            relative_motion_alignment,
        ) = (
            align_relative_motion_positions(
                times,
                visual_body_positions,
                reference_times,
                reference_positions,
            )
        )
        relative_motion_alignment["source_validation"] = (
            relative_motion_source_quality
        )
    acceleration_quality_before = inertial_consistency(
        positions,
        refined_rotations,
        node_indices,
        visual_times_mono,
        imu_times,
        accel,
        body_t_camera,
        config["td_s"],
    )
    position_mode, position_mode_policy = select_position_mode(
        args.position_mode, metric_scale_quality
    )
    selected_visual_position_sigma_m, visual_sigma_selection = (
        select_visual_position_sigma(
            args.visual_position_sigma_m,
            relative_motion_alignment,
            enabled=args.auto_visual_position_sigma,
            trigger_disagreement_m=(
                args.weak_visual_trigger_disagreement_mm / 1000.0
            ),
            weak_visual_sigma_m=args.weak_visual_position_sigma_m,
        )
    )
    keyframe_graph_quality = None
    if position_mode == "joint-inertial":
        refined_positions, position_quality = refine_positions_visual_inertial(
            positions,
            refined_rotations,
            stereo_report.get("observations", []),
            visual_times_mono,
            imu_times,
            gyro,
            accel,
            body_t_camera,
            config["td_s"],
            node_stride=args.position_node_stride,
            max_correction_m=args.joint_max_correction_mm / 1000.0,
            relative_motion_positions_body=relative_motion_positions_body,
            relative_motion_valid=relative_motion_valid,
            relative_motion_sigma_m=args.relative_motion_sigma_m,
            reference_stereo_scale=stereo_report[
                "scale_m_per_mast3r_unit"
            ],
            visual_position_sigma_m=selected_visual_position_sigma_m,
            correction_cap_mode=args.joint_correction_cap_mode,
            correction_interpolation_mode=args.joint_correction_interpolation,
        )
    elif position_mode == "keyframe-graph":
        if args.keyframe_dir is None:
            raise ValueError("keyframe-graph position mode requires --keyframe-dir")
        keyframe_indices, keyframe_graph_quality = load_mast3r_keyframe_indices(
            args.keyframe_dir, times
        )
        keyframe_indices, density_quality = select_keyframe_correction_nodes(
            keyframe_indices,
            len(times),
            args.position_node_stride,
            keyframe_only=args.keyframe_only_correction_nodes,
        )
        keyframe_graph_quality.update(density_quality)
        refined_positions, position_quality = refine_positions_visual_inertial(
            positions,
            refined_rotations,
            stereo_report.get("observations", []),
            visual_times_mono,
            imu_times,
            gyro,
            accel,
            body_t_camera,
            config["td_s"],
            correction_node_indices=keyframe_indices,
            max_correction_m=args.joint_max_correction_mm / 1000.0,
            relative_motion_positions_body=relative_motion_positions_body,
            relative_motion_valid=relative_motion_valid,
            relative_motion_sigma_m=args.relative_motion_sigma_m,
            reference_stereo_scale=stereo_report[
                "scale_m_per_mast3r_unit"
            ],
            visual_position_sigma_m=selected_visual_position_sigma_m,
            correction_cap_mode=args.joint_correction_cap_mode,
            correction_interpolation_mode=args.joint_correction_interpolation,
        )
    elif position_mode == "incremental":
        refined_positions, position_quality = refine_positions_incremental(
            positions,
            refined_rotations,
            stereo_report.get("observations", []),
        )
    elif position_mode in {"full", "projected"}:
        refined_positions, position_quality = refine_positions(
            positions,
            refined_rotations,
            edges,
            target_mode=position_mode,
        )
    else:
        refined_positions = positions
        position_quality = diagnose_stereo_positions(
            positions, refined_rotations, edges
        )
    full_rate_position_quality = None
    if args.full_rate_imu_position_refinement:
        if position_mode not in {"joint-inertial", "keyframe-graph"}:
            raise ValueError(
                "full-rate IMU position refinement requires joint-inertial or keyframe-graph mode"
            )
        refined_positions, full_rate_position_quality = refine_positions_full_rate_imu(
            refined_positions,
            refined_rotations,
            visual_times_mono,
            imu_times,
            accel,
            body_t_camera,
            config["td_s"],
            np.asarray(position_quality["gravity_solution_mps2"], dtype=float),
            max_correction_m=args.full_rate_max_correction_mm / 1000.0,
        )
    position_acceleration_quality = inertial_consistency(
        refined_positions,
        refined_rotations,
        node_indices,
        visual_times_mono,
        imu_times,
        accel,
        body_t_camera,
        config["td_s"],
    )
    # Keep the final attitude in the same world frame as the jointly refined
    # metric trajectory. This adjustment is constant and does not reshape it.
    refined_rotations, trajectory_frame_quality = (
        align_orientations_to_translation_frame(
            refined_positions,
            refined_rotations,
            stereo_report.get("observations", []),
        )
    )
    acceleration_quality = inertial_consistency(
        refined_positions,
        refined_rotations,
        node_indices,
        visual_times_mono,
        imu_times,
        accel,
        body_t_camera,
        config["td_s"],
    )
    inertial_position_rmse_regression = relative_rmse_regression(
        acceleration_quality_before["equation_rmse"],
        position_acceleration_quality["equation_rmse"],
    )
    failures = []
    if not rotation_quality["optimizer_success"]:
        failures.append("orientation_optimizer_failed")
    if rotation_quality["after_p95_deg"] > 1.0:
        failures.append("visual_gyro_rotation_inconsistent")
    if (
        rotation_quality["orientation_correction_max_deg"]
        > rotation_quality["orientation_correction_limit_deg"] + 1e-9
    ):
        failures.append("orientation_correction_too_large")
    if trajectory_frame_quality["correction_limited"]:
        failures.append("trajectory_frame_orientation_correction_too_large")
    if (
        position_mode != "none"
        and position_quality["stereo_edge_rmse_after_m"]
        >= position_quality["stereo_edge_rmse_before_m"]
    ):
        failures.append("stereo_translation_refinement_did_not_improve")
    if position_quality["position_correction_max_m"] > 0.05:
        failures.append("position_correction_too_large")
    if (
        position_mode == "incremental"
        and position_quality["position_correction_max_m"]
        > MAX_INCREMENTAL_POSITION_CORRECTION_M + 1e-9
    ):
        failures.append("incremental_position_correction_too_large")
    if (
        position_mode in {"joint-inertial", "keyframe-graph"}
        and position_quality["position_correction_max_m"]
        > args.joint_max_correction_mm / 1000.0 + 1e-9
    ):
        failures.append("joint_position_correction_too_large")
    if (
        position_mode in {"joint-inertial", "keyframe-graph"}
        and inertial_position_rmse_regression
        > MAX_INERTIAL_RMSE_REGRESSION_RATIO
    ):
        failures.append("imu_position_refinement_did_not_improve")
    if not 7.0 <= acceleration_quality["gravity_norm_mps2"] <= 12.0:
        failures.append("gravity_norm_out_of_range")
    if acceleration_quality["rank"] < acceleration_quality["unknowns"]:
        failures.append("inertial_system_rank_deficient")
    if (
        metric_scale_quality is not None
        and not metric_scale_quality["consistent"]
        and args.metric_scale_mode != "stereo"
    ):
        failures.append("imu_stereo_metric_scale_disagreement")
    # 默认 "fail" ⇒ blocking is failures ⇒ 行为与产物逐字节不变。
    blocking = blocking_failures(failures, args.scale_disagreement_policy)
    report = {
        "schema": "umi_mast3r_stereo_imu_fusion_v2",
        "result": "PASS" if not blocking else "FAIL",
        "failures": failures,
        "slam_supervision": False,
        "external_ground_truth_used": False,
        "algorithm": "MASt3R learned visual trajectory + D405 stereo scale check + 400Hz IMU metric scale/attitude",
        "inputs": {
            "trajectory": str(args.trajectory.resolve()),
            "stereo_report": str(args.stereo_report.resolve()),
            "additional_stereo_reports": [
                str(path.resolve()) for path in args.additional_stereo_report or []
            ],
            "imu_scale_report": (
                str(args.imu_scale_report.resolve()) if args.imu_scale_report else None
            ),
            "session": str(args.session.resolve()),
            "imu_calibration": str(args.imu_calibration.resolve()),
            "vins_spatiotemporal_calibration": str(args.vins_config.resolve()),
            "keyframe_dir": (
                str(args.keyframe_dir.resolve()) if args.keyframe_dir else None
            ),
            "relative_motion_trajectory": (
                str(args.relative_motion_trajectory.resolve())
                if args.relative_motion_trajectory
                else None
            ),
            "relative_motion_report": (
                str(args.relative_motion_report.resolve())
                if args.relative_motion_report
                else None
            ),
            "external_ground_truth_used": False,
        },
        "time_alignment": {
            "estimate_td": config["estimate_td"],
            "td_s": config["td_s"],
            "policy": "Docker2 formal fixed td applied exactly once; replay shift is zero",
        },
        "camera_extrinsics": {
            "vins_body_T_left_ir": config["body_T_camera"].tolist(),
            "effective_body_T_trajectory_camera": body_t_camera.tolist(),
            "trajectory_observation_frame": stereo_report["observation_frame"],
            "policy": (
                "body_T_color = body_T_left_ir * inverse(color_T_left_ir)"
                if stereo_report["observation_frame"] == "color_camera_i"
                else "trajectory camera is the calibrated left IR camera"
            ),
        },
        "imu": imu_info,
        "stereo_scale_m_per_mast3r_unit": stereo_report[
            "scale_m_per_mast3r_unit"
        ],
        "minimum_stereo_sample_hop": int(args.minimum_stereo_sample_hop),
        "learned_stereo_policy": {
            "minimum_confidence": args.minimum_learned_stereo_confidence,
            "translation_only": args.learned_stereo_translation_only,
        },
        "metric_scale_consistency": metric_scale_quality,
        "orientation_node_stride": args.orientation_node_stride,
        "position_node_stride": args.position_node_stride,
        "keyframe_only_correction_nodes": args.keyframe_only_correction_nodes,
        "position_fusion_selection": {
            "requested_mode": args.position_mode,
            "selected_mode": position_mode,
            "policy": position_mode_policy,
            "projected_max_scale_disagreement_ratio": (
                AUTO_PROJECTED_MAX_SCALE_DISAGREEMENT_RATIO
                if args.position_mode == "auto"
                else None
            ),
            "visual_position_sigma": visual_sigma_selection,
        },
        "keyframe_graph": keyframe_graph_quality,
        "relative_motion_alignment": relative_motion_alignment,
        "rotation_fusion": rotation_quality,
        "trajectory_frame_alignment": trajectory_frame_quality,
        "stereo_translation_fusion": position_quality,
        "full_rate_imu_position_refinement": full_rate_position_quality,
        "accelerometer_consistency_before": acceleration_quality_before,
        "accelerometer_consistency_after_position": position_acceleration_quality,
        "inertial_position_rmse_regression_ratio": inertial_position_rmse_regression,
        "maximum_inertial_position_rmse_regression_ratio": (
            MAX_INERTIAL_RMSE_REGRESSION_RATIO
        ),
        "accelerometer_consistency": acceleration_quality,
        "failed_output_written_for_diagnostics": bool(
            blocking and args.write_failed_output
        ),
        "output": str(args.output.resolve()),
    }
    if args.scale_disagreement_policy != "fail":
        # 新键**只在非默认策略下写** ⇒ 默认路径的报告 JSON 逐字节不变。
        report["scale_disagreement_policy"] = args.scale_disagreement_policy
        report["blocking_failures"] = blocking
        report["tolerated_failures"] = [
            name for name in failures if name not in set(blocking)
        ]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if not blocking or args.write_failed_output:
        write_trajectory(
            args.output, rows, refined_positions, refined_rotations
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trajectory", type=Path, required=True)
    parser.add_argument("--stereo-report", type=Path, required=True)
    parser.add_argument("--additional-stereo-report", type=Path, action="append")
    parser.add_argument("--imu-scale-report", type=Path)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--stream", choices=("color", "infrared_left"), default="color")
    parser.add_argument("--vins-config", type=Path, required=True)
    parser.add_argument("--imu-calibration", type=Path, required=True)
    parser.add_argument("--expected-td-s", type=float)
    parser.add_argument("--orientation-node-stride", type=int, default=10)
    parser.add_argument("--position-node-stride", type=int, default=10)
    parser.add_argument("--minimum-stereo-sample-hop", type=int, default=1)
    parser.add_argument(
        "--minimum-learned-stereo-confidence", type=float, default=0.0
    )
    parser.add_argument("--learned-stereo-translation-only", action="store_true")
    parser.add_argument(
        "--maximum-fixed-pnp-free-rotation-delta-deg",
        type=float,
        default=float("inf"),
    )
    parser.add_argument(
        "--maximum-fixed-pnp-reprojection-p95-px",
        type=float,
        default=float("inf"),
    )
    parser.add_argument("--keyframe-dir", type=Path)
    parser.add_argument(
        "--keyframe-only-correction-nodes",
        action="store_true",
        help="optimize only saved MASt3R keyframes instead of adding regular nodes",
    )
    parser.add_argument("--relative-motion-trajectory", type=Path)
    parser.add_argument("--relative-motion-report", type=Path)
    parser.add_argument("--relative-motion-sigma-m", type=float, default=0.008)
    parser.add_argument("--visual-position-sigma-m", type=float, default=0.020)
    parser.add_argument("--auto-visual-position-sigma", action="store_true")
    parser.add_argument("--weak-visual-position-sigma-m", type=float, default=0.040)
    parser.add_argument(
        "--weak-visual-trigger-disagreement-mm", type=float, default=50.0
    )
    parser.add_argument(
        "--joint-max-correction-mm",
        type=float,
        default=1000.0 * MAX_JOINT_POSITION_CORRECTION_M,
    )
    parser.add_argument(
        "--joint-correction-cap-mode",
        choices=("global", "per-node", "per-frame"),
        default="global",
    )
    parser.add_argument(
        "--joint-correction-interpolation",
        choices=("linear", "pchip"),
        default="linear",
    )
    parser.add_argument("--full-rate-imu-position-refinement", action="store_true")
    parser.add_argument("--full-rate-max-correction-mm", type=float, default=12.0)
    parser.add_argument(
        "--metric-scale-mode", choices=("imu", "joint", "stereo"), default="imu"
    )
    parser.add_argument(
        "--position-mode",
        choices=(
            "auto",
            "none",
            "full",
            "projected",
            "incremental",
            "joint-inertial",
            "keyframe-graph",
        ),
        default="full",
    )
    parser.add_argument("--max-scale-disagreement-ratio", type=float, default=0.15)
    parser.add_argument(
        "--scale-disagreement-policy",
        choices=("fail", "diagnose"),
        default="fail",
        help=(
            "fail (default) keeps imu_stereo_metric_scale_disagreement blocking; "
            "diagnose drops only that one name from the blocking set while keeping "
            "the joint log-mean scale and recording it in tolerated_failures"
        ),
    )
    parser.add_argument(
        "--write-failed-output",
        action="store_true",
        help="write the rejected trajectory for diagnostics without changing FAIL status",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.orientation_node_stride < 1:
        parser.error("--orientation-node-stride must be positive")
    if args.position_node_stride < 1:
        parser.error("--position-node-stride must be positive")
    if args.minimum_stereo_sample_hop < 1:
        parser.error("--minimum-stereo-sample-hop must be positive")
    if not 0.0 <= args.minimum_learned_stereo_confidence <= 1.0:
        parser.error("--minimum-learned-stereo-confidence must lie in [0, 1]")
    if args.maximum_fixed_pnp_free_rotation_delta_deg <= 0.0:
        parser.error(
            "--maximum-fixed-pnp-free-rotation-delta-deg must be positive"
        )
    if args.maximum_fixed_pnp_reprojection_p95_px <= 0.0:
        parser.error("--maximum-fixed-pnp-reprojection-p95-px must be positive")
    if args.relative_motion_sigma_m <= 0.0:
        parser.error("--relative-motion-sigma-m must be positive")
    if args.visual_position_sigma_m <= 0.0:
        parser.error("--visual-position-sigma-m must be positive")
    if args.weak_visual_position_sigma_m < args.visual_position_sigma_m:
        parser.error(
            "--weak-visual-position-sigma-m must be at least --visual-position-sigma-m"
        )
    if args.weak_visual_trigger_disagreement_mm <= 0.0:
        parser.error("--weak-visual-trigger-disagreement-mm must be positive")
    if args.joint_max_correction_mm <= 0.0:
        parser.error("--joint-max-correction-mm must be positive")
    if bool(args.relative_motion_trajectory) != bool(args.relative_motion_report):
        parser.error(
            "--relative-motion-trajectory and --relative-motion-report must be provided together"
        )
    if args.full_rate_max_correction_mm <= 0.0:
        parser.error("--full-rate-max-correction-mm must be positive")
    report = run(args)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["result"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
