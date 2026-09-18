#!/usr/bin/env python3
"""Fuse independent RGB and left-IR MASt3R trajectories without external truth."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation, Slerp


FIELDS = ("t_sec", "x", "y", "z", "qw", "qx", "qy", "qz")


def load_trajectory(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    if not rows or not set(FIELDS).issubset(rows[0]):
        raise ValueError(f"invalid trajectory schema: {path}")
    times = np.asarray([float(row["t_sec"]) for row in rows])
    positions = np.asarray(
        [[float(row[axis]) for axis in ("x", "y", "z")] for row in rows]
    )
    quaternions = np.asarray(
        [
            [float(row[axis]) for axis in ("qx", "qy", "qz", "qw")]
            for row in rows
        ]
    )
    if np.any(np.diff(times) <= 0):
        raise ValueError(f"timestamps must be strictly increasing: {path}")
    return times, positions, quaternions


def similarity_align(
    source: np.ndarray, target: np.ndarray
) -> tuple[float, np.ndarray, np.ndarray]:
    source_center = source.mean(axis=0)
    target_center = target.mean(axis=0)
    centered_source = source - source_center
    centered_target = target - target_center
    u, singular_values, vt = np.linalg.svd(centered_source.T @ centered_target)
    rotation = vt.T @ u.T
    if np.linalg.det(rotation) < 0:
        vt[-1] *= -1
        singular_values[-1] *= -1
        rotation = vt.T @ u.T
    energy = float(np.sum(centered_source**2))
    if energy <= np.finfo(float).eps:
        raise ValueError("source trajectory has no translation excitation")
    scale = float(np.sum(singular_values) / energy)
    translation = target_center - scale * rotation @ source_center
    return scale, rotation, translation


def robust_similarity_align(
    source: np.ndarray, target: np.ndarray
) -> tuple[float, np.ndarray, np.ndarray, np.ndarray]:
    inliers = np.ones(len(source), dtype=bool)
    for _ in range(4):
        scale, rotation, translation = similarity_align(
            source[inliers], target[inliers]
        )
        aligned = scale * (source @ rotation.T) + translation
        residuals = np.linalg.norm(aligned - target, axis=1)
        median = float(np.median(residuals))
        mad = float(np.median(np.abs(residuals - median)))
        threshold = median + max(3.0 * 1.4826 * mad, 0.003)
        updated = residuals <= threshold
        if np.array_equal(updated, inliers):
            break
        if np.count_nonzero(updated) < max(20, len(source) // 2):
            break
        inliers = updated
    scale, rotation, translation = similarity_align(source[inliers], target[inliers])
    return scale, rotation, translation, inliers


def fuse_trajectories(
    rgb_times: np.ndarray,
    rgb_positions: np.ndarray,
    rgb_quaternions: np.ndarray,
    ir_times: np.ndarray,
    ir_positions: np.ndarray,
    ir_quaternions: np.ndarray,
    ir_weight: float = 0.5,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    if not 0.0 <= ir_weight <= 1.0:
        raise ValueError("IR fusion weight must be within [0, 1]")
    inside = (rgb_times >= ir_times[0]) & (rgb_times <= ir_times[-1])
    common_times = rgb_times[inside]
    if len(common_times) < 20:
        raise ValueError("RGB/IR trajectories have insufficient timestamp overlap")
    rgb_positions = rgb_positions[inside]
    rgb_rotations = Rotation.from_quat(rgb_quaternions[inside])
    ir_positions = np.column_stack(
        [np.interp(common_times, ir_times, ir_positions[:, axis]) for axis in range(3)]
    )
    ir_rotations = Slerp(ir_times, Rotation.from_quat(ir_quaternions))(common_times)

    scale, alignment_rotation, translation, inliers = robust_similarity_align(
        ir_positions, rgb_positions
    )
    aligned_ir_positions = scale * (ir_positions @ alignment_rotation.T) + translation
    aligned_ir_rotations = Rotation.from_matrix(alignment_rotation) * ir_rotations
    relative_rotation = rgb_rotations.inv() * aligned_ir_rotations
    residuals = np.linalg.norm(aligned_ir_positions - rgb_positions, axis=1)
    residual_median = float(np.median(residuals))
    residual_mad = float(np.median(np.abs(residuals - residual_median)))
    consensus_threshold = residual_median + max(
        3.0 * 1.4826 * residual_mad, 0.003
    )
    adaptive_ir_weight = ir_weight * np.minimum(
        1.0, consensus_threshold / np.maximum(residuals, 1e-12)
    )
    fused_positions = (
        (1.0 - adaptive_ir_weight[:, None]) * rgb_positions
        + adaptive_ir_weight[:, None] * aligned_ir_positions
    )
    fused_rotations = rgb_rotations * Rotation.from_rotvec(
        adaptive_ir_weight[:, None] * relative_rotation.as_rotvec()
    )
    quality = {
        "schema": "umi_mast3r_dual_stream_fusion_v1",
        "result": "PASS",
        "slam_supervision": False,
        "inputs": "independent MASt3R RGB and left-IR trajectories only",
        "external_ground_truth_used": False,
        "samples": int(len(common_times)),
        "ir_weight": float(ir_weight),
        "weight_policy": "cross_stream_huber_consensus",
        "consensus_threshold_mast3r_units": float(consensus_threshold),
        "effective_ir_weight_min": float(np.min(adaptive_ir_weight)),
        "effective_ir_weight_median": float(np.median(adaptive_ir_weight)),
        "ir_to_rgb_scale": float(scale),
        "alignment_inliers": int(np.count_nonzero(inliers)),
        "alignment_residual_median_m": float(np.median(residuals)),
        "alignment_residual_p95_m": float(np.percentile(residuals, 95)),
        "alignment_residual_max_m": float(np.max(residuals)),
    }
    return common_times, fused_positions, fused_rotations.as_quat(), quality


def write_trajectory(
    path: Path, times: np.ndarray, positions: np.ndarray, quaternions: np.ndarray
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        for time_s, position, quaternion in zip(times, positions, quaternions):
            writer.writerow(
                {
                    "t_sec": f"{time_s:.9f}",
                    "x": f"{position[0]:.12f}",
                    "y": f"{position[1]:.12f}",
                    "z": f"{position[2]:.12f}",
                    "qw": f"{quaternion[3]:.12f}",
                    "qx": f"{quaternion[0]:.12f}",
                    "qy": f"{quaternion[1]:.12f}",
                    "qz": f"{quaternion[2]:.12f}",
                }
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rgb", type=Path, required=True)
    parser.add_argument("--infrared-left", type=Path, required=True)
    parser.add_argument("--ir-weight", type=float, default=0.5)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    fused = fuse_trajectories(
        *load_trajectory(args.rgb),
        *load_trajectory(args.infrared_left),
        ir_weight=args.ir_weight,
    )
    times, positions, quaternions, report = fused
    report["rgb_trajectory"] = str(args.rgb.resolve())
    report["infrared_left_trajectory"] = str(args.infrared_left.resolve())
    report["output"] = str(args.output.resolve())
    write_trajectory(args.output, times, positions, quaternions)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
