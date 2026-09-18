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


def similarity_align(
    source: np.ndarray, target: np.ndarray
) -> tuple[float, np.ndarray, np.ndarray]:
    """Best-fit Sim(3), reported only as a monocular shape diagnostic."""
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
    source_energy = float(np.sum(centered_source**2))
    if source_energy <= np.finfo(float).eps:
        raise ValueError("source trajectory has no translation for Sim(3) diagnostic")
    scale = float(np.sum(singular_values) / source_energy)
    translation = target_center - scale * rotation @ source_center
    return scale, rotation, translation


def orientation_align(
    source_quaternions: np.ndarray,
    target_quaternions: np.ndarray,
) -> np.ndarray:
    """Return the constant world rotation that best aligns source attitudes."""
    source = Rotation.from_quat(source_quaternions)
    target = Rotation.from_quat(target_quaternions)
    return (target * source.inv()).mean().as_matrix()


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


def camera_trajectory_to_body(
    positions: np.ndarray,
    quaternions: np.ndarray,
    body_t_camera: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    camera_rotations = Rotation.from_quat(quaternions)
    camera_rotation_in_body = Rotation.from_matrix(body_t_camera[:3, :3])
    body_rotations = camera_rotations * camera_rotation_in_body.inv()
    camera_position_in_body = body_t_camera[:3, 3]
    body_positions = positions - body_rotations.apply(camera_position_in_body)
    return body_positions, body_rotations.as_quat()


def camera_adjusted_body_transform(
    body_t_reference_camera: np.ndarray, adjustment_report: dict
) -> np.ndarray:
    """Compose a body-to-left-IR calibration with the factory color extrinsic."""
    if adjustment_report.get("observation_frame") != "color_camera_i":
        raise ValueError("camera adjustment report is not expressed in the color frame")
    calibration = adjustment_report.get("factory_stereo_calibration", {})
    color_rotation_from_left = np.asarray(
        calibration.get("color_rotation_from_left"), dtype=float
    )
    color_translation_from_left = np.asarray(
        calibration.get("color_translation_from_left_m"), dtype=float
    )
    if color_rotation_from_left.shape != (3, 3):
        raise ValueError("camera adjustment report lacks left-IR to color rotation")
    if color_translation_from_left.shape != (3,):
        raise ValueError("camera adjustment report lacks left-IR to color translation")
    color_t_left = np.eye(4)
    color_t_left[:3, :3] = color_rotation_from_left
    color_t_left[:3, 3] = color_translation_from_left
    return body_t_reference_camera @ np.linalg.inv(color_t_left)


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
    attitude_alignment_rotation = orientation_align(
        estimate_quaternions, gt_quaternions
    )
    attitude_alignment_translation = (
        gt_positions.mean(axis=0)
        - attitude_alignment_rotation @ estimate_positions.mean(axis=0)
    )
    attitude_aligned_positions = (
        estimate_positions @ attitude_alignment_rotation.T
        + attitude_alignment_translation
    )
    attitude_aligned_rotations = (
        Rotation.from_matrix(attitude_alignment_rotation)
        * Rotation.from_quat(estimate_quaternions)
    )
    attitude_aligned_translation = np.linalg.norm(
        attitude_aligned_positions - gt_positions, axis=1
    )
    attitude_aligned_rotation_deg = np.degrees(
        (gt_rotations.inv() * attitude_aligned_rotations).magnitude()
    )
    alignment_disagreement_deg = np.degrees(
        (
            Rotation.from_matrix(attitude_alignment_rotation)
            * Rotation.from_matrix(alignment_rotation).inv()
        ).magnitude()
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
    sim3_scale, sim3_rotation, sim3_translation = similarity_align(
        estimate_positions, gt_positions
    )
    sim3_aligned_positions = (
        sim3_scale * (estimate_positions @ sim3_rotation.T) + sim3_translation
    )
    sim3_translation_error = np.linalg.norm(
        sim3_aligned_positions - gt_positions, axis=1
    )
    return {
        "samples": int(len(gt_positions)),
        "alignment": "SE3_estimate_to_external_ground_truth_no_scale",
        "ate_translation_rmse_m": float(np.sqrt(np.mean(absolute_translation**2))),
        "ate_translation_mean_m": float(np.mean(absolute_translation)),
        "ate_translation_min_m": float(np.min(absolute_translation)),
        "ate_translation_median_m": float(np.median(absolute_translation)),
        "ate_translation_p95_m": float(np.percentile(absolute_translation, 95)),
        "ate_translation_max_m": float(np.max(absolute_translation)),
        "ate_translation_within_5mm_ratio": float(np.mean(absolute_translation <= 0.005)),
        "ate_translation_within_10mm_ratio": float(np.mean(absolute_translation <= 0.010)),
        "ate_translation_within_20mm_ratio": float(np.mean(absolute_translation <= 0.020)),
        "ate_rotation_rmse_deg": float(np.sqrt(np.mean(absolute_rotation_deg**2))),
        "attitude_aligned_ate_translation_rmse_m": float(
            np.sqrt(np.mean(attitude_aligned_translation**2))
        ),
        "attitude_aligned_ate_rotation_rmse_deg": float(
            np.sqrt(np.mean(attitude_aligned_rotation_deg**2))
        ),
        "position_vs_attitude_alignment_rotation_deg": float(
            alignment_disagreement_deg
        ),
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
        "sim3_diagnostic_scale_gt_per_estimate": sim3_scale,
        "sim3_diagnostic_ate_rmse_m": float(
            np.sqrt(np.mean(sim3_translation_error**2))
        ),
        "sim3_diagnostic_ate_p95_m": float(
            np.percentile(sim3_translation_error, 95)
        ),
    }


def acceptance(metrics: dict, max_ate_rmse_mm: float, max_ate_p95_mm: float,
               max_ate_max_mm: float,
               min_within_10mm_ratio: float, max_rotation_rmse_deg: float,
               min_timestamp_overlap_ratio: float) -> dict:
    thresholds = {
        "max_ate_rmse_mm": max_ate_rmse_mm,
        "max_ate_p95_mm": max_ate_p95_mm,
        "max_ate_max_mm": max_ate_max_mm,
        "min_within_10mm_ratio": min_within_10mm_ratio,
        "max_rotation_rmse_deg": max_rotation_rmse_deg,
        "min_timestamp_overlap_ratio": min_timestamp_overlap_ratio,
    }
    failures = []
    if metrics["ate_translation_rmse_m"] * 1000.0 > max_ate_rmse_mm:
        failures.append("ate_translation_rmse_over_limit")
    if metrics["ate_translation_p95_m"] * 1000.0 > max_ate_p95_mm:
        failures.append("ate_translation_p95_over_limit")
    if metrics["ate_translation_max_m"] * 1000.0 > max_ate_max_mm:
        failures.append("ate_translation_max_over_limit")
    if metrics["ate_translation_within_10mm_ratio"] < min_within_10mm_ratio:
        failures.append("within_10mm_ratio_below_limit")
    if metrics["ate_rotation_rmse_deg"] > max_rotation_rmse_deg:
        failures.append("ate_rotation_rmse_over_limit")
    if metrics["timestamp_overlap_ratio"] < min_timestamp_overlap_ratio:
        failures.append("timestamp_overlap_ratio_below_limit")
    return {
        "result": "PASS" if not failures else "FAIL",
        "thresholds": thresholds,
        "failures": failures,
    }


def write_plot(
    timestamps: np.ndarray,
    estimate_positions: np.ndarray,
    gt_positions: np.ndarray,
    output: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rotation, translation = rigid_align(estimate_positions, gt_positions)
    aligned = estimate_positions @ rotation.T + translation
    errors_mm = np.linalg.norm(aligned - gt_positions, axis=1) * 1000.0
    elapsed = timestamps - timestamps[0]

    figure, axes = plt.subplots(1, 3, figsize=(16, 5))
    for axis, x_index, y_index, x_label, y_label in (
        (axes[0], 0, 1, "X (m)", "Y (m)"),
        (axes[1], 0, 2, "X (m)", "Z (m)"),
    ):
        axis.plot(gt_positions[:, x_index], gt_positions[:, y_index], "k-", label="External GT")
        axis.plot(aligned[:, x_index], aligned[:, y_index], "#00a86b", label="SLAM aligned")
        axis.set_xlabel(x_label)
        axis.set_ylabel(y_label)
        axis.axis("equal")
        axis.grid(True, alpha=0.3)
        axis.legend()
    axes[0].set_title("Top view")
    axes[1].set_title("Side view")
    axes[2].plot(elapsed, errors_mm, color="#d62728")
    axes[2].axhline(10.0, color="black", linestyle="--", label="10 mm")
    axes[2].set_xlabel("Elapsed time (s)")
    axes[2].set_ylabel("Position error (mm)")
    axes[2].set_title("SE(3)-aligned ATE")
    axes[2].grid(True, alpha=0.3)
    axes[2].legend()
    figure.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=180)
    plt.close(figure)


def write_markdown(metrics: dict, output: Path) -> None:
    def mm(key: str) -> str:
        return f"{metrics[key] * 1000.0:.3f} mm"

    lines = [
        "# Lighthouse 外部真值 SLAM 精度报告",
        "",
        f"判定：**{metrics['result']}**",
        "",
        "| 指标 | 结果 |",
        "| --- | ---: |",
        f"| ATE RMSE | {mm('ate_translation_rmse_m')} |",
        f"| ATE 平均 | {mm('ate_translation_mean_m')} |",
        f"| ATE 最小 | {mm('ate_translation_min_m')} |",
        f"| ATE 中位 | {mm('ate_translation_median_m')} |",
        f"| ATE P95 | {mm('ate_translation_p95_m')} |",
        f"| ATE 最大 | {mm('ate_translation_max_m')} |",
        f"| 10 mm 内比例 | {metrics['ate_translation_within_10mm_ratio'] * 100.0:.3f}% |",
        f"| 姿态 RMSE | {metrics['ate_rotation_rmse_deg']:.3f}° |",
        f"| RPE 平移 RMSE | {mm('rpe_translation_rmse_m')} |",
        f"| 终点漂移 | {mm('endpoint_drift_m')} |",
        f"| Sim(3)形状诊断 RMSE | {mm('sim3_diagnostic_ate_rmse_m')} |",
        f"| Sim(3)最优尺度(gt/estimate) | {metrics['sim3_diagnostic_scale_gt_per_estimate']:.6f} |",
        "",
        "主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。",
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--estimate", type=Path, required=True)
    parser.add_argument("--ground-truth", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-interpolation-gap-s", type=float, default=0.1)
    parser.add_argument("--rpe-delta-samples", type=int, default=30)
    parser.add_argument("--plot", type=Path)
    parser.add_argument("--report-md", type=Path)
    parser.add_argument("--max-ate-rmse-mm", type=float, default=10.0)
    parser.add_argument("--max-ate-p95-mm", type=float, default=10.0)
    parser.add_argument("--max-ate-max-mm", type=float, default=10.0)
    parser.add_argument("--min-within-10mm-ratio", type=float, default=0.95)
    parser.add_argument("--max-rotation-rmse-deg", type=float, default=2.0)
    parser.add_argument("--min-timestamp-overlap-ratio", type=float, default=0.98)
    parser.add_argument(
        "--estimate-body-t-camera-yaml",
        type=Path,
        help="含 body_T_cam0 的 VINS YAML；先把估计 body 位姿转换到相机中心",
    )
    parser.add_argument(
        "--estimate-camera-to-body-yaml",
        type=Path,
        help="含 body_T_cam0 的 VINS YAML；把估计相机位姿转换到 body/IMU 原点",
    )
    parser.add_argument(
        "--estimate-camera-adjustment-report",
        type=Path,
        help="含D405左IR到RGB出厂外参的双目报告；用于把RGB轨迹转换到body原点",
    )
    parser.add_argument(
        "--ground-truth-body-t-camera-yaml",
        type=Path,
        help="含 body_T_cam0 的 VINS YAML；把外部真值 body 位姿转换到相机中心",
    )
    parser.add_argument(
        "--ground-truth-camera-adjustment-report",
        type=Path,
        help="含D405左IR到RGB出厂外参的双目报告；用于评价RGB相机轨迹",
    )
    parser.add_argument("--body-t-camera-key", default="body_T_cam0")
    args = parser.parse_args()

    estimate_time, estimate_position, estimate_quaternion = load_trajectory(args.estimate)
    estimate_frame = "as_recorded"
    if args.estimate_body_t_camera_yaml and args.estimate_camera_to_body_yaml:
        parser.error("estimate frame conversion directions are mutually exclusive")
    if args.estimate_body_t_camera_yaml:
        body_t_camera = load_opencv_matrix(
            args.estimate_body_t_camera_yaml, args.body_t_camera_key
        )
        estimate_position, estimate_quaternion = body_trajectory_to_camera(
            estimate_position, estimate_quaternion, body_t_camera
        )
        estimate_frame = f"camera_via_{args.body_t_camera_key}"
    elif args.estimate_camera_to_body_yaml:
        body_t_camera = load_opencv_matrix(
            args.estimate_camera_to_body_yaml, args.body_t_camera_key
        )
        if args.estimate_camera_adjustment_report:
            adjustment_report = json.loads(
                args.estimate_camera_adjustment_report.read_text(encoding="utf-8")
            )
            body_t_camera = camera_adjusted_body_transform(
                body_t_camera, adjustment_report
            )
        estimate_position, estimate_quaternion = camera_trajectory_to_body(
            estimate_position, estimate_quaternion, body_t_camera
        )
        estimate_frame = (
            "body_via_color_camera_and_body_T_left_ir"
            if args.estimate_camera_adjustment_report
            else f"body_via_inverse_{args.body_t_camera_key}"
        )
    elif args.estimate_camera_adjustment_report:
        parser.error(
            "--estimate-camera-adjustment-report requires "
            "--estimate-camera-to-body-yaml"
        )
    gt_time, gt_position, gt_quaternion = load_trajectory(args.ground_truth)
    ground_truth_frame = "as_recorded"
    if args.ground_truth_body_t_camera_yaml:
        body_t_camera = load_opencv_matrix(
            args.ground_truth_body_t_camera_yaml, args.body_t_camera_key
        )
        if args.ground_truth_camera_adjustment_report:
            adjustment_report = json.loads(
                args.ground_truth_camera_adjustment_report.read_text(
                    encoding="utf-8"
                )
            )
            body_t_camera = camera_adjusted_body_transform(
                body_t_camera, adjustment_report
            )
        gt_position, gt_quaternion = body_trajectory_to_camera(
            gt_position, gt_quaternion, body_t_camera
        )
        ground_truth_frame = (
            "color_camera_via_body_T_left_ir_and_factory_color_T_left_ir"
            if args.ground_truth_camera_adjustment_report
            else f"camera_via_{args.body_t_camera_key}"
        )
    elif args.ground_truth_camera_adjustment_report:
        parser.error(
            "--ground-truth-camera-adjustment-report requires "
            "--ground-truth-body-t-camera-yaml"
        )
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
            "ground_truth_frame": ground_truth_frame,
            "estimate_samples_total": int(len(estimate_time)),
            "ground_truth_samples_total": int(len(gt_time)),
            "timestamp_overlap_samples": int(len(selected_position)),
            "timestamp_overlap_ratio": float(len(selected_position) / len(estimate_time)),
        }
    )
    metrics.update(
        acceptance(
            metrics,
            args.max_ate_rmse_mm,
            args.max_ate_p95_mm,
            args.max_ate_max_mm,
            args.min_within_10mm_ratio,
            args.max_rotation_rmse_deg,
            args.min_timestamp_overlap_ratio,
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.plot:
        write_plot(interpolated[:, 0], selected_position, interpolated[:, 1:], args.plot)
    if args.report_md:
        write_markdown(metrics, args.report_md)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0 if metrics["result"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
