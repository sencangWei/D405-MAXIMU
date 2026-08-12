#!/usr/bin/env python3
"""Evaluate a timestamped SLAM trajectory against external 6-DoF ground truth.

Both CSV files use: t_sec,x,y,z,qw,qx,qy,qz. Ground truth is scoring-only and
is never published to or consumed by the SLAM process.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial.transform import Rotation, Slerp


def load_trajectory(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rows = list(csv.DictReader(path.open(newline="")))
    required = {"t_sec", "x", "y", "z", "qw", "qx", "qy", "qz"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"invalid trajectory schema: {path}")
    times = np.array([float(row["t_sec"]) for row in rows])
    positions = np.array(
        [[float(row[axis]) for axis in ("x", "y", "z")] for row in rows]
    )
    quaternions_xyzw = np.array(
        [
            [
                float(row["qx"]),
                float(row["qy"]),
                float(row["qz"]),
                float(row["qw"]),
            ]
            for row in rows
        ]
    )
    if np.any(np.diff(times) <= 0):
        raise ValueError(f"timestamps must be strictly increasing: {path}")
    return times, positions, quaternions_xyzw


def interpolate_ground_truth(
    estimate_times: np.ndarray,
    gt_times: np.ndarray,
    gt_positions: np.ndarray,
    gt_quaternions: np.ndarray,
    max_gap_s: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    inside = (estimate_times >= gt_times[0]) & (estimate_times <= gt_times[-1])
    selected_times = estimate_times[inside]
    right = np.searchsorted(gt_times, selected_times, side="right")
    right = np.clip(right, 1, len(gt_times) - 1)
    left = right - 1
    gaps = gt_times[right] - gt_times[left]
    valid = gaps <= max_gap_s
    selected_times = selected_times[valid]
    left, right, gaps = left[valid], right[valid], gaps[valid]
    alpha = (selected_times - gt_times[left]) / gaps
    positions = (
        (1.0 - alpha[:, None]) * gt_positions[left]
        + alpha[:, None] * gt_positions[right]
    )
    rotations = Slerp(gt_times, Rotation.from_quat(gt_quaternions))(selected_times)
    return inside, valid, np.column_stack((selected_times, positions)), rotations.as_quat()


def rigid_align(source: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    source_center = source.mean(axis=0)
    target_center = target.mean(axis=0)
    covariance = (source - source_center).T @ (target - target_center)
    u, _, vt = np.linalg.svd(covariance)
    rotation = vt.T @ u.T
    if np.linalg.det(rotation) < 0:
        vt[-1] *= -1
        rotation = vt.T @ u.T
    translation = target_center - rotation @ source_center
    return rotation, translation


def load_opencv_matrix(path: Path, key: str) -> np.ndarray:
    storage = cv2.FileStorage(str(path), cv2.FileStorage_READ)
    matrix = storage.getNode(key).mat()
    storage.release()
    if matrix is None or matrix.shape != (4, 4):
        raise ValueError(f"missing 4x4 {key} in {path}")
    return matrix


def body_trajectory_to_camera(
    positions: np.ndarray,
    quaternions: np.ndarray,
    body_t_camera: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    body_rotations = Rotation.from_quat(quaternions)
    camera_rotation_in_body = Rotation.from_matrix(body_t_camera[:3, :3])
    camera_position_in_body = body_t_camera[:3, 3]
    camera_positions = positions + body_rotations.apply(camera_position_in_body)
    camera_rotations = body_rotations * camera_rotation_in_body
    return camera_positions, camera_rotations.as_quat()


def pose_errors(
    estimate_positions: np.ndarray,
    estimate_quaternions: np.ndarray,
    gt_positions: np.ndarray,
    gt_quaternions: np.ndarray,
    delta: int,
) -> dict:
    alignment_rotation, alignment_translation = rigid_align(
        estimate_positions, gt_positions
    )
    aligned_positions = estimate_positions @ alignment_rotation.T + alignment_translation
    aligned_rotations = Rotation.from_matrix(alignment_rotation) * Rotation.from_quat(
        estimate_quaternions
    )
    gt_rotations = Rotation.from_quat(gt_quaternions)

    absolute_translation = np.linalg.norm(aligned_positions - gt_positions, axis=1)
    absolute_rotation_deg = np.degrees(
        (gt_rotations.inv() * aligned_rotations).magnitude()
    )
    if len(aligned_positions) <= delta:
        raise ValueError("trajectory too short for requested RPE delta")
    estimate_relative_rotation = aligned_rotations[:-delta].inv() * aligned_rotations[delta:]
    gt_relative_rotation = gt_rotations[:-delta].inv() * gt_rotations[delta:]
    estimate_delta_world = aligned_positions[delta:] - aligned_positions[:-delta]
    gt_delta_world = gt_positions[delta:] - gt_positions[:-delta]
    estimate_delta_local = aligned_rotations[:-delta].inv().apply(
        estimate_delta_world
    )
    gt_delta_local = gt_rotations[:-delta].inv().apply(gt_delta_world)
    relative_translation = np.linalg.norm(
        estimate_delta_local - gt_delta_local, axis=1
    )
    relative_rotation_deg = np.degrees(
        (gt_relative_rotation.inv() * estimate_relative_rotation).magnitude()
    )
    gt_path_m = float(np.linalg.norm(np.diff(gt_positions, axis=0), axis=1).sum())
    endpoint_error_m = float(
        np.linalg.norm(
            (aligned_positions[-1] - aligned_positions[0])
            - (gt_positions[-1] - gt_positions[0])
        )
    )
    z_error = aligned_positions[:, 2] - gt_positions[:, 2]
    return {
        "samples": int(len(gt_positions)),
        "alignment": "SE3_estimate_to_external_ground_truth_no_scale",
        "ate_translation_rmse_m": float(np.sqrt(np.mean(absolute_translation**2))),
        "ate_translation_median_m": float(np.median(absolute_translation)),
        "ate_translation_p95_m": float(np.percentile(absolute_translation, 95)),
        "ate_rotation_rmse_deg": float(np.sqrt(np.mean(absolute_rotation_deg**2))),
        "rpe_delta_samples": delta,
        "rpe_translation_rmse_m": float(np.sqrt(np.mean(relative_translation**2))),
        "rpe_rotation_rmse_deg": float(np.sqrt(np.mean(relative_rotation_deg**2))),
        "ground_truth_path_length_m": gt_path_m,
        "endpoint_drift_m": endpoint_error_m,
        "endpoint_drift_percent_of_path": (
            100.0 * endpoint_error_m / gt_path_m if gt_path_m > 0 else None
        ),
        "z_rmse_m": float(np.sqrt(np.mean(z_error**2))),
        "z_p95_abs_m": float(np.percentile(np.abs(z_error), 95)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--estimate", type=Path, required=True)
    parser.add_argument("--ground-truth", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-interpolation-gap-s", type=float, default=0.1)
    parser.add_argument("--rpe-delta-samples", type=int, default=30)
    parser.add_argument(
        "--estimate-body-t-camera-yaml",
        type=Path,
        help="含 body_T_cam0 的 VINS YAML；先把估计 body 位姿转换到相机中心",
    )
    parser.add_argument("--body-t-camera-key", default="body_T_cam0")
    args = parser.parse_args()

    estimate_time, estimate_position, estimate_quaternion = load_trajectory(args.estimate)
    estimate_frame = "as_recorded"
    if args.estimate_body_t_camera_yaml:
        body_t_camera = load_opencv_matrix(
            args.estimate_body_t_camera_yaml, args.body_t_camera_key
        )
        estimate_position, estimate_quaternion = body_trajectory_to_camera(
            estimate_position, estimate_quaternion, body_t_camera
        )
        estimate_frame = f"camera_via_{args.body_t_camera_key}"
    gt_time, gt_position, gt_quaternion = load_trajectory(args.ground_truth)
    inside, valid, interpolated, interpolated_quaternion = interpolate_ground_truth(
        estimate_time,
        gt_time,
        gt_position,
        gt_quaternion,
        args.max_interpolation_gap_s,
    )
    selected_position = estimate_position[inside][valid]
    selected_quaternion = estimate_quaternion[inside][valid]
    if len(selected_position) < max(20, args.rpe_delta_samples + 1):
        raise ValueError("insufficient timestamp-overlapped trajectory samples")
    metrics = pose_errors(
        selected_position,
        selected_quaternion,
        interpolated[:, 1:],
        interpolated_quaternion,
        args.rpe_delta_samples,
    )
    metrics.update(
        {
            "scope": "external_ground_truth_product_evaluation",
            "estimate": str(args.estimate.resolve()),
            "ground_truth": str(args.ground_truth.resolve()),
            "max_interpolation_gap_s": args.max_interpolation_gap_s,
            "estimate_frame": estimate_frame,
        }
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
