#!/usr/bin/env python3
"""Evaluate a gated depth-plane factor without feeding truth into SLAM.

The tool reads native D405 depth frames from a RealSense DB3 recording.  It
first checks whether each depth image contains a sufficiently large, precise
plane.  When a VINS body trajectory is supplied, it additionally transforms
the observed plane into the world frame and checks temporal consistency.

This is deliberately an offline diagnostic/prototype.  It never assumes that
the camera height is constant: genuine vertical motion changes the measured
camera-to-plane distance while the world plane itself remains fixed.
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import re
import sqlite3
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial.transform import Rotation, Slerp


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from evaluate_slam_ground_truth import load_opencv_matrix, load_trajectory


CAMERA_INFO_RE = re.compile(
    r"width=(\d+);height=(\d+);fx=([0-9.eE+-]+);ppx=([0-9.eE+-]+);"
    r"fy=([0-9.eE+-]+);ppy=([0-9.eE+-]+)"
)
METADATA_TIME_RE = re.compile(r"timestamp=([0-9.]+)")
DEPTH_DATA_TOPIC = "/device_0/sensor_0/Depth_0/image/data"
DEPTH_METADATA_TOPIC = "/device_0/sensor_0/Depth_0/image/metadata"
DEPTH_CAMERA_INFO_TOPIC = "/device_0/sensor_0/Depth_0/camera_info"
DEPTH_UNIT_TOPIC = "/device_0/sensor_0/option/Depth_Units/value"


@dataclass(frozen=True)
class Intrinsics:
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float


@dataclass
class PlaneObservation:
    epoch_s: float
    relative_s: float
    valid_points: int
    inlier_ratio: float
    median_residual_m: float
    p95_residual_m: float
    normal_camera: list[float]
    offset_camera_m: float
    local_gate_pass: bool
    pose_matched: bool = False
    normal_world: list[float] | None = None
    offset_world_m: float | None = None
    world_angle_error_deg: float | None = None
    world_offset_error_m: float | None = None
    temporal_gate_pass: bool = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="评估D405深度平面质量及其世界坐标稳定性"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--session", type=Path)
    source.add_argument(
        "--observations-csv",
        type=Path,
        help="复用既有depth_plane_frames.csv，跳过原始DB3深度重扫",
    )
    parser.add_argument("--trajectory", type=Path)
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config/product_live_stm32/vins_config.yaml",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sample-every", type=int, default=15)
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--grid-stride", type=int, default=12)
    parser.add_argument(
        "--roi-top-fraction",
        type=float,
        default=0.0,
        help=(
            "只在该归一化图像高度以下拟合平面；0使用原全图，"
            "0.5用于隔离下半图地面候选"
        ),
    )
    parser.add_argument("--min-depth-m", type=float, default=0.07)
    parser.add_argument("--max-depth-m", type=float, default=1.5)
    parser.add_argument("--ransac-threshold-m", type=float, default=0.006)
    parser.add_argument("--min-inlier-ratio", type=float, default=0.15)
    parser.add_argument("--min-inlier-points", type=int, default=220)
    parser.add_argument("--max-p95-residual-m", type=float, default=0.006)
    parser.add_argument("--world-angle-gate-deg", type=float, default=4.0)
    parser.add_argument("--world-offset-gate-m", type=float, default=0.025)
    parser.add_argument("--max-horizontal-tilt-deg", type=float, default=12.0)
    parser.add_argument("--plane-factor-gain", type=float, default=0.35)
    parser.add_argument("--plane-factor-max-correction-m", type=float, default=0.03)
    parser.add_argument("--plane-factor-max-gap-s", type=float, default=0.75)
    parser.add_argument("--plane-factor-min-support", type=int, default=5)
    parser.add_argument("--plane-factor-max-slew-mps", type=float, default=0.03)
    parser.add_argument(
        "--corrected-trajectory",
        type=Path,
        help="可选输出：仅在平面门控通过区间施加软Z修正的轨迹CSV",
    )
    return parser.parse_args()


def fit_plane_ransac(
    points: np.ndarray,
    threshold_m: float,
    iterations: int = 160,
    seed: int = 7,
) -> tuple[np.ndarray, float, np.ndarray]:
    if len(points) < 3:
        raise ValueError("at least three points are required")
    generator = np.random.default_rng(seed)
    best_mask: np.ndarray | None = None
    best_count = 0
    for _ in range(iterations):
        sample = points[generator.choice(len(points), 3, replace=False)]
        normal = np.cross(sample[1] - sample[0], sample[2] - sample[0])
        norm = np.linalg.norm(normal)
        if norm < 1e-8:
            continue
        normal /= norm
        offset = -float(normal @ sample[0])
        mask = np.abs(points @ normal + offset) <= threshold_m
        count = int(mask.sum())
        if count > best_count:
            best_count = count
            best_mask = mask
    if best_mask is None or best_count < 3:
        raise ValueError("RANSAC could not find a plane")

    inliers = points[best_mask]
    center = inliers.mean(axis=0)
    _, _, vh = np.linalg.svd(inliers - center, full_matrices=False)
    normal = vh[-1]
    normal /= np.linalg.norm(normal)
    offset = -float(normal @ center)
    if float(normal @ center) < 0.0:
        normal = -normal
        offset = -offset
    residuals = np.abs(points @ normal + offset)
    mask = residuals <= threshold_m
    return normal, offset, mask


def transform_plane_to_world(
    normal_camera: np.ndarray,
    offset_camera: float,
    world_rotation_camera: Rotation,
    world_position_camera: np.ndarray,
) -> tuple[np.ndarray, float]:
    normal_world = world_rotation_camera.apply(normal_camera)
    offset_world = float(offset_camera - normal_world @ world_position_camera)
    return normal_world, offset_world


def parse_intrinsics(text: str) -> Intrinsics:
    match = CAMERA_INFO_RE.search(text)
    if match is None:
        raise ValueError(f"unsupported depth camera_info: {text[:200]}")
    width, height, fx, cx, fy, cy = match.groups()
    return Intrinsics(
        int(width), int(height), float(fx), float(fy), float(cx), float(cy)
    )


def select_db3(session: Path) -> Path:
    candidates = [path for path in session.glob("*.db3") if path.stat().st_size > 0]
    if not candidates:
        raise FileNotFoundError(f"no non-empty DB3 in {session}")
    return max(candidates, key=lambda path: path.stat().st_size)


def load_observations_csv(path: Path) -> list[PlaneObservation]:
    vector_fields = {"normal_camera", "normal_world"}
    optional_float_fields = {
        "offset_world_m",
        "world_angle_error_deg",
        "world_offset_error_m",
    }
    boolean_fields = {
        "local_gate_pass",
        "pose_matched",
        "temporal_gate_pass",
    }
    observations: list[PlaneObservation] = []
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            values: dict[str, object] = {}
            for field in PlaneObservation.__dataclass_fields__:
                value = row[field]
                if field in vector_fields:
                    values[field] = ast.literal_eval(value) if value else None
                elif field in optional_float_fields:
                    values[field] = float(value) if value else None
                elif field in boolean_fields:
                    values[field] = value == "True"
                elif field == "valid_points":
                    values[field] = int(value)
                else:
                    values[field] = float(value)
            observations.append(PlaneObservation(**values))
    if not observations:
        raise RuntimeError(f"no observations in {path}")
    return observations


def read_static_string(
    database: sqlite3.Connection, topic_ids: dict[str, int], topic: str
) -> str:
    from rclpy.serialization import deserialize_message
    from std_msgs.msg import String

    row = database.execute(
        "SELECT data FROM messages WHERE topic_id = ? ORDER BY id LIMIT 1",
        (topic_ids[topic],),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"missing topic value: {topic}")
    return deserialize_message(bytes(row[0]), String).data


def sampled_depth_rows(
    database: sqlite3.Connection,
    topic_ids: dict[str, int],
    sample_every: int,
    max_frames: int,
) -> Iterator[tuple[int, bytes, bytes]]:
    if sample_every < 1:
        raise ValueError("sample_every must be positive")
    rows = database.execute(
        """
        SELECT topic_id, timestamp, data
        FROM messages
        WHERE topic_id IN (?, ?)
        ORDER BY id
        """,
        (topic_ids[DEPTH_DATA_TOPIC], topic_ids[DEPTH_METADATA_TOPIC]),
    )
    image_topic_id = topic_ids[DEPTH_DATA_TOPIC]
    pending_images: dict[int, bytes] = {}
    pending_metadata: dict[int, bytes] = {}
    complete_index = 0
    selected = 0
    for topic_id, timestamp, blob in rows:
        pending = pending_images if topic_id == image_topic_id else pending_metadata
        pending[timestamp] = bytes(blob)
        if timestamp not in pending_images or timestamp not in pending_metadata:
            continue
        image_blob = pending_images.pop(timestamp)
        metadata_blob = pending_metadata.pop(timestamp)
        if complete_index % sample_every == 0:
            yield timestamp, image_blob, metadata_blob
            selected += 1
            if max_frames > 0 and selected >= max_frames:
                return
        complete_index += 1


def depth_points(
    image: np.ndarray,
    intrinsics: Intrinsics,
    depth_unit_m: float,
    stride: int,
    min_depth_m: float,
    max_depth_m: float,
    roi_top_fraction: float = 0.0,
) -> np.ndarray:
    if not 0.0 <= roi_top_fraction < 0.9:
        raise ValueError("roi_top_fraction must be in [0, 0.9)")
    margin_x = intrinsics.width // 10
    margin_y = intrinsics.height // 10
    roi_top = max(margin_y, int(np.ceil(intrinsics.height * roi_top_fraction)))
    roi_bottom = intrinsics.height - margin_y
    if roi_top >= roi_bottom:
        raise ValueError("depth ROI is empty after applying image margins")
    ys = np.arange(roi_top, roi_bottom, stride)
    xs = np.arange(margin_x, intrinsics.width - margin_x, stride)
    xx, yy = np.meshgrid(xs, ys)
    zz = image[yy, xx].astype(np.float64) * depth_unit_m
    valid = (zz >= min_depth_m) & (zz <= max_depth_m)
    zz = zz[valid]
    xx = xx[valid]
    yy = yy[valid]
    x = (xx - intrinsics.cx) * zz / intrinsics.fx
    y = (yy - intrinsics.cy) * zz / intrinsics.fy
    return np.column_stack((x, y, zz))


def extract_observations(
    session: Path,
    sample_every: int,
    max_frames: int,
    grid_stride: int,
    min_depth_m: float,
    max_depth_m: float,
    ransac_threshold_m: float,
    min_inlier_ratio: float,
    min_inlier_points: int,
    max_p95_residual_m: float,
    roi_top_fraction: float = 0.0,
) -> tuple[list[PlaneObservation], Intrinsics, float, Path]:
    from rclpy.serialization import deserialize_message
    from sensor_msgs.msg import Image as RosImage
    from std_msgs.msg import String

    db3 = select_db3(session)
    database = sqlite3.connect(str(db3))
    topic_ids = {
        name: topic_id
        for topic_id, name in database.execute("SELECT id, name FROM topics")
    }
    required = {
        DEPTH_DATA_TOPIC,
        DEPTH_METADATA_TOPIC,
        DEPTH_CAMERA_INFO_TOPIC,
        DEPTH_UNIT_TOPIC,
    }
    missing = sorted(required - topic_ids.keys())
    if missing:
        database.close()
        raise RuntimeError("missing depth topics: " + ", ".join(missing))
    intrinsics = parse_intrinsics(
        read_static_string(database, topic_ids, DEPTH_CAMERA_INFO_TOPIC)
    )
    depth_unit_m = float(read_static_string(database, topic_ids, DEPTH_UNIT_TOPIC))
    rows = sampled_depth_rows(
        database, topic_ids, sample_every=sample_every, max_frames=max_frames
    )
    observations: list[PlaneObservation] = []
    first_epoch_s: float | None = None
    for bag_timestamp, image_blob, metadata_blob in rows:
        metadata = deserialize_message(bytes(metadata_blob), String).data
        timestamp_match = METADATA_TIME_RE.search(metadata)
        if timestamp_match is None:
            continue
        epoch_s = float(timestamp_match.group(1)) / 1000.0
        if first_epoch_s is None:
            first_epoch_s = epoch_s
        message = deserialize_message(bytes(image_blob), RosImage)
        if message.encoding.lower() not in {"mono16", "16uc1"}:
            raise ValueError(f"unsupported depth encoding: {message.encoding}")
        columns = message.step // 2
        image = np.frombuffer(message.data, dtype="<u2").reshape(
            message.height, columns
        )[:, : message.width]
        points = depth_points(
            image,
            intrinsics,
            depth_unit_m,
            grid_stride,
            min_depth_m,
            max_depth_m,
            roi_top_fraction,
        )
        if len(points) < 100:
            continue
        normal, offset, mask = fit_plane_ransac(
            points, threshold_m=ransac_threshold_m
        )
        residuals = np.abs(points[mask] @ normal + offset)
        inlier_ratio = float(mask.mean())
        p95 = float(np.percentile(residuals, 95))
        observations.append(
            PlaneObservation(
                epoch_s=epoch_s,
                relative_s=(bag_timestamp * 1e-9),
                valid_points=len(points),
                inlier_ratio=inlier_ratio,
                median_residual_m=float(np.median(residuals)),
                p95_residual_m=p95,
                normal_camera=normal.tolist(),
                offset_camera_m=float(offset),
                local_gate_pass=(
                    inlier_ratio >= min_inlier_ratio
                    and int(mask.sum()) >= min_inlier_points
                    and p95 <= max_p95_residual_m
                ),
            )
        )
    database.close()
    if not observations:
        raise RuntimeError("no usable depth plane observations")
    return observations, intrinsics, depth_unit_m, db3


def match_trajectory(
    observations: list[PlaneObservation], trajectory: Path, config: Path
) -> None:
    times, positions, quaternions = load_trajectory(trajectory)
    body_t_camera = load_opencv_matrix(config, "body_T_cam0")
    body_rotations = Rotation.from_quat(quaternions)
    camera_rotation_in_body = Rotation.from_matrix(body_t_camera[:3, :3])
    camera_position_in_body = body_t_camera[:3, 3]
    slerp = Slerp(times, body_rotations)

    selected = [item for item in observations if times[0] <= item.epoch_s <= times[-1]]
    if not selected:
        raise RuntimeError("depth and trajectory timestamps do not overlap")
    selected_times = np.asarray([item.epoch_s for item in selected])
    right = np.clip(np.searchsorted(times, selected_times), 1, len(times) - 1)
    nearest_gap = np.minimum(
        np.abs(selected_times - times[right - 1]), np.abs(times[right] - selected_times)
    )
    rotations = slerp(selected_times)
    camera_rotations = rotations * camera_rotation_in_body
    body_positions = np.column_stack(
        [np.interp(selected_times, times, positions[:, axis]) for axis in range(3)]
    )
    camera_positions = body_positions + rotations.apply(camera_position_in_body)
    for index, item in enumerate(selected):
        if nearest_gap[index] > 0.15 or not item.local_gate_pass:
            continue
        normal, offset = transform_plane_to_world(
            np.asarray(item.normal_camera),
            item.offset_camera_m,
            camera_rotations[index],
            camera_positions[index],
        )
        item.pose_matched = True
        item.normal_world = normal.tolist()
        item.offset_world_m = offset


def apply_temporal_gate(
    observations: list[PlaneObservation],
    angle_gate_deg: float,
    offset_gate_m: float,
    max_horizontal_tilt_deg: float,
) -> dict[str, object] | None:
    locally_matched = [
        item
        for item in observations
        if item.pose_matched and item.normal_world is not None
    ]
    candidates = []
    for item in locally_matched:
        normal = np.asarray(item.normal_world)
        horizontal_tilt_deg = float(
            np.degrees(np.arccos(np.clip(abs(normal[2]), 0.0, 1.0)))
        )
        if horizontal_tilt_deg <= max_horizontal_tilt_deg:
            candidates.append(item)
    if len(candidates) < 5:
        return {
            "locally_matched_observations": len(locally_matched),
            "horizontal_candidates": len(candidates),
            "matched_observations": len(candidates),
            "accepted_observations": 0,
            "accepted_fraction": 0.0,
            "disabled_reason": "insufficient gravity-aligned horizontal planes",
        }
    normals = np.asarray([item.normal_world for item in candidates])
    offsets = np.asarray([item.offset_world_m for item in candidates], dtype=float)
    reference = normals[0]
    signs = np.where(normals @ reference < 0.0, -1.0, 1.0)
    normals *= signs[:, None]
    offsets *= signs

    pairwise_cosine = np.clip(normals @ normals.T, -1.0, 1.0)
    pairwise_angles = np.degrees(np.arccos(pairwise_cosine))
    pairwise_offsets = np.abs(offsets[:, None] - offsets[None, :])
    support = (
        (pairwise_angles <= angle_gate_deg)
        & (pairwise_offsets <= offset_gate_m)
    ).sum(axis=1)
    center_index = int(np.argmax(support))
    member_mask = (
        (pairwise_angles[center_index] <= angle_gate_deg)
        & (pairwise_offsets[center_index] <= offset_gate_m)
    )
    cluster_normals = normals[member_mask]
    reference_normal = cluster_normals.mean(axis=0)
    reference_normal /= np.linalg.norm(reference_normal)
    reference_offset = float(np.median(offsets[member_mask]))

    for item, normal, offset in zip(candidates, normals, offsets):
        angle_error = float(
            np.degrees(np.arccos(np.clip(normal @ reference_normal, -1.0, 1.0)))
        )
        offset_error = float(abs(offset - reference_offset))
        item.world_angle_error_deg = angle_error
        item.world_offset_error_m = offset_error
        item.temporal_gate_pass = (
            angle_error <= angle_gate_deg and offset_error <= offset_gate_m
        )
    accepted_offsets = np.asarray(
        [item.offset_world_m for item in candidates if item.temporal_gate_pass]
    )
    return {
        "locally_matched_observations": len(locally_matched),
        "horizontal_candidates": len(candidates),
        "reference_normal_world": reference_normal.tolist(),
        "reference_offset_world_m": reference_offset,
        "matched_observations": len(candidates),
        "accepted_observations": int(
            sum(item.temporal_gate_pass for item in candidates)
        ),
        "accepted_fraction": float(
            sum(item.temporal_gate_pass for item in candidates) / len(candidates)
        ),
        "accepted_world_offset_std_m": float(np.std(accepted_offsets)),
        "accepted_world_offset_p95_abs_error_m": float(
            np.percentile(np.abs(accepted_offsets - reference_offset), 95)
        ),
    }


def plane_factor_correction(
    observations: list[PlaneObservation],
    trajectory_times: np.ndarray,
    trajectory_positions: np.ndarray,
    gain: float,
    max_correction_m: float,
    max_gap_s: float,
    min_support: int,
    max_slew_mps: float,
    angle_gate_deg: float = 4.0,
    offset_gate_m: float = 0.025,
    max_horizontal_tilt_deg: float = 12.0,
) -> tuple[np.ndarray, np.ndarray, dict[str, object]]:
    """Apply a causal bounded gravity-axis correction from a stable plane.

    The reference is frozen only after consecutive past observations agree;
    future observations never affect an earlier correction.  A real camera
    elevation changes the camera-frame plane distance, so it does not appear
    as a world-plane residual and is retained.  No absolute height or endpoint
    information enters this calculation.
    """
    if not 0.0 < gain <= 1.0:
        raise ValueError("plane factor gain must be in (0, 1]")
    if max_correction_m <= 0.0 or max_gap_s <= 0.0 or max_slew_mps <= 0.0:
        raise ValueError("plane factor limits must be positive")
    if not 0.0 < max_horizontal_tilt_deg < 90.0:
        raise ValueError("horizontal tilt limit must be in (0, 90) degrees")
    if min_support < 2:
        raise ValueError("plane factor min_support must be at least 2")
    if angle_gate_deg <= 0.0 or offset_gate_m <= 0.0:
        raise ValueError("plane consistency gates must be positive")
    if len(trajectory_times) != len(trajectory_positions):
        raise ValueError("trajectory time/position length mismatch")
    if trajectory_positions.ndim != 2 or trajectory_positions.shape[1] != 3:
        raise ValueError("trajectory positions must have shape (N, 3)")
    if len(trajectory_times) < 2:
        raise ValueError("trajectory must contain at least two samples")
    if not np.all(np.isfinite(trajectory_times)) or not np.all(
        np.isfinite(trajectory_positions)
    ):
        raise ValueError("trajectory contains non-finite values")
    if np.any(np.diff(trajectory_times) <= 0.0):
        raise ValueError("trajectory timestamps must be strictly increasing")

    disabled = {
        "status": "DISABLED",
        "reason": "no stable gravity-aligned plane",
        "support_observations": 0,
        "active_trajectory_samples": 0,
        "causal": True,
        "uses_absolute_height": False,
        "uses_endpoint_constraint": False,
    }
    eligible = sorted(
        (
            item
            for item in observations
            if item.local_gate_pass
            and item.pose_matched
            and item.normal_world is not None
            and item.offset_world_m is not None
        ),
        key=lambda item: item.epoch_s,
    )
    if not eligible:
        return trajectory_positions.copy(), np.zeros(len(trajectory_times)), disabled

    gravity_axis = np.array([0.0, 0.0, 1.0])
    correction = np.zeros(len(trajectory_times), dtype=float)
    active_samples = 0
    support_used = 0
    activations = 0
    resets = 0
    event_index = 0
    warmup: list[tuple[np.ndarray, float]] = []
    reference_normal: np.ndarray | None = None
    reference_offset: float | None = None
    last_observation_s: float | None = None
    target_correction = 0.0

    def reset() -> None:
        nonlocal reference_normal, reference_offset, target_correction, resets
        if reference_normal is not None or warmup:
            resets += 1
        reference_normal = None
        reference_offset = None
        target_correction = 0.0
        warmup.clear()

    def angle_deg(left: np.ndarray, right: np.ndarray) -> float:
        return float(
            np.degrees(np.arccos(np.clip(float(left @ right), -1.0, 1.0)))
        )

    def process(item: PlaneObservation) -> None:
        nonlocal reference_normal, reference_offset, last_observation_s
        nonlocal target_correction, activations, support_used
        if (
            last_observation_s is not None
            and item.epoch_s - last_observation_s > max_gap_s
        ):
            reset()
        last_observation_s = item.epoch_s
        normal = np.asarray(item.normal_world, dtype=float)
        offset = float(item.offset_world_m)
        normal_norm = np.linalg.norm(normal)
        if (
            normal.shape != (3,)
            or not np.all(np.isfinite(normal))
            or not np.isfinite(offset)
            or normal_norm < 1e-8
        ):
            reset()
            return
        normal /= normal_norm
        if normal @ gravity_axis < 0.0:
            normal = -normal
            offset = -offset
        tilt = angle_deg(normal, gravity_axis)
        if tilt > max_horizontal_tilt_deg:
            reset()
            return

        if reference_normal is None:
            if warmup:
                warmup_normals = np.asarray([sample[0] for sample in warmup])
                warmup_normal = warmup_normals.mean(axis=0)
                warmup_normal /= np.linalg.norm(warmup_normal)
                warmup_offset = float(np.median([sample[1] for sample in warmup]))
                if (
                    angle_deg(normal, warmup_normal) > angle_gate_deg
                    or abs(offset - warmup_offset) > offset_gate_m
                ):
                    reset()
            warmup.append((normal, offset))
            if len(warmup) < min_support:
                return
            normals = np.asarray([sample[0] for sample in warmup])
            reference_normal = normals.mean(axis=0)
            reference_normal /= np.linalg.norm(reference_normal)
            reference_offset = float(np.median([sample[1] for sample in warmup]))
            target_correction = 0.0
            activations += 1
            support_used += len(warmup)
            warmup.clear()
            return

        if (
            angle_deg(normal, reference_normal) > angle_gate_deg
            or abs(offset - reference_offset) > offset_gate_m
        ):
            reset()
            warmup.append((normal, offset))
            return
        gravity_projection = float(reference_normal @ gravity_axis)
        target_correction = float(
            np.clip(
                gain * (offset - reference_offset) / gravity_projection,
                -max_correction_m,
                max_correction_m,
            )
        )
        support_used += 1

    for index in range(1, len(trajectory_times)):
        timestamp = float(trajectory_times[index])
        while event_index < len(eligible) and eligible[event_index].epoch_s <= timestamp:
            process(eligible[event_index])
            event_index += 1
        if (
            reference_normal is not None
            and last_observation_s is not None
            and timestamp - last_observation_s > max_gap_s
        ):
            reset()
        if reference_normal is not None:
            active_samples += 1
        dt = float(trajectory_times[index] - trajectory_times[index - 1])
        max_change = max_slew_mps * max(dt, 0.0)
        correction[index] = correction[index - 1] + float(
            np.clip(
                target_correction - correction[index - 1],
                -max_change,
                max_change,
            )
        )
    corrected = trajectory_positions + correction[:, None] * gravity_axis
    raw_vertical_span = float(np.ptp(trajectory_positions @ gravity_axis))
    corrected_vertical_span = float(np.ptp(corrected @ gravity_axis))
    if activations == 0:
        disabled["reason"] = "insufficient causal horizontal-plane support"
        disabled["support_observations"] = len(eligible)
        return trajectory_positions.copy(), np.zeros(len(trajectory_times)), disabled

    applied_max = float(np.max(np.abs(correction)))
    if applied_max <= 1e-12:
        disabled.update(
            {
                "reason": "plane support produced no nonzero correction",
                "support_observations": support_used,
                "activations": activations,
                "resets": resets,
                "active_trajectory_samples": 0,
                "correction_axis_world": gravity_axis.tolist(),
                "gain": gain,
                "max_correction_m": max_correction_m,
                "max_gap_s": max_gap_s,
                "min_support": min_support,
                "max_slew_mps": max_slew_mps,
                "max_horizontal_tilt_deg": max_horizontal_tilt_deg,
                "angle_gate_deg": angle_gate_deg,
                "offset_gate_m": offset_gate_m,
                "applied_correction_max_abs_m": 0.0,
                "applied_correction_rms_m": 0.0,
                "raw_gravity_axis_span_m": raw_vertical_span,
                "corrected_gravity_axis_span_m": raw_vertical_span,
                "gravity_axis_span_retention_ratio": 1.0,
            }
        )
        return trajectory_positions.copy(), np.zeros(len(trajectory_times)), disabled

    report = {
        "status": "ACTIVE",
        "reason": None,
        "support_observations": support_used,
        "activations": activations,
        "resets": resets,
        "active_trajectory_samples": active_samples,
        "active_fraction": float(active_samples / len(trajectory_times)),
        "last_reference_normal_world": (
            reference_normal.tolist() if reference_normal is not None else None
        ),
        "last_reference_offset_world_m": reference_offset,
        "correction_axis_world": gravity_axis.tolist(),
        "gain": gain,
        "max_correction_m": max_correction_m,
        "max_gap_s": max_gap_s,
        "min_support": min_support,
        "max_slew_mps": max_slew_mps,
        "max_horizontal_tilt_deg": max_horizontal_tilt_deg,
        "angle_gate_deg": angle_gate_deg,
        "offset_gate_m": offset_gate_m,
        "causal": True,
        "applied_correction_max_abs_m": applied_max,
        "applied_correction_rms_m": float(np.sqrt(np.mean(correction**2))),
        "raw_gravity_axis_span_m": raw_vertical_span,
        "corrected_gravity_axis_span_m": corrected_vertical_span,
        "gravity_axis_span_retention_ratio": (
            corrected_vertical_span / raw_vertical_span
            if raw_vertical_span > 1e-9
            else None
        ),
        "uses_absolute_height": False,
        "uses_endpoint_constraint": False,
    }
    return corrected, correction, report


def plane_factor_observation_metrics(
    observations: list[PlaneObservation],
    trajectory_times: np.ndarray,
    correction: np.ndarray,
    max_gap_s: float,
) -> dict[str, object]:
    """Measure plane-offset consistency before/after a candidate correction.

    This is an offline diagnostic only.  It uses observations already accepted
    by the world temporal gate and never feeds a result back into the factor.
    Segment-local metrics avoid treating a long loss of plane support as proof
    that a later horizontal plane is the same physical landmark.
    """
    if len(trajectory_times) != len(correction):
        raise ValueError("trajectory time/correction length mismatch")
    if len(trajectory_times) < 2 or np.any(np.diff(trajectory_times) <= 0.0):
        raise ValueError("trajectory timestamps must be strictly increasing")
    if max_gap_s <= 0.0:
        raise ValueError("max_gap_s must be positive")

    samples: list[tuple[float, float, float]] = []
    gravity_axis = np.array([0.0, 0.0, 1.0])
    for item in sorted(observations, key=lambda observation: observation.epoch_s):
        if (
            not item.temporal_gate_pass
            or not item.pose_matched
            or item.normal_world is None
            or item.offset_world_m is None
        ):
            continue
        normal = np.asarray(item.normal_world, dtype=float)
        normal_norm = float(np.linalg.norm(normal))
        if normal.shape != (3,) or normal_norm < 1e-8:
            continue
        normal /= normal_norm
        offset = float(item.offset_world_m)
        if normal @ gravity_axis < 0.0:
            normal = -normal
            offset = -offset
        applied = float(
            np.interp(item.epoch_s, trajectory_times, correction)
        )
        corrected_offset = offset - float(normal @ gravity_axis) * applied
        samples.append((float(item.epoch_s), offset, corrected_offset))

    if len(samples) < 2:
        return {
            "status": "INSUFFICIENT_SUPPORT",
            "accepted_observations": len(samples),
            "segments": 0,
            "segment_metrics": [],
            "offline_diagnostic_only": True,
        }

    segments: list[list[tuple[float, float, float]]] = []
    current: list[tuple[float, float, float]] = []
    for sample in samples:
        if current and sample[0] - current[-1][0] > max_gap_s:
            segments.append(current)
            current = []
        current.append(sample)
    if current:
        segments.append(current)

    def distribution(values: np.ndarray) -> tuple[float, float]:
        center = float(np.median(values))
        return (
            float(np.std(values)),
            float(np.quantile(np.abs(values - center), 0.95)),
        )

    raw_all = np.asarray([sample[1] for sample in samples], dtype=float)
    corrected_all = np.asarray([sample[2] for sample in samples], dtype=float)
    raw_std, raw_p95 = distribution(raw_all)
    corrected_std, corrected_p95 = distribution(corrected_all)
    segment_metrics = []
    improved_segments = 0
    for index, segment in enumerate(segments, start=1):
        raw = np.asarray([sample[1] for sample in segment], dtype=float)
        corrected = np.asarray([sample[2] for sample in segment], dtype=float)
        segment_raw_std, segment_raw_p95 = distribution(raw)
        segment_corrected_std, segment_corrected_p95 = distribution(corrected)
        improved = bool(
            segment_corrected_std < segment_raw_std
            and segment_corrected_p95 < segment_raw_p95
        )
        improved_segments += int(improved)
        segment_metrics.append(
            {
                "segment": index,
                "observations": len(segment),
                "start_epoch_s": segment[0][0],
                "end_epoch_s": segment[-1][0],
                "raw_offset_std_m": segment_raw_std,
                "corrected_offset_std_m": segment_corrected_std,
                "raw_p95_abs_error_m": segment_raw_p95,
                "corrected_p95_abs_error_m": segment_corrected_p95,
                "improved": improved,
            }
        )

    raw_within = np.concatenate(
        [
            np.asarray([sample[1] for sample in segment], dtype=float)
            - np.median([sample[1] for sample in segment])
            for segment in segments
        ]
    )
    corrected_within = np.concatenate(
        [
            np.asarray([sample[2] for sample in segment], dtype=float)
            - np.median([sample[2] for sample in segment])
            for segment in segments
        ]
    )
    raw_within_std, raw_within_p95 = distribution(raw_within)
    corrected_within_std, corrected_within_p95 = distribution(corrected_within)
    changed = bool(np.max(np.abs(corrected_all - raw_all)) > 1e-12)
    within_segment_improved = bool(
        corrected_within_std < raw_within_std
        and corrected_within_p95 < raw_within_p95
    )
    status = (
        "UNCHANGED"
        if not changed
        else "IMPROVED"
        if within_segment_improved
        else "NO_NET_IMPROVEMENT"
    )
    return {
        "status": status,
        "accepted_observations": len(samples),
        "segments": len(segments),
        "improved_segments": improved_segments,
        "raw_offset_std_m": raw_std,
        "corrected_offset_std_m": corrected_std,
        "raw_p95_abs_error_m": raw_p95,
        "corrected_p95_abs_error_m": corrected_p95,
        "within_segment_raw_std_m": raw_within_std,
        "within_segment_corrected_std_m": corrected_within_std,
        "within_segment_raw_p95_abs_error_m": raw_within_p95,
        "within_segment_corrected_p95_abs_error_m": corrected_within_p95,
        "decisive_metric_scope": "within_segment",
        "pooled_cross_segment_metrics_decisive": False,
        "segment_metrics": segment_metrics,
        "offline_diagnostic_only": True,
    }


def write_trajectory(
    path: Path,
    times: np.ndarray,
    positions: np.ndarray,
    quaternions: np.ndarray,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("t_sec", "x", "y", "z", "qw", "qx", "qy", "qz"))
        for timestamp, position, quaternion in zip(times, positions, quaternions):
            writer.writerow(
                (
                    timestamp,
                    *position,
                    quaternion[3],
                    quaternion[0],
                    quaternion[1],
                    quaternion[2],
                )
            )


def write_outputs(
    output_dir: Path,
    observations: list[PlaneObservation],
    report: dict[str, object],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "depth_plane_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    rows = [asdict(item) for item in observations]
    with (output_dir / "depth_plane_frames.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    observation_source = None
    if args.observations_csv is not None:
        observation_source = args.observations_csv.resolve()
        observations = load_observations_csv(observation_source)
        intrinsics = None
        depth_unit_m = None
        db3 = None
    else:
        observations, intrinsics, depth_unit_m, db3 = extract_observations(
            session=args.session.resolve(),
            sample_every=args.sample_every,
            max_frames=args.max_frames,
            grid_stride=args.grid_stride,
            min_depth_m=args.min_depth_m,
            max_depth_m=args.max_depth_m,
            ransac_threshold_m=args.ransac_threshold_m,
            min_inlier_ratio=args.min_inlier_ratio,
            min_inlier_points=args.min_inlier_points,
            max_p95_residual_m=args.max_p95_residual_m,
            roi_top_fraction=args.roi_top_fraction,
        )
    temporal_report = None
    plane_factor_report = None
    if args.trajectory is not None:
        match_trajectory(
            observations,
            args.trajectory.resolve(),
            args.config.resolve(),
        )
        temporal_report = apply_temporal_gate(
            observations,
            angle_gate_deg=args.world_angle_gate_deg,
            offset_gate_m=args.world_offset_gate_m,
            max_horizontal_tilt_deg=args.max_horizontal_tilt_deg,
        )
        trajectory_times, trajectory_positions, trajectory_quaternions = load_trajectory(
            args.trajectory.resolve()
        )
        corrected_positions, correction, plane_factor_report = plane_factor_correction(
            observations,
            trajectory_times,
            trajectory_positions,
            gain=args.plane_factor_gain,
            max_correction_m=args.plane_factor_max_correction_m,
            max_gap_s=args.plane_factor_max_gap_s,
            min_support=args.plane_factor_min_support,
            max_slew_mps=args.plane_factor_max_slew_mps,
            angle_gate_deg=args.world_angle_gate_deg,
            offset_gate_m=args.world_offset_gate_m,
            max_horizontal_tilt_deg=args.max_horizontal_tilt_deg,
        )
        plane_factor_report["observation_metrics"] = plane_factor_observation_metrics(
            observations,
            trajectory_times,
            correction,
            max_gap_s=args.plane_factor_max_gap_s,
        )
        if args.corrected_trajectory is not None:
            write_trajectory(
                args.corrected_trajectory.resolve(),
                trajectory_times,
                corrected_positions,
                trajectory_quaternions,
            )
    local_passes = sum(item.local_gate_pass for item in observations)
    report: dict[str, object] = {
        "result": "PROTOTYPE_ONLY",
        "session": str(args.session.resolve()) if args.session else None,
        "observations_csv": str(observation_source) if observation_source else None,
        "db3": str(db3) if db3 else None,
        "trajectory": str(args.trajectory.resolve()) if args.trajectory else None,
        "intrinsics": asdict(intrinsics) if intrinsics else None,
        "depth_unit_m": depth_unit_m,
        "sample_every": args.sample_every if observation_source is None else None,
        "roi_top_fraction": (
            args.roi_top_fraction if observation_source is None else None
        ),
        "observations": len(observations),
        "local_gate_passes": local_passes,
        "local_gate_fraction": local_passes / len(observations),
        "local_gate_thresholds": (
            {
                "min_inlier_ratio": args.min_inlier_ratio,
                "min_inlier_points": args.min_inlier_points,
                "max_p95_residual_m": args.max_p95_residual_m,
            }
            if observation_source is None
            else None
        ),
        "local_gate_labels_replayed": observation_source is not None,
        "world_gate_thresholds": {
            "max_horizontal_tilt_deg": args.max_horizontal_tilt_deg,
            "max_normal_cluster_error_deg": args.world_angle_gate_deg,
            "max_plane_offset_cluster_error_m": args.world_offset_gate_m,
        },
        "temporal_gate": temporal_report,
        "plane_factor": plane_factor_report,
        "constraint_policy": (
            "The plane factor considers only locally valid pose-matched planes, "
            "then applies its own causal past-only gravity and consistency gates. "
            "The temporal_gate field is an offline diagnostic and is not consumed "
            "by the factor. Camera height and trajectory endpoints are never assumed."
        ),
    }
    write_outputs(args.output_dir.resolve(), observations, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
