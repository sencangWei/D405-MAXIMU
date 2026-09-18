#!/usr/bin/env python3
"""Fuse Docker2 short-term motion with MASt3R long-term geometry.

Only two SLAM trajectories from the same UMI recording and fixed camera/IMU
calibration are accepted. External reference trajectories are deliberately not
part of the interface.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.spatial.transform import Rotation, Slerp


FIELDS = ("t_sec", "x", "y", "z", "qw", "qx", "qy", "qz")


def load_trajectory(path: Path) -> tuple[np.ndarray, np.ndarray, Rotation]:
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    if not rows or not set(FIELDS).issubset(rows[0]):
        raise ValueError(f"invalid trajectory: {path}")
    times = np.asarray([float(row["t_sec"]) for row in rows])
    positions = np.asarray(
        [[float(row[key]) for key in ("x", "y", "z")] for row in rows]
    )
    quaternions = np.asarray(
        [
            [float(row[key]) for key in ("qx", "qy", "qz", "qw")]
            for row in rows
        ]
    )
    if np.any(np.diff(times) <= 0.0):
        raise ValueError(f"trajectory timestamps are not monotonic: {path}")
    return times, positions, Rotation.from_quat(quaternions)


def load_body_t_camera(path: Path, key: str = "body_T_cam0") -> np.ndarray:
    storage = cv2.FileStorage(str(path), cv2.FileStorage_READ)
    matrix = storage.getNode(key).mat()
    storage.release()
    if matrix is None or matrix.shape != (4, 4):
        raise ValueError(f"missing 4x4 {key} in {path}")
    return matrix


def camera_to_body(
    positions: np.ndarray,
    rotations: Rotation,
    body_t_camera: np.ndarray,
) -> tuple[np.ndarray, Rotation]:
    body_from_camera = Rotation.from_matrix(body_t_camera[:3, :3])
    body_rotations = rotations * body_from_camera.inv()
    body_positions = positions - body_rotations.apply(body_t_camera[:3, 3])
    return body_positions, body_rotations


def camera_to_body_with_body_orientation_prior(
    positions: np.ndarray,
    camera_rotations: Rotation,
    body_t_camera: np.ndarray,
    prior_body_rotations: Rotation,
) -> tuple[np.ndarray, Rotation, dict]:
    """Use the calibrated IMU attitude for the camera-to-body lever arm."""
    body_from_camera = Rotation.from_matrix(body_t_camera[:3, :3])
    visual_body_rotations = camera_rotations * body_from_camera.inv()
    world_alignment = (visual_body_rotations * prior_body_rotations.inv()).mean()
    aligned_prior = world_alignment * prior_body_rotations
    disagreement_deg = np.degrees(
        (visual_body_rotations.inv() * aligned_prior).magnitude()
    )
    body_positions = positions - aligned_prior.apply(body_t_camera[:3, 3])
    return body_positions, aligned_prior, {
        "source": "Docker2 calibrated 400Hz IMU attitude",
        "world_alignment_quaternion_xyzw": world_alignment.as_quat().tolist(),
        "visual_prior_disagreement_median_deg": float(
            np.median(disagreement_deg)
        ),
        "visual_prior_disagreement_p95_deg": float(
            np.percentile(disagreement_deg, 95)
        ),
        "visual_prior_disagreement_max_deg": float(np.max(disagreement_deg)),
        "policy": "orientation prior changes only body attitude and calibrated lever-arm conversion",
    }


def rigid_align(source: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
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


def robust_metric_scale_ratio(
    base_times: np.ndarray,
    base_positions: np.ndarray,
    metric_positions: np.ndarray,
    horizon_s: float,
    minimum_displacement_m: float = 0.010,
) -> tuple[float, dict]:
    rate_hz = 1.0 / float(np.median(np.diff(base_times)))
    hop = max(1, int(round(horizon_s * rate_hz)))
    base_distance = np.linalg.norm(
        base_positions[hop:] - base_positions[:-hop], axis=1
    )
    metric_distance = np.linalg.norm(
        metric_positions[hop:] - metric_positions[:-hop], axis=1
    )
    valid = (base_distance >= minimum_displacement_m) & (
        metric_distance >= minimum_displacement_m
    )
    ratios = metric_distance[valid] / base_distance[valid]
    if len(ratios) < 50:
        raise ValueError("insufficient excited windows for cross-SLAM metric scale")
    median = float(np.median(ratios))
    mad = float(np.median(np.abs(ratios - median)))
    tolerance = max(3.0 * 1.4826 * mad, 0.05 * median)
    inliers = np.abs(ratios - median) <= tolerance
    if np.count_nonzero(inliers) < 50:
        raise ValueError("insufficient robust cross-SLAM scale windows")
    ratio = float(np.median(ratios[inliers]))
    return ratio, {
        "horizon_s": horizon_s,
        "hop_samples": hop,
        "candidate_windows": int(len(ratios)),
        "robust_inliers": int(np.count_nonzero(inliers)),
        "ratio_median": ratio,
        "ratio_p10": float(np.percentile(ratios[inliers], 10)),
        "ratio_p90": float(np.percentile(ratios[inliers], 90)),
        "ratio_mad": mad,
    }


def joint_log_scale_ratio(
    metric_ratio: float, docker2_weight: float = 0.5
) -> tuple[float, float]:
    """Fuse stereo and Docker2 metric scales in log space."""
    if metric_ratio <= 0.0:
        raise ValueError("metric scale ratio must be positive")
    if not 0.0 <= docker2_weight <= 1.0:
        raise ValueError("Docker2 scale weight must lie in [0, 1]")
    relative_disagreement = abs(metric_ratio - 1.0) / (0.5 * (metric_ratio + 1.0))
    if relative_disagreement > 0.15:
        raise ValueError("Docker2 and stereo metric scales disagree by more than 15%")
    return float(metric_ratio**docker2_weight), float(relative_disagreement)


def complementary_positions(
    base_times: np.ndarray,
    base_positions: np.ndarray,
    metric_positions: np.ndarray,
    scale_ratio: float,
    smoothing_s: float,
    local_weight: float,
    adaptive_local_weight: bool = False,
    roughness_threshold_m: float = 0.009,
    adaptive_weight_strength: float = 0.3,
) -> tuple[np.ndarray, dict]:
    if not 0.0 <= local_weight <= 1.0:
        raise ValueError("local weight must lie in [0, 1]")
    if smoothing_s <= 0.0:
        raise ValueError("smoothing duration must be positive")
    if roughness_threshold_m <= 0.0:
        raise ValueError("roughness threshold must be positive")
    if not 0.0 <= adaptive_weight_strength <= 1.0:
        raise ValueError("adaptive weight strength must lie in [0, 1]")
    scaled_base = base_positions[0] + scale_ratio * (
        base_positions - base_positions[0]
    )
    alignment_rotation, alignment_translation = rigid_align(
        metric_positions, scaled_base
    )
    aligned_metric = (
        alignment_rotation @ metric_positions.T
    ).T + alignment_translation
    disagreement = aligned_metric - scaled_base
    rate_hz = 1.0 / float(np.median(np.diff(base_times)))
    low_frequency = gaussian_filter1d(
        disagreement,
        sigma=smoothing_s * rate_hz,
        axis=0,
        mode="nearest",
    )
    high_frequency = disagreement - low_frequency
    effective_weight = np.full(len(base_positions), local_weight, dtype=float)
    if adaptive_local_weight:
        def local_roughness(trajectory: np.ndarray) -> np.ndarray:
            neighbors = 0.5 * (
                np.vstack((trajectory[0], trajectory[:-2], trajectory[-1]))
                + np.vstack((trajectory[0], trajectory[2:], trajectory[-1]))
            )
            return np.linalg.norm(trajectory - neighbors, axis=1)

        base_roughness = local_roughness(scaled_base)
        metric_roughness = local_roughness(aligned_metric)
        maximum_roughness = np.maximum(base_roughness, metric_roughness)
        activation = 1.0 / (
            1.0
            + np.exp(
                -(maximum_roughness - roughness_threshold_m) / 0.0015
            )
        )
        relative_roughness = (base_roughness - metric_roughness) / (
            base_roughness + metric_roughness + 0.0005
        )
        effective_weight = np.clip(
            local_weight
            + adaptive_weight_strength * activation * relative_roughness,
            0.02,
            0.98,
        )
    injected = effective_weight[:, None] * high_frequency
    fused = scaled_base + injected
    return fused, {
        "smoothing_s": smoothing_s,
        "local_weight": local_weight,
        "adaptive_local_weight": adaptive_local_weight,
        "roughness_threshold_mm": roughness_threshold_m * 1000.0,
        "adaptive_weight_strength": adaptive_weight_strength,
        "effective_local_weight_min": float(np.min(effective_weight)),
        "effective_local_weight_median": float(np.median(effective_weight)),
        "effective_local_weight_max": float(np.max(effective_weight)),
        "input_disagreement_p95_mm": float(
            np.percentile(np.linalg.norm(disagreement, axis=1), 95) * 1000.0
        ),
        "injected_correction_median_mm": float(
            np.median(np.linalg.norm(injected, axis=1)) * 1000.0
        ),
        "injected_correction_p95_mm": float(
            np.percentile(np.linalg.norm(injected, axis=1), 95) * 1000.0
        ),
        "injected_correction_max_mm": float(
            np.max(np.linalg.norm(injected, axis=1)) * 1000.0
        ),
        "alignment_rotation": alignment_rotation.tolist(),
        "alignment_translation_m": alignment_translation.tolist(),
    }


def write_trajectory(
    path: Path,
    times: np.ndarray,
    positions: np.ndarray,
    rotations: Rotation,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    quaternions = rotations.as_quat()
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(FIELDS)
        for time_s, position, quaternion in zip(times, positions, quaternions):
            writer.writerow(
                [
                    f"{time_s:.9f}",
                    *(f"{value:.9f}" for value in position),
                    f"{quaternion[3]:.9f}",
                    *(f"{value:.9f}" for value in quaternion[:3]),
                ]
            )


def run(args: argparse.Namespace) -> dict:
    mast3r_times, mast3r_positions, mast3r_rotations = load_trajectory(args.mast3r)
    docker_times, docker_positions, docker_rotations = load_trajectory(args.docker2)
    body_t_camera = load_body_t_camera(args.body_t_camera_yaml)
    inside = (mast3r_times >= docker_times[0]) & (mast3r_times <= docker_times[-1])
    common_times = mast3r_times[inside]
    if len(common_times) < 100:
        raise ValueError("insufficient Docker2/MASt3R timestamp overlap")
    mast3r_positions = mast3r_positions[inside]
    mast3r_rotations = mast3r_rotations[inside]
    docker_positions = np.column_stack(
        [
            np.interp(common_times, docker_times, docker_positions[:, axis])
            for axis in range(3)
        ]
    )
    docker_rotations = Slerp(docker_times, docker_rotations)(common_times)
    orientation_prior_quality = None
    if args.use_docker2_orientation_for_lever_arm:
        (
            mast3r_positions,
            mast3r_rotations,
            orientation_prior_quality,
        ) = camera_to_body_with_body_orientation_prior(
            mast3r_positions,
            mast3r_rotations,
            body_t_camera,
            docker_rotations,
        )
    else:
        mast3r_positions, mast3r_rotations = camera_to_body(
            mast3r_positions, mast3r_rotations, body_t_camera
        )
    initial_rotation, initial_translation = rigid_align(
        docker_positions, mast3r_positions
    )
    initially_aligned_docker = (
        initial_rotation @ docker_positions.T
    ).T + initial_translation
    docker2_scale_ratio, scale_quality = robust_metric_scale_ratio(
        common_times,
        mast3r_positions,
        initially_aligned_docker,
        args.scale_horizon_s,
    )
    scale_ratio, scale_disagreement = joint_log_scale_ratio(
        docker2_scale_ratio, args.docker2_scale_weight
    )
    scale_quality.update(
        {
            "docker2_to_mast3r_ratio": docker2_scale_ratio,
            "stereo_reference_ratio": 1.0,
            "selected_joint_ratio": scale_ratio,
            "relative_disagreement": scale_disagreement,
            "selection_policy": "weighted_log_mean_of_stereo_and_docker2",
            "docker2_scale_weight": args.docker2_scale_weight,
        }
    )
    fused_positions, fusion_quality = complementary_positions(
        common_times,
        mast3r_positions,
        docker_positions,
        scale_ratio,
        args.smoothing_s,
        args.docker2_local_weight,
        args.adaptive_local_weight,
        args.roughness_threshold_mm / 1000.0,
        args.adaptive_weight_strength,
    )
    write_trajectory(args.output, common_times, fused_positions, mast3r_rotations)
    report = {
        "schema": "umi_docker2_mast3r_complementary_v1",
        "result": "PASS",
        "slam_supervision": False,
        "external_ground_truth_used": False,
        "algorithm": (
            "MASt3R long-term pose + Docker2 high-pass local metric motion"
        ),
        "inputs": {
            "mast3r_camera_trajectory": str(args.mast3r.resolve()),
            "docker2_body_trajectory": str(args.docker2.resolve()),
            "body_camera_calibration": str(args.body_t_camera_yaml.resolve()),
        },
        "samples": int(len(common_times)),
        "timestamp_overlap_s": float(common_times[-1] - common_times[0]),
        "scale": scale_quality,
        "fusion": fusion_quality,
        "orientation_prior": orientation_prior_quality,
        "output_frame": "body_imu_origin",
        "output": str(args.output.resolve()),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mast3r", type=Path, required=True)
    parser.add_argument("--docker2", type=Path, required=True)
    parser.add_argument("--body-t-camera-yaml", type=Path, required=True)
    parser.add_argument("--scale-horizon-s", type=float, default=1.0)
    parser.add_argument("--smoothing-s", type=float, default=4.0)
    parser.add_argument("--docker2-local-weight", type=float, default=0.5)
    parser.add_argument("--docker2-scale-weight", type=float, default=0.5)
    parser.add_argument("--adaptive-local-weight", action="store_true")
    parser.add_argument("--roughness-threshold-mm", type=float, default=9.0)
    parser.add_argument("--adaptive-weight-strength", type=float, default=0.3)
    parser.add_argument(
        "--use-docker2-orientation-for-lever-arm", action="store_true"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.scale_horizon_s <= 0.0:
        parser.error("--scale-horizon-s must be positive")
    if not 0.0 <= args.docker2_scale_weight <= 1.0:
        parser.error("--docker2-scale-weight must lie in [0, 1]")
    if args.roughness_threshold_mm <= 0.0:
        parser.error("--roughness-threshold-mm must be positive")
    if not 0.0 <= args.adaptive_weight_strength <= 1.0:
        parser.error("--adaptive-weight-strength must lie in [0, 1]")
    report = run(args)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
