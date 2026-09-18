#!/usr/bin/env python3
"""Calibrate Vive Tracker-to-IMU/body directly from stereo AprilGrid corners.

The fixed AprilGrid and Lighthouse tracker are the only external references.
No SLAM or VIO trajectory is accepted.  A single Tracker-to-body transform is
optimized jointly across captures while each capture gets its own fixed-grid
pose in the Lighthouse world frame.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path

import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from calibrate_lighthouse_aprilgrid import map_camera_times
from calibrate_lighthouse_umi import interpolate_tracker, load_tracker, summary
from collect_calib_data import load_aprilgrid_config
from extract_aprilgrid_ground_truth import (
    detected_points,
    load_camera_yaml,
    rsusb_stereo_image_iter,
    stereo_pose,
)
from replay_db3_to_ros2 import select_db3


FORMAL_CALIBRATION_ROOT = Path(
    "/home/robot/umi_docker2_product_1.0.0-20260829/"
    "docker2_release/formal_runtime_calibration"
)


@dataclass(frozen=True)
class CameraModel:
    intrinsic: np.ndarray
    distortion: np.ndarray
    body_T_camera: np.ndarray


@dataclass(frozen=True)
class CameraObservations:
    object_points: np.ndarray
    image_points: np.ndarray
    frame_indices: np.ndarray


@dataclass(frozen=True)
class BundleCapture:
    name: str
    tracker_poses: np.ndarray
    cameras: tuple[CameraObservations, CameraObservations]
    initial_world_T_grid: np.ndarray
    frame_reprojection_rmse_px: np.ndarray


def load_body_T_cameras(path: Path) -> tuple[np.ndarray, np.ndarray]:
    calibration = cv2.FileStorage(str(path), cv2.FileStorage_READ)
    if not calibration.isOpened():
        raise ValueError(f"cannot open body-camera calibration: {path}")
    transforms = tuple(
        calibration.getNode(name).mat() for name in ("body_T_cam0", "body_T_cam1")
    )
    calibration.release()
    if any(transform is None or transform.shape != (4, 4) for transform in transforms):
        raise ValueError(f"body_T_cam0/body_T_cam1 missing or invalid in {path}")
    return tuple(np.asarray(transform, dtype=float) for transform in transforms)


def transform_from_parameters(parameters: np.ndarray) -> np.ndarray:
    transform = np.eye(4)
    transform[:3, :3] = Rotation.from_rotvec(parameters[:3]).as_matrix()
    transform[:3, 3] = parameters[3:6]
    return transform


def parameters_from_transform(transform: np.ndarray) -> np.ndarray:
    return np.r_[
        Rotation.from_matrix(transform[:3, :3]).as_rotvec(), transform[:3, 3]
    ]


def mean_transform(transforms: np.ndarray) -> np.ndarray:
    result = np.eye(4)
    result[:3, :3] = Rotation.from_matrix(transforms[:, :3, :3]).mean().as_matrix()
    result[:3, 3] = np.median(transforms[:, :3, 3], axis=0)
    return result


def project_points(
    tracker_poses: np.ndarray,
    tracker_T_body: np.ndarray,
    world_T_grid: np.ndarray,
    model: CameraModel,
    observations: CameraObservations,
    grid_scale: float = 1.0,
) -> np.ndarray:
    world_T_cameras = tracker_poses @ tracker_T_body @ model.body_T_camera
    world_R_camera = world_T_cameras[:, :3, :3]
    world_t_camera = world_T_cameras[:, :3, 3]
    camera_R_world = np.swapaxes(world_R_camera, 1, 2)
    camera_R_grid = camera_R_world @ world_T_grid[:3, :3]
    camera_t_grid = np.einsum(
        "nij,nj->ni",
        camera_R_world,
        world_T_grid[:3, 3] - world_t_camera,
    )

    frame_indices = observations.frame_indices
    points_camera = np.einsum(
        "nij,nj->ni",
        camera_R_grid[frame_indices],
        observations.object_points * grid_scale,
    ) + camera_t_grid[frame_indices]
    z = points_camera[:, 2]
    safe_z = np.where(z > 1.0e-5, z, 1.0e-5)
    x = points_camera[:, 0] / safe_z
    y = points_camera[:, 1] / safe_z

    distortion = np.pad(model.distortion.ravel(), (0, 4), mode="constant")[:4]
    k1, k2, p1, p2 = distortion
    radius2 = x * x + y * y
    radial = 1.0 + k1 * radius2 + k2 * radius2 * radius2
    distorted_x = x * radial + 2.0 * p1 * x * y + p2 * (radius2 + 2.0 * x * x)
    distorted_y = y * radial + p1 * (radius2 + 2.0 * y * y) + 2.0 * p2 * x * y
    projected = np.column_stack(
        (
            model.intrinsic[0, 0] * distorted_x + model.intrinsic[0, 2],
            model.intrinsic[1, 1] * distorted_y + model.intrinsic[1, 2],
        )
    )
    if np.any(z <= 1.0e-5):
        projected[z <= 1.0e-5] += 1.0e4
    return projected


def bundle_residual(
    parameters: np.ndarray,
    captures: list[BundleCapture],
    models: tuple[CameraModel, CameraModel],
    fixed_tracker_T_body: np.ndarray | None = None,
    fixed_grid_scale: float | None = None,
) -> np.ndarray:
    if fixed_tracker_T_body is None:
        tracker_T_body = transform_from_parameters(parameters[:6])
        grid_scale = float(np.exp(parameters[6]))
        grid_offset = 7
    else:
        tracker_T_body = fixed_tracker_T_body
        if fixed_grid_scale is None:
            raise ValueError("fixed_grid_scale is required with a fixed external")
        grid_scale = fixed_grid_scale
        grid_offset = 0
    residuals = []
    for capture_index, capture in enumerate(captures):
        start = grid_offset + capture_index * 6
        world_T_grid = transform_from_parameters(parameters[start : start + 6])
        for model, observations in zip(models, capture.cameras):
            projected = project_points(
                capture.tracker_poses,
                tracker_T_body,
                world_T_grid,
                model,
                observations,
                grid_scale,
            )
            residuals.append((projected - observations.image_points).ravel())
    return np.concatenate(residuals)


def residual_profile(residual: np.ndarray) -> dict[str, object]:
    point_errors = np.linalg.norm(residual.reshape(-1, 2), axis=1)
    return {
        "points": int(len(point_errors)),
        "reprojection_error_px": summary(point_errors),
    }


def fit_bundle(
    captures: list[BundleCapture],
    models: tuple[CameraModel, CameraModel],
    initial_tracker_T_body: np.ndarray,
) -> tuple[np.ndarray, float, list[np.ndarray], dict[str, object]]:
    initial = [parameters_from_transform(initial_tracker_T_body), np.asarray([0.0])]
    initial.extend(parameters_from_transform(item.initial_world_T_grid) for item in captures)
    fitted = least_squares(
        bundle_residual,
        np.concatenate(initial),
        args=(captures, models),
        loss="huber",
        f_scale=1.0,
        x_scale="jac",
        max_nfev=200,
    )
    tracker_T_body = transform_from_parameters(fitted.x[:6])
    grid_scale = float(np.exp(fitted.x[6]))
    world_T_grids = [
        transform_from_parameters(fitted.x[7 + index * 6 : 13 + index * 6])
        for index in range(len(captures))
    ]
    residual = bundle_residual(fitted.x, captures, models)
    return tracker_T_body, grid_scale, world_T_grids, {
        "solver_success": bool(fitted.success),
        "solver_status": int(fitted.status),
        "solver_message": str(fitted.message),
        "cost": float(fitted.cost),
        "optimality": float(fitted.optimality),
        **residual_profile(residual),
    }


def fit_grids_with_fixed_external(
    captures: list[BundleCapture],
    models: tuple[CameraModel, CameraModel],
    tracker_T_body: np.ndarray,
    grid_scale: float,
) -> dict[str, object]:
    initial = np.concatenate(
        [parameters_from_transform(item.initial_world_T_grid) for item in captures]
    )
    fitted = least_squares(
        bundle_residual,
        initial,
        args=(captures, models, tracker_T_body, grid_scale),
        loss="huber",
        f_scale=1.0,
        x_scale="jac",
        max_nfev=120,
    )
    residual = bundle_residual(
        fitted.x, captures, models, tracker_T_body, grid_scale
    )
    return {
        "solver_success": bool(fitted.success),
        **residual_profile(residual),
    }


def transform_difference(first: np.ndarray, second: np.ndarray) -> dict[str, float]:
    return {
        "translation_mm": float(np.linalg.norm(first[:3, 3] - second[:3, 3]) * 1000.0),
        "rotation_deg": float(
            np.degrees(
                Rotation.from_matrix(first[:3, :3] @ second[:3, :3].T).magnitude()
            )
        ),
    }


def initial_external(calibration_path: Path, body_T_cam0: np.ndarray) -> tuple[np.ndarray, dict]:
    calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
    if "tracker_T_body" in calibration:
        transform = np.asarray(calibration["tracker_T_body"], dtype=float)
    elif "tracker_T_d405_left_camera" in calibration:
        transform = (
            np.asarray(calibration["tracker_T_d405_left_camera"], dtype=float)
            @ np.linalg.inv(body_T_cam0)
        )
    else:
        raise ValueError(f"initial calibration has no supported transform: {calibration_path}")
    if transform.shape != (4, 4):
        raise ValueError("initial Tracker external must be 4x4")
    return transform, calibration


def select_evenly(items: list, maximum: int) -> list:
    if len(items) <= maximum:
        return items
    indices = np.linspace(0, len(items) - 1, maximum, dtype=int)
    return [items[index] for index in indices]


def motion_speeds(detected: list[tuple]) -> tuple[np.ndarray, np.ndarray]:
    times = np.asarray([item[0] for item in detected])
    grid_T_cam0 = np.linalg.inv(np.asarray([item[5] for item in detected]))
    linear = np.zeros(len(detected))
    angular = np.zeros(len(detected))
    for index in range(len(detected)):
        first = max(0, index - 1)
        last = min(len(detected) - 1, index + 1)
        if first == last:
            continue
        elapsed = times[last] - times[first]
        linear[index] = (
            np.linalg.norm(grid_T_cam0[last, :3, 3] - grid_T_cam0[first, :3, 3])
            / elapsed
        )
        angular[index] = np.degrees(
            Rotation.from_matrix(
                grid_T_cam0[first, :3, :3].T @ grid_T_cam0[last, :3, :3]
            ).magnitude()
            / elapsed
        )
    return linear, angular


def load_capture(
    session: Path,
    tracker_path: Path,
    d405_frames_path: Path,
    detector,
    grid: dict,
    models: tuple[CameraModel, CameraModel],
    cam1_T_cam0: np.ndarray,
    initial_tracker_T_body: np.ndarray,
    tracker_query_offset_ms: float,
    tracker_time_source: str,
    max_gap_s: float,
    min_tags: int,
    max_frame_reprojection_rmse_px: float,
    max_linear_speed_mps: float,
    max_angular_speed_deg_s: float,
    discard_leading_s: float,
    frame_stride: int,
    max_frames: int,
) -> tuple[BundleCapture, str, dict[str, object]]:
    detected = []
    input_frames = 0
    db3 = select_db3(session)
    for frame_index, sample in enumerate(rsusb_stereo_image_iter(db3)):
        input_frames += 1
        if frame_index % frame_stride:
            continue
        timestamp, left_image, right_image = sample
        left_object, left_points, left_tags = detected_points(detector, left_image, grid)
        right_object, right_points, right_tags = detected_points(detector, right_image, grid)
        if left_tags < min_tags or right_tags < min_tags:
            continue
        try:
            rvec, tvec, rmse = stereo_pose(
                left_object,
                left_points,
                right_object,
                right_points,
                models[0].intrinsic,
                models[0].distortion,
                models[1].intrinsic,
                models[1].distortion,
                cam1_T_cam0,
            )
        except ValueError:
            continue
        if rmse > max_frame_reprojection_rmse_px:
            continue
        cam0_T_grid = np.eye(4)
        cam0_T_grid[:3, :3] = Rotation.from_rotvec(rvec).as_matrix()
        cam0_T_grid[:3, 3] = tvec
        detected.append(
            (
                float(timestamp),
                np.asarray(left_object, dtype=float),
                np.asarray(left_points, dtype=float),
                np.asarray(right_object, dtype=float),
                np.asarray(right_points, dtype=float),
                cam0_T_grid,
                float(rmse),
            )
        )
    first_detected_time = detected[0][0] if detected else 0.0
    detected = [
        item for item in detected if item[0] >= first_detected_time + discard_leading_s
    ]
    if len(detected) < 40:
        raise ValueError(
            f"too few frames after leading-time exclusion in {session}: {len(detected)}"
        )
    detected_before_speed_gate = len(detected)
    linear_speed, angular_speed = motion_speeds(detected)
    speed_valid = (linear_speed <= max_linear_speed_mps) & (
        angular_speed <= max_angular_speed_deg_s
    )
    detected = [item for item, keep in zip(detected, speed_valid) if keep]
    selected_linear_speed = linear_speed[speed_valid]
    selected_angular_speed = angular_speed[speed_valid]
    detected = select_evenly(detected, max_frames)
    if len(detected) < 40:
        raise ValueError(f"too few valid stereo AprilGrid frames in {session}: {len(detected)}")

    camera_times = np.asarray([item[0] for item in detected])
    aligned_times, clock_mapping = map_camera_times(
        camera_times, tracker_time_source, d405_frames_path
    )
    tracker_times, tracker_positions, tracker_quaternions, serial = load_tracker(
        tracker_path, tracker_time_source
    )
    tracker_poses, valid = interpolate_tracker(
        aligned_times + tracker_query_offset_ms / 1000.0,
        tracker_times,
        tracker_positions,
        tracker_quaternions,
        max_gap_s,
    )
    detected = [item for item, keep in zip(detected, valid) if keep]
    if len(detected) < 40:
        raise ValueError(f"too few timestamp-overlapped frames in {session}: {len(detected)}")

    def flatten(object_index: int, image_index: int) -> CameraObservations:
        counts = [len(item[object_index]) for item in detected]
        return CameraObservations(
            object_points=np.vstack([item[object_index] for item in detected]),
            image_points=np.vstack([item[image_index] for item in detected]),
            frame_indices=np.repeat(np.arange(len(detected)), counts),
        )

    initial_world_T_grids = np.asarray(
        [
            tracker_pose
            @ initial_tracker_T_body
            @ models[0].body_T_camera
            @ item[5]
            for tracker_pose, item in zip(tracker_poses, detected)
        ]
    )
    capture = BundleCapture(
        name=session.name,
        tracker_poses=tracker_poses,
        cameras=(flatten(1, 2), flatten(3, 4)),
        initial_world_T_grid=mean_transform(initial_world_T_grids),
        frame_reprojection_rmse_px=np.asarray([item[6] for item in detected]),
    )
    diagnostics = {
        "session": str(session.resolve()),
        "tracker": str(tracker_path.resolve()),
        "d405_frames": str(d405_frames_path.resolve()),
        "db3": str(db3.resolve()),
        "input_stereo_frames": input_frames,
        "frames_before_speed_gate": detected_before_speed_gate,
        "discarded_leading_s": discard_leading_s,
        "selected_frames": len(detected),
        "left_corners": int(len(capture.cameras[0].object_points)),
        "right_corners": int(len(capture.cameras[1].object_points)),
        "pnp_frame_rmse_px": summary(capture.frame_reprojection_rmse_px),
        "speed_gate": {
            "maximum_linear_speed_mps": max_linear_speed_mps,
            "maximum_angular_speed_deg_s": max_angular_speed_deg_s,
            "selected_linear_speed_mps": summary(selected_linear_speed),
            "selected_angular_speed_deg_s": summary(selected_angular_speed),
        },
        "clock_mapping": clock_mapping,
    }
    return capture, serial, diagnostics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--capture",
        nargs=3,
        action="append",
        metavar=("SESSION", "TRACKER_CSV", "D405_FRAMES_CSV"),
        required=True,
        help="可重复；至少两轮独立固定板多轴采集",
    )
    parser.add_argument("--initial-calibration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--aprilgrid",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "config/aprilgrid_6x6_35mm.yaml",
    )
    parser.add_argument("--left-camera-yaml", type=Path, default=FORMAL_CALIBRATION_ROOT / "left.yaml")
    parser.add_argument("--right-camera-yaml", type=Path, default=FORMAL_CALIBRATION_ROOT / "right.yaml")
    parser.add_argument("--body-camera-config", type=Path, default=FORMAL_CALIBRATION_ROOT / "vins_config.yaml")
    parser.add_argument("--tracker-query-offset-ms", type=float)
    parser.add_argument("--tracker-time-source", choices=("host_monotonic", "host_realtime"), default="host_monotonic")
    parser.add_argument("--max-gap-ms", type=float, default=30.0)
    parser.add_argument("--min-tags", type=int, default=4)
    parser.add_argument("--max-frame-reprojection-rmse-px", type=float, default=1.5)
    parser.add_argument("--max-linear-speed-mps", type=float, default=10.0)
    parser.add_argument("--max-angular-speed-deg-s", type=float, default=1000.0)
    parser.add_argument("--discard-leading-s", type=float, default=0.0)
    parser.add_argument("--frame-stride", type=int, default=5)
    parser.add_argument("--max-frames-per-capture", type=int, default=180)
    args = parser.parse_args()

    if len(args.capture) < 2:
        raise ValueError("at least two independent captures are required")
    if args.frame_stride < 1 or args.max_frames_per_capture < 40:
        raise ValueError("frame stride/max frames are invalid")

    body_T_cam0, body_T_cam1 = load_body_T_cameras(args.body_camera_config)
    left_intrinsic, left_distortion = load_camera_yaml(args.left_camera_yaml)
    right_intrinsic, right_distortion = load_camera_yaml(args.right_camera_yaml)
    models = (
        CameraModel(left_intrinsic, left_distortion, body_T_cam0),
        CameraModel(right_intrinsic, right_distortion, body_T_cam1),
    )
    initial_tracker_T_body, initial_calibration = initial_external(
        args.initial_calibration, body_T_cam0
    )
    tracker_query_offset_ms = (
        float(args.tracker_query_offset_ms)
        if args.tracker_query_offset_ms is not None
        else float(initial_calibration["tracker_query_offset_ms"])
    )
    cam1_T_cam0 = np.linalg.inv(body_T_cam1) @ body_T_cam0

    from aprilgrid import Detector

    detector = Detector("t36h11")
    grid = load_aprilgrid_config(args.aprilgrid)
    captures = []
    serials = []
    capture_diagnostics = []
    for session, tracker, frames in args.capture:
        capture, serial, diagnostics = load_capture(
            Path(session).resolve(),
            Path(tracker).resolve(),
            Path(frames).resolve(),
            detector,
            grid,
            models,
            cam1_T_cam0,
            initial_tracker_T_body,
            tracker_query_offset_ms,
            args.tracker_time_source,
            args.max_gap_ms / 1000.0,
            args.min_tags,
            args.max_frame_reprojection_rmse_px,
            args.max_linear_speed_mps,
            args.max_angular_speed_deg_s,
            args.discard_leading_s,
            args.frame_stride,
            args.max_frames_per_capture,
        )
        captures.append(capture)
        serials.append(serial)
        capture_diagnostics.append(diagnostics)
    if len(set(serials)) != 1:
        raise ValueError(f"captures use different trackers: {sorted(set(serials))}")

    joint_external, joint_grid_scale, _, joint_profile = fit_bundle(
        captures, models, initial_tracker_T_body
    )
    independent = []
    independent_externals = []
    for capture in captures:
        external, grid_scale, _, profile = fit_bundle(
            [capture], models, initial_tracker_T_body
        )
        independent_externals.append(external)
        independent.append(
            {
                "capture": capture.name,
                "tracker_T_body": external.tolist(),
                "grid_scale": grid_scale,
                "profile": profile,
            }
        )
    pairwise = []
    for first in range(len(captures)):
        for second in range(first + 1, len(captures)):
            pairwise.append(
                {
                    "first": captures[first].name,
                    "second": captures[second].name,
                    **transform_difference(
                        independent_externals[first], independent_externals[second]
                    ),
                }
            )
    held_out = []
    for index, capture in enumerate(captures):
        training = [item for position, item in enumerate(captures) if position != index]
        training_external, training_grid_scale, _, _ = fit_bundle(
            training, models, initial_tracker_T_body
        )
        held_out.append(
            {
                "capture": capture.name,
                "training_grid_scale": training_grid_scale,
                **fit_grids_with_fixed_external(
                    [capture], models, training_external, training_grid_scale
                ),
            }
        )

    maximum_repeat_translation_mm = max(item["translation_mm"] for item in pairwise)
    maximum_repeat_rotation_deg = max(item["rotation_deg"] for item in pairwise)
    maximum_held_out_p95_px = max(
        item["reprojection_error_px"]["p95"] for item in held_out
    )
    passed = (
        joint_profile["solver_success"]
        and maximum_repeat_translation_mm <= 2.0
        and maximum_repeat_rotation_deg <= 0.3
        and maximum_held_out_p95_px <= 3.0
        and 0.95 <= joint_grid_scale <= 1.05
    )
    tracker_T_cam0 = joint_external @ body_T_cam0
    report = {
        "schema": "lighthouse_d405_body_corner_bundle_v1",
        "result": "PASS_CANDIDATE" if passed else "DIAGNOSTIC_CANDIDATE",
        "auto_apply": False,
        "method": "joint raw dual-IR AprilGrid corner bundle adjustment directly to Tracker-to-IMU/body",
        "independent_ground_truth": True,
        "slam_supervision": False,
        "slam_inputs": [],
        "tracker_serial": serials[0],
        "tracker_time_source": args.tracker_time_source,
        "tracker_query_offset_ms": tracker_query_offset_ms,
        "time_offset_policy": "fixed common offset from independent multi-capture hand-eye solve; not coupled into this external solve",
        "tracker_T_body": joint_external.tolist(),
        "tracker_T_d405_left_camera": tracker_T_cam0.tolist(),
        "translation_norm_mm": float(np.linalg.norm(joint_external[:3, 3]) * 1000.0),
        "rotation_angle_deg": float(np.degrees(Rotation.from_matrix(joint_external[:3, :3]).magnitude())),
        "aprilgrid_scale": joint_grid_scale,
        "estimated_tag_size_mm": float(grid["tagSize"] * joint_grid_scale * 1000.0),
        "initial_external_difference": transform_difference(joint_external, initial_tracker_T_body),
        "joint_profile": joint_profile,
        "independent_profiles": independent,
        "pairwise_independent_external_differences": pairwise,
        "held_out_profiles": held_out,
        "acceptance": {
            "maximum_repeat_translation_mm": maximum_repeat_translation_mm,
            "maximum_repeat_rotation_deg": maximum_repeat_rotation_deg,
            "maximum_held_out_reprojection_p95_px": maximum_held_out_p95_px,
            "thresholds": {
                "maximum_repeat_translation_mm": 2.0,
                "maximum_repeat_rotation_deg": 0.3,
                "maximum_held_out_reprojection_p95_px": 3.0,
                "aprilgrid_scale_range": [0.95, 1.05],
            },
        },
        "captures": capture_diagnostics,
        "inputs": {
            "initial_calibration": str(args.initial_calibration.resolve()),
            "aprilgrid": str(args.aprilgrid.resolve()),
            "left_camera": str(args.left_camera_yaml.resolve()),
            "right_camera": str(args.right_camera_yaml.resolve()),
            "body_camera_config": str(args.body_camera_config.resolve()),
        },
        "acceptance_note": "Never replace the frozen evaluation external unless this is PASS_CANDIDATE and a separate trajectory recording also improves.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
