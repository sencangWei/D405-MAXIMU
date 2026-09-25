#!/usr/bin/env python3
"""Recover MASt3R-SLAM metric scale from the recorded D405 stereo IR pair.

Only UMI-native measurements are consumed: MASt3R camera poses, synchronized
left/right IR images, and the RealSense factory calibration stored in the DB3.
Docker2, robot/TCP, and Lighthouse trajectories are never read.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial.transform import Rotation


LEFT_IMAGE_TOPIC = "/device_0/sensor_0/Infrared_1/image/data"
LEFT_METADATA_TOPIC = "/device_0/sensor_0/Infrared_1/image/metadata"
RIGHT_IMAGE_TOPIC = "/device_0/sensor_0/Infrared_2/image/data"
RIGHT_METADATA_TOPIC = "/device_0/sensor_0/Infrared_2/image/metadata"
LEFT_INFO_TOPIC = "/device_0/sensor_0/Infrared_1/camera_info"
RIGHT_INFO_TOPIC = "/device_0/sensor_0/Infrared_2/camera_info"
RIGHT_TF_TOPIC = "/device_0/sensor_0/Infrared_2/tf/ref_0"
COLOR_TF_TOPIC = "/device_0/sensor_0/Color_0/tf/ref_0"
BASELINE_TOPIC = "/device_0/sensor_0/option/Stereo_Baseline/value"
FRAME_NUMBER_RE = re.compile(r"(?:^|;)Frame number=(\d+)")


def select_db3(session: Path) -> Path:
    bags = [path for path in session.glob("*.db3") if path.stat().st_size]
    if not bags:
        raise FileNotFoundError(f"no non-empty db3 in {session}")
    return max(bags, key=lambda path: path.stat().st_size)


def parse_camera_info(text: str) -> dict:
    fields = dict(item.split("=", 1) for item in text.split(";") if "=" in item)
    required = {"width", "height", "fx", "fy", "ppx", "ppy", "model", "coeffs"}
    if not required.issubset(fields):
        raise ValueError("incomplete RealSense camera_info")
    return {
        "width": int(fields["width"]),
        "height": int(fields["height"]),
        "fx": float(fields["fx"]),
        "fy": float(fields["fy"]),
        "cx": float(fields["ppx"]),
        "cy": float(fields["ppy"]),
        "model": fields["model"],
        "coeffs": [float(value) for value in fields["coeffs"].split(",")],
    }


def parse_transform(text: str) -> tuple[np.ndarray, np.ndarray]:
    fields = dict(item.split("=", 1) for item in text.split(";") if "=" in item)
    rotation = np.asarray([float(value) for value in fields["rotation"].split(",")])
    translation = np.asarray(
        [float(value) for value in fields["translation"].split(",")]
    )
    if rotation.size != 9 or translation.size != 3:
        raise ValueError("invalid RealSense transform")
    return rotation.reshape(3, 3), translation


def set_topic_filter(reader, topics) -> None:
    import rosbag2_py

    reader.set_filter(rosbag2_py.StorageFilter(topics=sorted(topics)))


def read_static_strings(db3: Path, topics: set[str]) -> dict[str, str]:
    import rosbag2_py
    from rclpy.serialization import deserialize_message
    from std_msgs.msg import String

    reader = rosbag2_py.SequentialReader()
    reader.open(
        rosbag2_py.StorageOptions(uri=str(db3), storage_id="sqlite3"),
        rosbag2_py.ConverterOptions("", ""),
    )
    set_topic_filter(reader, topics)
    values = {}
    while reader.has_next() and values.keys() != topics:
        topic, data, _ = reader.read_next()
        if topic in topics and topic not in values:
            values[topic] = deserialize_message(data, String).data
    missing = topics - values.keys()
    if missing:
        raise ValueError("missing factory calibration topics: " + ", ".join(sorted(missing)))
    return values


def load_stereo_calibration(db3: Path) -> dict:
    topics = {
        LEFT_INFO_TOPIC,
        RIGHT_INFO_TOPIC,
        RIGHT_TF_TOPIC,
        COLOR_TF_TOPIC,
        BASELINE_TOPIC,
    }
    values = read_static_strings(db3, topics)
    left = parse_camera_info(values[LEFT_INFO_TOPIC])
    right = parse_camera_info(values[RIGHT_INFO_TOPIC])
    rotation, translation = parse_transform(values[RIGHT_TF_TOPIC])
    color_rotation, color_translation = parse_transform(values[COLOR_TF_TOPIC])
    baseline_m = float(values[BASELINE_TOPIC]) / 1000.0
    transform_baseline_m = float(np.linalg.norm(translation))
    failures = []
    if left["width"] != right["width"] or left["height"] != right["height"]:
        failures.append("stereo_resolution_mismatch")
    if max(abs(left[key] - right[key]) for key in ("fx", "fy", "cx", "cy")) > 0.5:
        failures.append("stereo_intrinsics_mismatch")
    if np.degrees(Rotation.from_matrix(rotation).magnitude()) > 0.1:
        failures.append("stereo_not_rectified")
    if abs(transform_baseline_m - baseline_m) > 0.0001:
        failures.append("baseline_sources_disagree")
    if not 0.005 <= baseline_m <= 0.1:
        failures.append("baseline_out_of_range")
    if not np.allclose(color_rotation.T @ color_rotation, np.eye(3), atol=1e-4):
        failures.append("color_extrinsic_rotation_not_orthonormal")
    if not np.isclose(np.linalg.det(color_rotation), 1.0, atol=1e-4):
        failures.append("color_extrinsic_rotation_not_proper")
    if failures:
        raise ValueError("invalid factory stereo calibration: " + ", ".join(failures))
    return {
        "left": left,
        "right": right,
        "right_rotation_from_left": rotation,
        "right_translation_from_left_m": translation,
        "color_rotation_from_left": color_rotation,
        "color_translation_from_left_m": color_translation,
        "baseline_m": baseline_m,
        "factory_topics": sorted(topics),
    }


def load_stereo_calibration_from_prepared_dataset(dataset: Path) -> dict:
    manifest_path = dataset / "dataset_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("stream") != "infrared_left":
        raise ValueError("prepared stereo dataset must use infrared_left")
    stereo = manifest.get("stereo_depth_source") or {}
    left = manifest.get("source_camera_info") or manifest.get("camera_info")
    right = stereo.get("right_camera_info")
    baseline_m = float(stereo.get("baseline_m", 0.0))
    if left is None or right is None or not 0.005 <= baseline_m <= 0.1:
        raise ValueError("prepared dataset lacks valid stereo calibration")
    return {
        "left": {
            "width": int(left["width"]),
            "height": int(left["height"]),
            "fx": float(left["fx"]),
            "fy": float(left["fy"]),
            "cx": float(left["ppx"]),
            "cy": float(left["ppy"]),
            "model": left["model"],
            "coeffs": [float(value) for value in left["coeffs"]],
        },
        "right": {
            "width": int(right["width"]),
            "height": int(right["height"]),
            "fx": float(right["fx"]),
            "fy": float(right["fy"]),
            "cx": float(right["ppx"]),
            "cy": float(right["ppy"]),
            "model": right["model"],
            "coeffs": [float(value) for value in right["coeffs"]],
        },
        "right_rotation_from_left": np.eye(3),
        "right_translation_from_left_m": np.asarray([-baseline_m, 0.0, 0.0]),
        "color_rotation_from_left": np.eye(3),
        "color_translation_from_left_m": np.zeros(3),
        "baseline_m": baseline_m,
        "factory_topics": [str(manifest_path.resolve())],
    }


def change_relative_pose_frame(
    source_rotation: Rotation,
    source_translation: np.ndarray,
    target_rotation_from_source: Rotation,
    target_translation_from_source: np.ndarray,
) -> tuple[Rotation, np.ndarray]:
    """Conjugate a relative camera transform into another rigid camera frame."""
    source_matrix = source_rotation.as_matrix()
    target_from_source_matrix = target_rotation_from_source.as_matrix()
    target_rotation_matrix = (
        target_from_source_matrix
        @ source_matrix
        @ target_from_source_matrix.T
    )
    target_translation = (
        target_translation_from_source
        + target_from_source_matrix @ source_translation
        - target_rotation_matrix @ target_translation_from_source
    )
    return Rotation.from_matrix(target_rotation_matrix), target_translation


def solve_translation_with_fixed_rotation(
    object_points: np.ndarray,
    image_points: np.ndarray,
    camera_matrix: np.ndarray,
    rotation: Rotation,
) -> tuple[np.ndarray, np.ndarray]:
    """Estimate only translation after rotation is fixed by the pose trajectory."""
    rotated = rotation.apply(np.asarray(object_points, dtype=float))
    image_points = np.asarray(image_points, dtype=float)
    fx = float(camera_matrix[0, 0])
    fy = float(camera_matrix[1, 1])
    cx = float(camera_matrix[0, 2])
    cy = float(camera_matrix[1, 2])
    normalized_x = (image_points[:, 0] - cx) / fx
    normalized_y = (image_points[:, 1] - cy) / fy

    design = np.zeros((2 * len(rotated), 3), dtype=float)
    target = np.zeros(2 * len(rotated), dtype=float)
    design[0::2, 0] = 1.0
    design[0::2, 2] = -normalized_x
    design[1::2, 1] = 1.0
    design[1::2, 2] = -normalized_y
    target[0::2] = normalized_x * rotated[:, 2] - rotated[:, 0]
    target[1::2] = normalized_y * rotated[:, 2] - rotated[:, 1]

    point_weights = np.ones(len(rotated), dtype=float)
    translation = np.zeros(3, dtype=float)
    reprojection_error = np.full(len(rotated), np.inf, dtype=float)
    for _ in range(8):
        row_weights = np.repeat(np.sqrt(point_weights), 2)
        translation = np.linalg.lstsq(
            design * row_weights[:, None], target * row_weights, rcond=None
        )[0]
        camera_points = rotated + translation
        valid_depth = camera_points[:, 2] > 1e-6
        projected = np.full_like(image_points, np.inf)
        projected[valid_depth, 0] = (
            fx * camera_points[valid_depth, 0] / camera_points[valid_depth, 2] + cx
        )
        projected[valid_depth, 1] = (
            fy * camera_points[valid_depth, 1] / camera_points[valid_depth, 2] + cy
        )
        reprojection_error = np.linalg.norm(projected - image_points, axis=1)
        finite = np.isfinite(reprojection_error)
        if not np.any(finite):
            break
        robust_scale = max(float(np.median(reprojection_error[finite])), 0.5)
        cutoff = 1.5 * robust_scale
        point_weights = np.zeros(len(rotated), dtype=float)
        point_weights[finite] = np.minimum(
            1.0, cutoff / np.maximum(reprojection_error[finite], 1e-9)
        )
    return translation, reprojection_error


def load_trajectory(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[dict]]:
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    required = {"t_sec", "x", "y", "z", "qw", "qx", "qy", "qz"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"invalid trajectory: {path}")
    times = np.asarray([float(row["t_sec"]) for row in rows])
    positions = np.asarray(
        [[float(row[key]) for key in ("x", "y", "z")] for row in rows]
    )
    quaternions = np.asarray(
        [[float(row[key]) for key in ("qx", "qy", "qz", "qw")] for row in rows]
    )
    if np.any(np.diff(times) <= 0):
        raise ValueError("trajectory timestamps must be strictly increasing")
    return times, positions, quaternions, rows


def match_trajectory_to_stereo_frames(
    frame_csv: Path,
    trajectory_times: np.ndarray,
    max_delta_s: float = 0.002,
    trajectory_frame: str = "color",
) -> tuple[np.ndarray, np.ndarray, dict]:
    rows = list(csv.DictReader(frame_csv.open(newline="", encoding="utf-8")))
    required = {
        "color_device_ms",
        "infrared_left_device_ms",
        "infrared_right_device_ms",
        "infrared_left_frame_number",
        "infrared_right_frame_number",
    }
    if not rows or not required.issubset(rows[0]):
        raise ValueError("d405_frames.csv lacks synchronized RGB/stereo columns")
    source_column = {
        "color": "color_device_ms",
        "infrared_left": "infrared_left_device_ms",
    }[trajectory_frame]
    source_times = np.asarray(
        [float(row[source_column]) / 1000.0 for row in rows]
    )
    indices = np.searchsorted(source_times, trajectory_times)
    indices = np.clip(indices, 1, len(source_times) - 1)
    choose_left = np.abs(trajectory_times - source_times[indices - 1]) <= np.abs(
        trajectory_times - source_times[indices]
    )
    indices = indices - choose_left.astype(int)
    deltas = np.abs(trajectory_times - source_times[indices])
    if float(np.max(deltas)) > max_delta_s:
        raise ValueError(
            f"MASt3R timestamps do not match D405 {trajectory_frame} frames: "
            f"max={np.max(deltas)*1000:.3f}ms"
        )
    left_numbers = np.asarray(
        [int(rows[index]["infrared_left_frame_number"]) for index in indices]
    )
    right_numbers = np.asarray(
        [int(rows[index]["infrared_right_frame_number"]) for index in indices]
    )
    stereo_skew_ms = np.asarray(
        [
            abs(
                float(rows[index]["infrared_left_device_ms"])
                - float(rows[index]["infrared_right_device_ms"])
            )
            for index in indices
        ]
    )
    rgb_ir_skew_ms = np.asarray(
        [
            abs(
                float(rows[index]["color_device_ms"])
                - float(rows[index]["infrared_left_device_ms"])
            )
            for index in indices
        ]
    )
    if float(np.max(stereo_skew_ms)) > 0.1:
        raise ValueError("left/right IR are not hardware synchronized")
    return left_numbers, right_numbers, {
        "trajectory_frame": trajectory_frame,
        "max_trajectory_to_source_delta_ms": float(np.max(deltas) * 1000.0),
        "max_left_right_ir_skew_ms": float(np.max(stereo_skew_ms)),
        "max_rgb_left_ir_skew_ms": float(np.max(rgb_ir_skew_ms)),
    }


def sample_pairs(
    sample_indices: np.ndarray,
    max_hop: int,
    hop_values: tuple[int, ...] | None = None,
) -> list[tuple[int, int, int]]:
    if max_hop < 1:
        raise ValueError("maximum stereo pair hop must be positive")
    if hop_values is None:
        hops = range(1, min(max_hop, len(sample_indices) - 1) + 1)
    else:
        if not hop_values or any(hop < 1 for hop in hop_values):
            raise ValueError("stereo pair hops must be positive")
        hops = sorted(set(hop_values))
    return [
        (int(sample_indices[first]), int(sample_indices[first + hop]), hop)
        for hop in hops
        if hop < len(sample_indices)
        for first in range(len(sample_indices) - hop)
    ]


def decode_mono(message) -> np.ndarray:
    height, width, step = int(message.height), int(message.width), int(message.step)
    data = np.frombuffer(message.data, dtype=np.uint8)
    if step != width or data.size != height * step:
        raise ValueError(f"unexpected mono image layout: {width}x{height}, step={step}")
    return data.reshape(height, width).copy()


def load_selected_stereo_images(
    db3: Path, left_numbers: set[int], right_numbers: set[int]
) -> tuple[dict[int, np.ndarray], dict[int, np.ndarray]]:
    import rosbag2_py
    from rclpy.serialization import deserialize_message
    from sensor_msgs.msg import Image
    from std_msgs.msg import String

    targets = {
        "left": (LEFT_IMAGE_TOPIC, LEFT_METADATA_TOPIC, left_numbers),
        "right": (RIGHT_IMAGE_TOPIC, RIGHT_METADATA_TOPIC, right_numbers),
    }
    topic_to_stream = {
        topic: (stream, kind)
        for stream, (image_topic, metadata_topic, _) in targets.items()
        for topic, kind in ((image_topic, "image"), (metadata_topic, "metadata"))
    }
    reader = rosbag2_py.SequentialReader()
    reader.open(
        rosbag2_py.StorageOptions(uri=str(db3), storage_id="sqlite3"),
        rosbag2_py.ConverterOptions("", ""),
    )
    set_topic_filter(reader, topic_to_stream)
    pending_images = {"left": None, "right": None}
    pending_numbers = {"left": None, "right": None}
    output = {"left": {}, "right": {}}
    while reader.has_next():
        topic, data, _ = reader.read_next()
        stream, kind = topic_to_stream[topic]
        if kind == "image":
            pending_images[stream] = deserialize_message(data, Image)
        else:
            text = deserialize_message(data, String).data
            match = FRAME_NUMBER_RE.search(text)
            pending_numbers[stream] = int(match.group(1)) if match else None
        if pending_images[stream] is None or pending_numbers[stream] is None:
            continue
        frame_number = pending_numbers[stream]
        if frame_number in targets[stream][2]:
            output[stream][frame_number] = decode_mono(pending_images[stream])
        pending_images[stream] = None
        pending_numbers[stream] = None
        if (
            len(output["left"]) == len(left_numbers)
            and len(output["right"]) == len(right_numbers)
        ):
            break
    missing_left = left_numbers - output["left"].keys()
    missing_right = right_numbers - output["right"].keys()
    if missing_left or missing_right:
        raise RuntimeError(
            f"missing selected stereo images: left={sorted(missing_left)[:5]}, "
            f"right={sorted(missing_right)[:5]}"
        )
    return output["left"], output["right"]


def load_selected_prepared_stereo_images(
    dataset: Path,
    frame_csv: Path,
    left_numbers: set[int],
    right_numbers: set[int],
) -> tuple[dict[int, np.ndarray], dict[int, np.ndarray]]:
    exported = list(
        csv.DictReader((dataset / "frames.csv").open(newline="", encoding="utf-8"))
    )
    left_paths = {
        int(row["source_frame_number"]): dataset / row["image"] for row in exported
    }
    source_rows = list(csv.DictReader(frame_csv.open(newline="", encoding="utf-8")))
    right_to_left = {
        int(row["infrared_right_frame_number"]): int(
            row["infrared_left_frame_number"]
        )
        for row in source_rows
        if row.get("infrared_left_frame_number")
        and row.get("infrared_right_frame_number")
    }

    left_images = {}
    for number in left_numbers:
        path = left_paths.get(number)
        if path is None:
            continue
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is not None:
            left_images[number] = image

    right_images = {}
    for number in right_numbers:
        left_number = right_to_left.get(number)
        left_path = left_paths.get(left_number) if left_number is not None else None
        if left_path is None:
            continue
        path = dataset / "stereo_right" / left_path.name
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is not None:
            right_images[number] = image

    missing_left = left_numbers - left_images.keys()
    missing_right = right_numbers - right_images.keys()
    if missing_left or missing_right:
        raise RuntimeError(
            f"missing prepared stereo images: left={sorted(missing_left)[:5]}, "
            f"right={sorted(missing_right)[:5]}"
        )
    return left_images, right_images


def stereo_disparity(
    left: np.ndarray, right: np.ndarray, num_disparities: int
) -> tuple[np.ndarray, np.ndarray]:
    num_disparities = max(16, int(np.ceil(num_disparities / 16.0)) * 16)
    common = dict(
        numDisparities=num_disparities,
        blockSize=5,
        P1=8 * 5 * 5,
        P2=32 * 5 * 5,
        disp12MaxDiff=1,
        uniquenessRatio=8,
        speckleWindowSize=80,
        speckleRange=2,
        preFilterCap=31,
        mode=cv2.STEREO_SGBM_MODE_SGBM_3WAY,
    )
    disparity_left = cv2.StereoSGBM_create(minDisparity=0, **common).compute(
        left, right
    ).astype(np.float32) / 16.0
    disparity_right = cv2.StereoSGBM_create(
        minDisparity=-num_disparities, **common
    ).compute(right, left).astype(np.float32) / 16.0
    return disparity_left, disparity_right


def left_right_consistent(
    points: np.ndarray,
    disparity_left: np.ndarray,
    disparity_right: np.ndarray,
    tolerance_px: float,
) -> tuple[np.ndarray, np.ndarray]:
    xy = np.rint(points).astype(int)
    height, width = disparity_left.shape
    inside = (
        (xy[:, 0] >= 0)
        & (xy[:, 0] < width)
        & (xy[:, 1] >= 0)
        & (xy[:, 1] < height)
    )
    disparity = np.full(len(points), np.nan)
    disparity[inside] = disparity_left[xy[inside, 1], xy[inside, 0]]
    right_x = np.rint(points[:, 0] - disparity).astype(int)
    right_inside = inside & (right_x >= 0) & (right_x < width) & np.isfinite(disparity)
    right_value = np.full(len(points), np.nan)
    right_value[right_inside] = disparity_right[
        xy[right_inside, 1], right_x[right_inside]
    ]
    valid = right_inside & (disparity > 0.5)
    valid &= np.abs(disparity + right_value) <= tolerance_px
    return valid, disparity


def fit_rigid_transform_3d(
    source: np.ndarray,
    target: np.ndarray,
    minimum_inliers: int = 20,
) -> tuple[Rotation, np.ndarray, np.ndarray, np.ndarray]:
    """Fit a rigid 3D transform with iterative MAD outlier rejection."""
    source = np.asarray(source, dtype=float)
    target = np.asarray(target, dtype=float)
    if source.shape != target.shape or source.ndim != 2 or source.shape[1] != 3:
        raise ValueError("3D correspondence arrays must have matching Nx3 shape")
    if len(source) < minimum_inliers:
        raise ValueError("insufficient 3D correspondences")
    inliers = np.ones(len(source), dtype=bool)
    for _ in range(5):
        source_center = np.mean(source[inliers], axis=0)
        target_center = np.mean(target[inliers], axis=0)
        covariance = (source[inliers] - source_center).T @ (
            target[inliers] - target_center
        )
        u, _, vt = np.linalg.svd(covariance)
        rotation_matrix = vt.T @ u.T
        if np.linalg.det(rotation_matrix) < 0.0:
            vt[-1] *= -1.0
            rotation_matrix = vt.T @ u.T
        translation = target_center - rotation_matrix @ source_center
        residuals = np.linalg.norm(
            (rotation_matrix @ source.T).T + translation - target,
            axis=1,
        )
        center = float(np.median(residuals[inliers]))
        mad = float(np.median(np.abs(residuals[inliers] - center)))
        threshold = max(center + 3.0 * 1.4826 * mad, 0.004)
        updated = residuals <= threshold
        if np.count_nonzero(updated) < minimum_inliers:
            break
        if np.array_equal(updated, inliers):
            break
        inliers = updated
    if np.count_nonzero(inliers) < minimum_inliers:
        raise ValueError("insufficient robust 3D correspondences")
    return Rotation.from_matrix(rotation_matrix), translation, inliers, residuals


def backproject_stereo_points(
    points: np.ndarray,
    disparity: np.ndarray,
    intrinsics: dict,
    baseline_m: float,
) -> np.ndarray:
    xy = np.rint(points).astype(int)
    sampled_disparity = disparity[xy[:, 1], xy[:, 0]]
    depth = intrinsics["fx"] * baseline_m / sampled_disparity
    return np.column_stack(
        (
            (points[:, 0] - intrinsics["cx"]) * depth / intrinsics["fx"],
            (points[:, 1] - intrinsics["cy"]) * depth / intrinsics["fy"],
            depth,
        )
    )


def map_mast3r_points_to_original(
    points: np.ndarray, transform: dict
) -> np.ndarray:
    """Undo the resize and center crop used by the MASt3R image encoder."""
    mapped = np.asarray(points, dtype=np.float32).copy()
    mapped[:, 0] = (mapped[:, 0] + transform["crop_left_px"]) / transform[
        "scale_x"
    ]
    mapped[:, 1] = (mapped[:, 1] + transform["crop_top_px"]) / transform[
        "scale_y"
    ]
    return mapped


def prepare_mast3r_image(image: np.ndarray, image_norm) -> tuple[dict, dict]:
    """Prepare one grayscale IR image without writing a temporary image file."""
    from PIL import Image

    original_height, original_width = image.shape
    pil_image = Image.fromarray(image).convert("RGB")
    scale = 512.0 / max(original_width, original_height)
    resized_width = int(round(original_width * scale))
    resized_height = int(round(original_height * scale))
    pil_image = pil_image.resize(
        (resized_width, resized_height), Image.Resampling.BICUBIC
    )
    center_x, center_y = resized_width // 2, resized_height // 2
    half_width = ((2 * center_x) // 16) * 8
    half_height = ((2 * center_y) // 16) * 8
    if resized_width == resized_height:
        half_height = 3 * half_width // 4
    crop_left = center_x - half_width
    crop_top = center_y - half_height
    pil_image = pil_image.crop(
        (
            crop_left,
            crop_top,
            center_x + half_width,
            center_y + half_height,
        )
    )
    width, height = pil_image.size
    view = {
        "img": image_norm(pil_image)[None],
        "true_shape": np.int32([[height, width]]),
        "idx": 0,
        "instance": "0",
    }
    transform = {
        "scale_x": resized_width / original_width,
        "scale_y": resized_height / original_height,
        "crop_left_px": crop_left,
        "crop_top_px": crop_top,
    }
    return view, transform


def load_mast3r_correspondence_model(weights: Path | None, device: str) -> dict:
    """Load the existing MASt3R model lazily so the classical path stays light."""
    import torch
    from mast3r.model import AsymmetricMASt3R
    import mast3r.utils.path_to_dust3r  # noqa: F401
    from dust3r.utils.image import ImgNorm

    if weights is None:
        tool_dir = Path(
            os.environ.get(
                "MAST3R_SLAM_DIR",
                "/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM",
            )
        )
        weights = (
            tool_dir
            / "checkpoints"
            / "MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric.pth"
        )
    if not weights.is_file():
        raise FileNotFoundError(f"MASt3R correspondence weights not found: {weights}")
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for MASt3R long-range correspondence")
    model = AsymmetricMASt3R.from_pretrained(str(weights)).to(device).eval()
    return {"model": model, "device": device, "image_norm": ImgNorm}


def estimate_mast3r_correspondences(
    image_i: np.ndarray, image_j: np.ndarray, matcher: dict
) -> dict:
    """Find long-range learned matches and reject epipolar outliers."""
    from dust3r.inference import inference
    from mast3r.fast_nn import fast_reciprocal_NNs

    view_i, transform_i = prepare_mast3r_image(image_i, matcher["image_norm"])
    view_j, transform_j = prepare_mast3r_image(image_j, matcher["image_norm"])
    view_j["idx"] = 1
    view_j["instance"] = "1"
    output = inference(
        [(view_i, view_j)],
        matcher["model"],
        matcher["device"],
        batch_size=1,
        verbose=False,
    )
    descriptor_i = output["pred1"]["desc"].squeeze(0).detach()
    descriptor_j = output["pred2"]["desc"].squeeze(0).detach()
    points_i, points_j = fast_reciprocal_NNs(
        descriptor_i,
        descriptor_j,
        subsample_or_initxy1=8,
        device=matcher["device"],
        dist="dot",
        block_size=8192,
    )
    height_i, width_i = descriptor_i.shape[:2]
    height_j, width_j = descriptor_j.shape[:2]
    inside = (
        (points_i[:, 0] >= 3)
        & (points_i[:, 0] < width_i - 3)
        & (points_i[:, 1] >= 3)
        & (points_i[:, 1] < height_i - 3)
        & (points_j[:, 0] >= 3)
        & (points_j[:, 0] < width_j - 3)
        & (points_j[:, 1] >= 3)
        & (points_j[:, 1] < height_j - 3)
    )
    points_i = points_i[inside].astype(np.float32)
    points_j = points_j[inside].astype(np.float32)
    if len(points_i) < 30:
        return {
            "accepted": False,
            "reason": "insufficient_mast3r_matches",
            "raw_matches": int(len(points_i)),
        }
    _fundamental, mask = cv2.findFundamentalMat(
        points_i,
        points_j,
        cv2.USAC_MAGSAC,
        1.5,
        0.999,
        10_000,
    )
    if mask is None:
        return {
            "accepted": False,
            "reason": "mast3r_epipolar_fit_failed",
            "raw_matches": int(len(points_i)),
        }
    epipolar_valid = mask.ravel().astype(bool)
    epipolar_inliers = int(np.count_nonzero(epipolar_valid))
    if epipolar_inliers < 30:
        return {
            "accepted": False,
            "reason": "insufficient_mast3r_epipolar_inliers",
            "raw_matches": int(len(points_i)),
            "epipolar_inliers": epipolar_inliers,
        }
    return {
        "accepted": True,
        "points_i": map_mast3r_points_to_original(points_i, transform_i),
        "points_j": map_mast3r_points_to_original(points_j, transform_j),
        "correspondence_valid": epipolar_valid,
        "raw_matches": int(len(points_i)),
        "epipolar_inliers": epipolar_inliers,
        "epipolar_inlier_ratio": float(epipolar_inliers / len(points_i)),
    }


def estimate_motion_from_stereo_3d(
    points_i: np.ndarray,
    points_j: np.ndarray,
    correspondence_valid: np.ndarray,
    disparity_left_i: np.ndarray,
    disparity_right_i: np.ndarray,
    disparity_left_j: np.ndarray,
    disparity_right_j: np.ndarray,
    mast3r_position_i: np.ndarray,
    mast3r_position_j: np.ndarray,
    mast3r_rotation_i: Rotation,
    mast3r_rotation_j: Rotation,
    calibration: dict,
    min_depth_m: float,
    max_depth_m: float,
    trajectory_frame: str = "color",
) -> dict:
    valid_i, sampled_i = left_right_consistent(
        points_i, disparity_left_i, disparity_right_i, tolerance_px=1.0
    )
    valid_j, sampled_j = left_right_consistent(
        points_j, disparity_left_j, disparity_right_j, tolerance_px=1.0
    )
    valid = correspondence_valid & valid_i & valid_j
    left = calibration["left"]
    depth_i = left["fx"] * calibration["baseline_m"] / sampled_i
    depth_j = left["fx"] * calibration["baseline_m"] / sampled_j
    valid &= (depth_i >= min_depth_m) & (depth_i <= max_depth_m)
    valid &= (depth_j >= min_depth_m) & (depth_j <= max_depth_m)
    if int(np.count_nonzero(valid)) < 30:
        return {
            "accepted": False,
            "reason": "insufficient_bidirectional_3d_points",
            "method": "stereo_3d",
        }
    object_i = backproject_stereo_points(
        points_i[valid], disparity_left_i, left, calibration["baseline_m"]
    )
    object_j = backproject_stereo_points(
        points_j[valid], disparity_left_j, left, calibration["baseline_m"]
    )
    try:
        relative_rotation, relative_translation, inliers, residuals = (
            fit_rigid_transform_3d(object_i, object_j)
        )
    except ValueError as error:
        return {"accepted": False, "reason": str(error), "method": "stereo_3d"}
    if trajectory_frame == "color":
        relative_rotation, relative_translation = change_relative_pose_frame(
            relative_rotation,
            relative_translation,
            Rotation.from_matrix(calibration["color_rotation_from_left"]),
            calibration["color_translation_from_left_m"],
        )
    camera_displacement_i = -relative_rotation.inv().apply(relative_translation)
    metric_distance = float(np.linalg.norm(camera_displacement_i))
    mast3r_delta_world = mast3r_position_j - mast3r_position_i
    mast3r_delta_i = mast3r_rotation_i.inv().apply(mast3r_delta_world)
    mast3r_distance = float(np.linalg.norm(mast3r_delta_i))
    if metric_distance < 0.003 or mast3r_distance < 1e-4:
        return {
            "accepted": False,
            "reason": "translation_excitation_low",
            "method": "stereo_3d",
        }
    direction_cosine = float(
        np.dot(camera_displacement_i, mast3r_delta_i)
        / (metric_distance * mast3r_distance)
    )
    expected_relative_rotation = mast3r_rotation_j.inv() * mast3r_rotation_i
    rotation_error_deg = float(
        np.degrees(
            (expected_relative_rotation.inv() * relative_rotation).magnitude()
        )
    )
    if direction_cosine < 0.5:
        return {
            "accepted": False,
            "reason": "translation_direction_disagrees",
            "direction_cosine": direction_cosine,
            "method": "stereo_3d",
        }
    if rotation_error_deg > 5.0:
        return {
            "accepted": False,
            "reason": "rotation_disagrees",
            "rotation_error_deg": rotation_error_deg,
            "method": "stereo_3d",
        }
    scale = float(np.dot(camera_displacement_i, mast3r_delta_i) / mast3r_distance**2)
    if not 0.02 <= scale <= 20.0:
        return {
            "accepted": False,
            "reason": "scale_out_of_range",
            "scale": scale,
            "method": "stereo_3d",
        }
    robust_residuals = residuals[inliers]
    return {
        "accepted": True,
        "scale": scale,
        "metric_distance_m": metric_distance,
        "metric_displacement_camera_i_m": camera_displacement_i.tolist(),
        "metric_displacement_frame": (
            "color_camera_i"
            if trajectory_frame == "color"
            else "infrared_left_camera_i"
        ),
        "mast3r_distance": mast3r_distance,
        "direction_cosine": direction_cosine,
        "rotation_error_deg": rotation_error_deg,
        "tracked_points": int(len(object_i)),
        "pnp_inliers": int(np.count_nonzero(inliers)),
        "pnp_inlier_ratio": float(np.mean(inliers)),
        "median_depth_m": float(np.median(depth_i[valid])),
        "stereo_3d_residual_median_m": float(np.median(robust_residuals)),
        "stereo_3d_residual_p95_m": float(np.percentile(robust_residuals, 95)),
        "method": "stereo_3d",
    }


def estimate_motion_from_correspondences(
    points_i: np.ndarray,
    points_j: np.ndarray,
    correspondence_valid: np.ndarray,
    disparity_left: np.ndarray,
    disparity_right: np.ndarray,
    mast3r_position_i: np.ndarray,
    mast3r_position_j: np.ndarray,
    mast3r_rotation_i: Rotation,
    mast3r_rotation_j: Rotation,
    calibration: dict,
    min_depth_m: float,
    max_depth_m: float,
    method: str,
    trajectory_frame: str = "color",
    pnp_iterations: int = 200,
    pnp_reprojection_error_px: float = 2.0,
    refine_pnp: bool = False,
    pnp_rotation_mode: str = "free",
) -> dict:
    lr_valid, disparity = left_right_consistent(
        points_i, disparity_left, disparity_right, tolerance_px=1.0
    )
    valid = correspondence_valid & lr_valid
    left = calibration["left"]
    depth = left["fx"] * calibration["baseline_m"] / disparity
    valid &= (depth >= min_depth_m) & (depth <= max_depth_m)
    image_height, image_width = disparity_left.shape
    valid &= (
        (points_j[:, 0] >= 1)
        & (points_j[:, 0] < image_width - 1)
        & (points_j[:, 1] >= 1)
        & (points_j[:, 1] < image_height - 1)
    )
    if int(np.count_nonzero(valid)) < 30:
        return {
            "accepted": False,
            "reason": "insufficient_consistent_points",
            "method": method,
        }
    uv = points_i[valid]
    z = depth[valid]
    object_points = np.column_stack(
        (
            (uv[:, 0] - left["cx"]) * z / left["fx"],
            (uv[:, 1] - left["cy"]) * z / left["fy"],
            z,
        )
    ).astype(np.float32)
    image_points = points_j[valid].astype(np.float32)
    camera_matrix = np.asarray(
        [[left["fx"], 0.0, left["cx"]], [0.0, left["fy"], left["cy"]], [0.0, 0.0, 1.0]]
    )
    solved, rvec, tvec, inliers = cv2.solvePnPRansac(
        object_points,
        image_points,
        camera_matrix,
        None,
        iterationsCount=pnp_iterations,
        reprojectionError=pnp_reprojection_error_px,
        confidence=0.999,
        flags=cv2.SOLVEPNP_EPNP,
    )
    if not solved or inliers is None or len(inliers) < 20:
        return {
            "accepted": False,
            "reason": "pnp_failed",
            "method": method,
            "tracked_points": int(len(object_points)),
            "pnp_inliers": int(len(inliers)) if inliers is not None else 0,
        }
    inlier_ratio = float(len(inliers) / len(object_points))
    if inlier_ratio < 0.25:
        return {
            "accepted": False,
            "reason": "pnp_inlier_ratio_low",
            "method": method,
            "tracked_points": int(len(object_points)),
            "pnp_inliers": int(len(inliers)),
            "pnp_inlier_ratio": inlier_ratio,
        }
    inlier_indices = inliers.ravel()
    inlier_object_points = object_points[inlier_indices]
    inlier_image_points = image_points[inlier_indices]
    projected_before, _ = cv2.projectPoints(
        inlier_object_points, rvec, tvec, camera_matrix, None
    )
    reprojection_before = np.linalg.norm(
        projected_before.reshape(-1, 2) - inlier_image_points, axis=1
    )
    free_pnp_rotation = Rotation.from_rotvec(rvec.ravel())
    pnp_refined = False
    if refine_pnp:
        refined_rvec, refined_tvec = cv2.solvePnPRefineLM(
            inlier_object_points,
            inlier_image_points,
            camera_matrix,
            None,
            rvec.copy(),
            tvec.copy(),
        )
        projected_after, _ = cv2.projectPoints(
            inlier_object_points,
            refined_rvec,
            refined_tvec,
            camera_matrix,
            None,
        )
        reprojection_after = np.linalg.norm(
            projected_after.reshape(-1, 2) - inlier_image_points, axis=1
        )
        if (
            np.all(np.isfinite(refined_rvec))
            and np.all(np.isfinite(refined_tvec))
            and np.median(reprojection_after) <= np.median(reprojection_before)
        ):
            rvec, tvec = refined_rvec, refined_tvec
            reprojection_before = reprojection_after
            pnp_refined = True
    pnp_rotation_constrained = False
    free_rotation_delta_deg = 0.0
    if pnp_rotation_mode == "trajectory-fixed":
        expected_rotation = mast3r_rotation_j.inv() * mast3r_rotation_i
        if trajectory_frame == "color":
            color_from_left = Rotation.from_matrix(
                calibration["color_rotation_from_left"]
            )
            fixed_rotation = (
                color_from_left.inv() * expected_rotation * color_from_left
            )
        else:
            fixed_rotation = expected_rotation
        fixed_translation, fixed_reprojection = solve_translation_with_fixed_rotation(
            inlier_object_points,
            inlier_image_points,
            camera_matrix,
            fixed_rotation,
        )
        if np.all(np.isfinite(fixed_translation)) and np.all(
            np.isfinite(fixed_reprojection)
        ):
            free_rotation_delta_deg = float(
                np.degrees((free_pnp_rotation.inv() * fixed_rotation).magnitude())
            )
            rvec = fixed_rotation.as_rotvec().reshape(3, 1)
            tvec = fixed_translation.reshape(3, 1)
            reprojection_before = fixed_reprojection
            pnp_rotation_constrained = True
    pnp_rotation = Rotation.from_rotvec(rvec.ravel())
    pnp_translation = tvec.ravel()
    if trajectory_frame == "color":
        pnp_rotation, pnp_translation = change_relative_pose_frame(
            pnp_rotation,
            pnp_translation,
            Rotation.from_matrix(calibration["color_rotation_from_left"]),
            calibration["color_translation_from_left_m"],
        )
    camera_displacement_i = -pnp_rotation.inv().apply(pnp_translation)
    metric_distance = float(np.linalg.norm(camera_displacement_i))
    mast3r_delta_world = mast3r_position_j - mast3r_position_i
    mast3r_delta_i = mast3r_rotation_i.inv().apply(mast3r_delta_world)
    mast3r_distance = float(np.linalg.norm(mast3r_delta_i))
    if metric_distance < 0.003 or mast3r_distance < 1e-4:
        return {
            "accepted": False,
            "reason": "translation_excitation_low",
            "method": method,
        }
    direction_cosine = float(
        np.dot(camera_displacement_i, mast3r_delta_i)
        / (metric_distance * mast3r_distance)
    )
    expected_relative_rotation = mast3r_rotation_j.inv() * mast3r_rotation_i
    rotation_error_deg = float(
        np.degrees((expected_relative_rotation.inv() * pnp_rotation).magnitude())
    )
    if direction_cosine < 0.5:
        return {
            "accepted": False,
            "reason": "translation_direction_disagrees",
            "direction_cosine": direction_cosine,
            "method": method,
        }
    if rotation_error_deg > 5.0:
        return {
            "accepted": False,
            "reason": "rotation_disagrees",
            "rotation_error_deg": rotation_error_deg,
            "method": method,
        }
    scale = float(np.dot(camera_displacement_i, mast3r_delta_i) / mast3r_distance**2)
    if not 0.02 <= scale <= 20.0:
        return {
            "accepted": False,
            "reason": "scale_out_of_range",
            "scale": scale,
            "method": method,
        }
    return {
        "accepted": True,
        "scale": scale,
        "metric_distance_m": metric_distance,
        "metric_displacement_camera_i_m": camera_displacement_i.tolist(),
        "metric_displacement_frame": (
            "color_camera_i"
            if trajectory_frame == "color"
            else "infrared_left_camera_i"
        ),
        "mast3r_distance": mast3r_distance,
        "direction_cosine": direction_cosine,
        "rotation_error_deg": rotation_error_deg,
        "pnp_rotation_quaternion_xyzw": pnp_rotation.as_quat().tolist(),
        "tracked_points": int(len(object_points)),
        "pnp_inliers": int(len(inliers)),
        "pnp_inlier_ratio": inlier_ratio,
        "pnp_refined": pnp_refined,
        "pnp_rotation_mode": pnp_rotation_mode,
        "pnp_rotation_constrained": pnp_rotation_constrained,
        "pnp_free_rotation_delta_deg": free_rotation_delta_deg,
        "pnp_reprojection_median_px": float(np.median(reprojection_before)),
        "pnp_reprojection_p95_px": float(np.percentile(reprojection_before, 95)),
        "median_depth_m": float(np.median(z)),
        "method": method,
    }


def estimate_sift_fallback(
    left_i: np.ndarray,
    left_j: np.ndarray,
    disparity_left: np.ndarray,
    disparity_right: np.ndarray,
    mast3r_position_i: np.ndarray,
    mast3r_position_j: np.ndarray,
    mast3r_rotation_i: Rotation,
    mast3r_rotation_j: Rotation,
    calibration: dict,
    min_depth_m: float,
    max_depth_m: float,
    trajectory_frame: str = "color",
    pnp_rotation_mode: str = "free",
) -> dict:
    detector = cv2.SIFT_create(
        nfeatures=4000, contrastThreshold=0.01, edgeThreshold=15
    )
    disparity_mask = ((disparity_left > 0.5).astype(np.uint8) * 255)
    keypoints_i, descriptors_i = detector.detectAndCompute(left_i, disparity_mask)
    keypoints_j, descriptors_j = detector.detectAndCompute(left_j, None)
    if descriptors_i is None or descriptors_j is None:
        return {"accepted": False, "reason": "sift_descriptors_missing", "method": "sift"}
    matches = cv2.BFMatcher(cv2.NORM_L2).knnMatch(
        descriptors_i, descriptors_j, k=2
    )
    good = [first for first, second in matches if first.distance < 0.75 * second.distance]
    if len(good) < 30:
        return {"accepted": False, "reason": "insufficient_sift_matches", "method": "sift"}
    points_i = np.asarray(
        [keypoints_i[match.queryIdx].pt for match in good], dtype=np.float32
    )
    points_j = np.asarray(
        [keypoints_j[match.trainIdx].pt for match in good], dtype=np.float32
    )
    return estimate_motion_from_correspondences(
        points_i,
        points_j,
        np.ones(len(points_i), dtype=bool),
        disparity_left,
        disparity_right,
        mast3r_position_i,
        mast3r_position_j,
        mast3r_rotation_i,
        mast3r_rotation_j,
        calibration,
        min_depth_m,
        max_depth_m,
        method="sift",
        trajectory_frame=trajectory_frame,
        pnp_iterations=1000,
        pnp_rotation_mode=pnp_rotation_mode,
    )


def combine_bidirectional_scale(
    forward: dict,
    reverse: dict,
    max_relative_disagreement: float = 0.20,
) -> dict:
    """Keep only stereo motion estimates that agree in both time directions."""
    if not forward.get("accepted"):
        return forward
    if not reverse.get("accepted"):
        rejected = dict(forward)
        rejected.update(
            {
                "accepted": False,
                "reason": "reverse_motion_failed",
                "reverse_failure_reason": reverse.get("reason", "unknown"),
            }
        )
        return rejected
    forward_scale = float(forward["scale"])
    reverse_scale = float(reverse["scale"])
    mean_scale = 0.5 * (forward_scale + reverse_scale)
    relative_disagreement = abs(forward_scale - reverse_scale) / mean_scale
    if relative_disagreement > max_relative_disagreement:
        rejected = dict(forward)
        rejected.update(
            {
                "accepted": False,
                "reason": "bidirectional_scale_disagrees",
                "forward_scale": forward_scale,
                "reverse_scale": reverse_scale,
                "bidirectional_relative_disagreement": relative_disagreement,
            }
        )
        return rejected
    forward_weight = float(forward["metric_distance_m"]) ** 2 * max(
        float(forward.get("pnp_inlier_ratio", 0.25)), 0.25
    )
    reverse_weight = float(reverse["metric_distance_m"]) ** 2 * max(
        float(reverse.get("pnp_inlier_ratio", 0.25)), 0.25
    )
    combined = dict(forward)
    combined.update(
        {
            "scale": float(
                np.average(
                    [forward_scale, reverse_scale],
                    weights=[forward_weight, reverse_weight],
                )
            ),
            "scale_estimator": "bidirectional_pnp_weighted_mean",
            "forward_scale": forward_scale,
            "reverse_scale": reverse_scale,
            "bidirectional_relative_disagreement": relative_disagreement,
            "reverse_metric_distance_m": float(reverse["metric_distance_m"]),
            "reverse_pnp_inlier_ratio": float(reverse["pnp_inlier_ratio"]),
            "reverse_rotation_error_deg": float(reverse["rotation_error_deg"]),
        }
    )
    return combined


def combine_mast3r_bidirectional_scale(
    forward: dict, reverse: dict, match_quality: dict
) -> dict:
    """Allow a high-confidence learned forward edge when reverse depth is weak."""
    if reverse.get("accepted"):
        return combine_bidirectional_scale(forward, reverse)
    strong_forward = (
        forward.get("accepted")
        and int(forward.get("pnp_inliers", 0)) >= 80
        and float(forward.get("pnp_inlier_ratio", 0.0)) >= 0.30
        and float(forward.get("direction_cosine", 0.0)) >= 0.95
        and float(forward.get("rotation_error_deg", 180.0)) <= 2.0
        and float(match_quality.get("epipolar_inlier_ratio", 0.0)) >= 0.90
    )
    if not strong_forward:
        return combine_bidirectional_scale(forward, reverse)
    accepted = dict(forward)
    accepted.update(
        {
            "scale_estimator": "strict_forward_mast3r_pnp",
            "reverse_failure_reason": reverse.get("reason", "unknown"),
        }
    )
    return accepted


def estimate_pair_scale(
    left_i: np.ndarray,
    right_i: np.ndarray,
    left_j: np.ndarray,
    right_j: np.ndarray,
    mast3r_position_i: np.ndarray,
    mast3r_position_j: np.ndarray,
    mast3r_rotation_i: Rotation,
    mast3r_rotation_j: Rotation,
    calibration: dict,
    num_disparities: int,
    min_depth_m: float,
    max_depth_m: float,
    trajectory_frame: str = "color",
    motion_estimator: str = "pnp",
    correspondence_estimator: str = "classical",
    mast3r_matcher: dict | None = None,
    pnp_rotation_mode: str = "free",
) -> dict:
    disparity_left, disparity_right = stereo_disparity(
        left_i, right_i, num_disparities
    )
    if correspondence_estimator == "mast3r":
        if mast3r_matcher is None:
            raise ValueError("MASt3R correspondence model is not loaded")
        matches = estimate_mast3r_correspondences(left_i, left_j, mast3r_matcher)
        if not matches.get("accepted"):
            matches["method"] = "mast3r"
            return matches
        if motion_estimator == "stereo_3d":
            disparity_left_j, disparity_right_j = stereo_disparity(
                left_j, right_j, num_disparities
            )
            result = estimate_motion_from_stereo_3d(
                matches["points_i"],
                matches["points_j"],
                matches["correspondence_valid"],
                disparity_left,
                disparity_right,
                disparity_left_j,
                disparity_right_j,
                mast3r_position_i,
                mast3r_position_j,
                mast3r_rotation_i,
                mast3r_rotation_j,
                calibration,
                min_depth_m,
                max_depth_m,
                trajectory_frame=trajectory_frame,
            )
            result["method"] = "mast3r_stereo_3d"
        else:
            result = estimate_motion_from_correspondences(
                matches["points_i"],
                matches["points_j"],
                matches["correspondence_valid"],
                disparity_left,
                disparity_right,
                mast3r_position_i,
                mast3r_position_j,
                mast3r_rotation_i,
                mast3r_rotation_j,
                calibration,
                min_depth_m,
                max_depth_m,
                method="mast3r",
                trajectory_frame=trajectory_frame,
                pnp_iterations=1000,
                pnp_reprojection_error_px=4.0,
                pnp_rotation_mode=pnp_rotation_mode,
            )
            if result.get("accepted"):
                disparity_left_j, disparity_right_j = stereo_disparity(
                    left_j, right_j, num_disparities
                )
                reverse = estimate_motion_from_correspondences(
                    matches["points_j"],
                    matches["points_i"],
                    matches["correspondence_valid"],
                    disparity_left_j,
                    disparity_right_j,
                    mast3r_position_j,
                    mast3r_position_i,
                    mast3r_rotation_j,
                    mast3r_rotation_i,
                    calibration,
                    min_depth_m,
                    max_depth_m,
                    method="mast3r_reverse",
                    trajectory_frame=trajectory_frame,
                    pnp_iterations=1000,
                    pnp_reprojection_error_px=4.0,
                    pnp_rotation_mode=pnp_rotation_mode,
                )
                result = combine_mast3r_bidirectional_scale(result, reverse, matches)
        result.update(
            {
                key: matches[key]
                for key in (
                    "raw_matches",
                    "epipolar_inliers",
                    "epipolar_inlier_ratio",
                )
                if key in matches
            }
        )
        return result
    valid_disparity = disparity_left > 0.5
    features = cv2.goodFeaturesToTrack(
        left_i,
        maxCorners=2500,
        qualityLevel=0.01,
        minDistance=7,
        mask=(valid_disparity.astype(np.uint8) * 255),
        blockSize=7,
    )
    if features is None or len(features) < 40:
        fallback = estimate_sift_fallback(
            left_i,
            left_j,
            disparity_left,
            disparity_right,
            mast3r_position_i,
            mast3r_position_j,
            mast3r_rotation_i,
            mast3r_rotation_j,
            calibration,
            min_depth_m,
            max_depth_m,
            trajectory_frame,
            pnp_rotation_mode,
        )
        if fallback.get("accepted"):
            fallback["lk_failure_reason"] = "insufficient_stereo_features"
            return fallback
        return {
            "accepted": False,
            "reason": "insufficient_stereo_features",
            "method": "lk",
            "sift_failure_reason": fallback.get("reason"),
        }
    points_i = features.reshape(-1, 2)
    points_j, status_forward, _ = cv2.calcOpticalFlowPyrLK(
        left_i,
        left_j,
        points_i.astype(np.float32),
        None,
        winSize=(31, 31),
        maxLevel=4,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, 0.01),
    )
    points_back, status_back, _ = cv2.calcOpticalFlowPyrLK(
        left_j,
        left_i,
        points_j,
        None,
        winSize=(31, 31),
        maxLevel=4,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, 0.01),
    )
    flow_valid = status_forward.ravel().astype(bool) & status_back.ravel().astype(bool)
    flow_valid &= np.linalg.norm(points_back - points_i, axis=1) <= 1.0
    if motion_estimator == "stereo_3d":
        disparity_left_j, disparity_right_j = stereo_disparity(
            left_j, right_j, num_disparities
        )
        return estimate_motion_from_stereo_3d(
            points_i,
            points_j,
            flow_valid,
            disparity_left,
            disparity_right,
            disparity_left_j,
            disparity_right_j,
            mast3r_position_i,
            mast3r_position_j,
            mast3r_rotation_i,
            mast3r_rotation_j,
            calibration,
            min_depth_m,
            max_depth_m,
            trajectory_frame,
        )
    result = estimate_motion_from_correspondences(
        points_i,
        points_j,
        flow_valid,
        disparity_left,
        disparity_right,
        mast3r_position_i,
        mast3r_position_j,
        mast3r_rotation_i,
        mast3r_rotation_j,
        calibration,
        min_depth_m,
        max_depth_m,
        method="lk",
        trajectory_frame=trajectory_frame,
        pnp_rotation_mode=pnp_rotation_mode,
    )
    if result.get("accepted"):
        disparity_left_j, disparity_right_j = stereo_disparity(
            left_j, right_j, num_disparities
        )
        reverse = estimate_motion_from_correspondences(
            points_j,
            points_i,
            flow_valid,
            disparity_left_j,
            disparity_right_j,
            mast3r_position_j,
            mast3r_position_i,
            mast3r_rotation_j,
            mast3r_rotation_i,
            calibration,
            min_depth_m,
            max_depth_m,
            method="lk_reverse",
            trajectory_frame=trajectory_frame,
            pnp_rotation_mode=pnp_rotation_mode,
        )
        return combine_bidirectional_scale(result, reverse)
    if result.get("reason") == "translation_excitation_low":
        return result
    fallback = estimate_sift_fallback(
        left_i,
        left_j,
        disparity_left,
        disparity_right,
        mast3r_position_i,
        mast3r_position_j,
        mast3r_rotation_i,
        mast3r_rotation_j,
        calibration,
        min_depth_m,
        max_depth_m,
        trajectory_frame,
        pnp_rotation_mode,
    )
    if fallback.get("accepted"):
        fallback["lk_failure_reason"] = result.get("reason")
        return fallback
    result["sift_failure_reason"] = fallback.get("reason")
    return result


def robust_scale(observations: list[dict], min_observations: int) -> tuple[float, dict]:
    accepted = [item for item in observations if item.get("accepted")]
    if len(accepted) < min_observations:
        raise ValueError(
            f"insufficient accepted stereo scale observations: {len(accepted)} < {min_observations}"
        )
    scales = np.asarray([item["scale"] for item in accepted])
    median = float(np.median(scales))
    mad = float(np.median(np.abs(scales - median)))
    tolerance = max(3.0 * 1.4826 * mad, 0.15 * median)
    inlier_mask = np.abs(scales - median) <= tolerance
    inlier_scales = scales[inlier_mask]
    if len(inlier_scales) < min_observations:
        raise ValueError("insufficient robust stereo scale inliers")
    inlier_observations = [
        observation
        for observation, keep in zip(accepted, inlier_mask)
        if keep
    ]
    if all("metric_distance_m" in item for item in inlier_observations):
        weights = np.asarray(
            [
                float(item["metric_distance_m"]) ** 2
                * max(float(item.get("pnp_inlier_ratio", 0.25)), 0.25)
                for item in inlier_observations
            ]
        )
        weight_limit = float(np.percentile(weights, 95))
        weights = np.minimum(weights, weight_limit)
        scale = float(np.average(inlier_scales, weights=weights))
        estimator = "robust_inverse_variance_weighted_mean"
    else:
        weights = np.ones_like(inlier_scales)
        weight_limit = 1.0
        scale = float(np.median(inlier_scales))
        estimator = "robust_median_legacy_fallback"
    relative_p90_p10 = float(
        (np.percentile(inlier_scales, 90) - np.percentile(inlier_scales, 10)) / scale
    )
    if relative_p90_p10 > 0.5:
        raise ValueError(
            f"stereo scale dispersion too high: relative_p90_p10={relative_p90_p10:.3f}"
        )
    return scale, {
        "accepted_observations": len(accepted),
        "robust_inliers": int(len(inlier_scales)),
        "median_before_filter": median,
        "mad_before_filter": mad,
        "relative_p90_p10": relative_p90_p10,
        "scale_estimator": estimator,
        "weight_p95_limit": weight_limit,
    }


def trajectory_step_continuity(
    positions: np.ndarray, scale: float, times: np.ndarray | None = None
) -> dict:
    """Catch isolated frame jumps without mistaking missing frames for one step."""
    points = np.asarray(positions, dtype=float)
    if (
        points.ndim != 2
        or points.shape[1] != 3
        or len(points) < 2
        or not np.isfinite(points).all()
        or not np.isfinite(scale)
        or scale <= 0.0
    ):
        return {"result": "FAIL", "reason": "invalid_trajectory", "jump_count": 0}
    if times is None:
        intervals = np.ones(len(points) - 1)
    else:
        stamps = np.asarray(times, dtype=float)
        if (
            stamps.shape != (len(points),)
            or not np.isfinite(stamps).all()
            or not (np.diff(stamps) > 0).all()
        ):
            return {"result": "FAIL", "reason": "invalid_timestamps", "jump_count": 0}
        intervals = np.diff(stamps)
    nominal_interval = float(np.median(intervals))
    gaps = intervals > 1.5 * nominal_interval
    steps = np.linalg.norm(np.diff(points, axis=0), axis=1) * scale
    equivalent_steps = steps * nominal_interval / intervals
    local_median = np.asarray(
        [
            np.median(equivalent_steps[max(0, index - 15) : index + 16])
            for index in range(len(steps))
        ]
    )
    jumps = (~gaps) & (equivalent_steps > np.maximum(0.030, 5.0 * local_median))
    return {
        "result": "FAIL" if jumps.any() else "PASS",
        "reason": "isolated_position_step_jump" if jumps.any() else None,
        "jump_count": int(jumps.sum()),
        "first_jump_index": int(np.flatnonzero(jumps)[0]) if jumps.any() else None,
        "max_step_m": float(steps.max()),
        "max_contiguous_step_m": float(steps[~gaps].max()) if (~gaps).any() else None,
        "unverified_gap_count": int(gaps.sum()),
        "max_gap_s": float(intervals[gaps].max()) if gaps.any() else 0.0,
        "absolute_step_limit_m": 0.030,
        "local_median_multiplier": 5.0,
    }


def write_scaled_trajectory(
    output: Path,
    rows: list[dict],
    positions: np.ndarray,
    scale: float,
) -> None:
    scaled = positions[0] + scale * (positions - positions[0])
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    with output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row, position in zip(rows, scaled):
            updated = dict(row)
            for key, value in zip(("x", "y", "z"), position):
                updated[key] = f"{value:.9f}"
            writer.writerow(updated)


def run(args: argparse.Namespace) -> dict:
    session = args.session.resolve()
    db3 = select_db3(session)
    times, positions, quaternions, rows = load_trajectory(args.trajectory)
    left_numbers, right_numbers, sync = match_trajectory_to_stereo_frames(
        session / "d405_frames.csv", times, trajectory_frame=args.trajectory_frame
    )
    if args.prepared_dataset is not None:
        if args.trajectory_frame != "infrared_left":
            raise ValueError("--prepared-dataset requires infrared_left trajectory")
        calibration = load_stereo_calibration_from_prepared_dataset(
            args.prepared_dataset.resolve()
        )
    else:
        calibration = load_stereo_calibration(db3)
    sample_indices = np.arange(0, len(times), args.frame_step, dtype=int)
    if sample_indices[-1] != len(times) - 1:
        sample_indices = np.append(sample_indices, len(times) - 1)
    hop_values = (
        tuple(int(value) for value in args.hop_values.split(","))
        if args.hop_values
        else None
    )
    pairs = sample_pairs(sample_indices, args.max_hop, hop_values)
    if args.retry_failed_report is not None:
        previous = json.loads(args.retry_failed_report.read_text(encoding="utf-8"))
        retry_pairs = {
            (int(observation["first_index"]), int(observation["second_index"]))
            for observation in previous.get("observations", [])
            if not observation.get("accepted")
            and observation.get("reason") != "translation_excitation_low"
        }
        pairs = [
            pair for pair in pairs if (int(pair[0]), int(pair[1])) in retry_pairs
        ]
    if args.max_pairs > 0:
        pairs = pairs[: args.max_pairs]
    selected_indices = sorted(
        {index for first, second, _hop in pairs for index in (first, second)}
    )
    selected_left = {int(left_numbers[index]) for index in selected_indices}
    selected_right = {int(right_numbers[index]) for index in selected_indices}
    if args.prepared_dataset is not None:
        left_images, right_images = load_selected_prepared_stereo_images(
            args.prepared_dataset.resolve(),
            session / "d405_frames.csv",
            selected_left,
            selected_right,
        )
    else:
        left_images, right_images = load_selected_stereo_images(
            db3, selected_left, selected_right
        )
    rotations = Rotation.from_quat(quaternions)
    mast3r_matcher = None
    if args.correspondence_estimator == "mast3r":
        mast3r_matcher = load_mast3r_correspondence_model(
            args.mast3r_weights, args.mast3r_device
        )
    observations = []
    for first, second, hop in pairs:
        result = estimate_pair_scale(
            left_images[int(left_numbers[first])],
            right_images[int(right_numbers[first])],
            left_images[int(left_numbers[second])],
            right_images[int(right_numbers[second])],
            positions[first],
            positions[second],
            rotations[first],
            rotations[second],
            calibration,
            args.num_disparities,
            args.min_depth_m,
            args.max_depth_m,
            args.trajectory_frame,
            args.motion_estimator,
            args.correspondence_estimator,
            mast3r_matcher,
            args.pnp_rotation_mode,
        )
        result.update(
            {
                "first_index": int(first),
                "second_index": int(second),
                "first_t_sec": float(times[first]),
                "second_t_sec": float(times[second]),
                "sample_hop": int(hop),
            }
        )
        observations.append(result)
    report = {
        "schema": "umi_mast3r_stereo_scale_v2",
        "result": "FAIL",
        "slam_supervision": False,
        "external_ground_truth_used": False,
        "inputs": (
            f"MASt3R {args.trajectory_frame} poses + synchronized D405 stereo IR "
            "+ factory calibration only"
        ),
        "session": str(session),
        "trajectory": str(args.trajectory.resolve()),
        "db3": str(db3),
        "prepared_dataset": (
            str(args.prepared_dataset.resolve())
            if args.prepared_dataset is not None
            else None
        ),
        "factory_stereo_calibration": {
            "baseline_m": calibration["baseline_m"],
            "left_intrinsics": calibration["left"],
            "right_intrinsics": calibration["right"],
            "right_translation_from_left_m": calibration[
                "right_translation_from_left_m"
            ].tolist(),
            "color_rotation_from_left": calibration[
                "color_rotation_from_left"
            ].tolist(),
            "color_translation_from_left_m": calibration[
                "color_translation_from_left_m"
            ].tolist(),
            "factory_topics": calibration["factory_topics"],
        },
        "observation_frame": (
            "color_camera_i"
            if args.trajectory_frame == "color"
            else "infrared_left_camera_i"
        ),
        "synchronization": sync,
        "frame_step": args.frame_step,
        "max_hop": args.max_hop,
        "hop_values": list(hop_values) if hop_values is not None else None,
        "motion_estimator": args.motion_estimator,
        "correspondence_estimator": args.correspondence_estimator,
        "pnp_rotation_mode": args.pnp_rotation_mode,
        "retry_failed_report": (
            str(args.retry_failed_report.resolve())
            if args.retry_failed_report is not None
            else None
        ),
        "candidate_pairs": len(pairs),
        "observations": observations,
    }
    try:
        scale, quality = robust_scale(observations, args.min_observations)
    except ValueError as error:
        report["failures"] = [str(error)]
    else:
        continuity = trajectory_step_continuity(positions, scale, times)
        report.update(
            {
                "scale_m_per_mast3r_unit": scale,
                "quality": quality,
                "trajectory_continuity": continuity,
            }
        )
        if continuity["result"] == "FAIL":
            report["failures"] = [continuity["reason"]]
        else:
            report.update(
                {
                    "result": "PASS",
                    "failures": [],
                    "output": str(args.output.resolve()),
                }
            )
            write_scaled_trajectory(args.output, rows, positions, scale)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trajectory", type=Path, required=True)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--prepared-dataset", type=Path)
    parser.add_argument("--frame-step", type=int, default=15)
    parser.add_argument("--max-pairs", type=int, default=0)
    parser.add_argument("--max-hop", type=int, default=1)
    parser.add_argument(
        "--hop-values",
        help="comma-separated sampled hops; overrides the dense 1..max-hop range",
    )
    parser.add_argument("--min-observations", type=int, default=4)
    parser.add_argument("--num-disparities", type=int, default=128)
    parser.add_argument("--min-depth-m", type=float, default=0.07)
    parser.add_argument("--max-depth-m", type=float, default=1.5)
    parser.add_argument(
        "--trajectory-frame",
        choices=("color", "infrared_left"),
        default="color",
    )
    parser.add_argument(
        "--motion-estimator",
        choices=("pnp", "stereo_3d"),
        default="pnp",
    )
    parser.add_argument(
        "--correspondence-estimator",
        choices=("classical", "mast3r"),
        default="classical",
    )
    parser.add_argument("--mast3r-weights", type=Path)
    parser.add_argument("--mast3r-device", default="cuda")
    parser.add_argument(
        "--pnp-rotation-mode",
        choices=("free", "trajectory-fixed"),
        default="free",
    )
    parser.add_argument("--retry-failed-report", type=Path)
    args = parser.parse_args()
    if args.frame_step < 1:
        parser.error("--frame-step must be positive")
    if args.frame_step == 1 and args.retry_failed_report is None:
        parser.error("--frame-step 1 is allowed only for an explicit retry report")
    if args.max_hop < 1:
        parser.error("--max-hop must be positive")
    report = run(args)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["result"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
