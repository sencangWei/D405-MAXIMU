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
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--trajectory", type=Path)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "/home/robot/ros2_ws/src/vins_fusion_ros2/config/"
            "d405_stereo_imu/d405_stereo_imu_config.yaml"
        ),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sample-every", type=int, default=15)
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--grid-stride", type=int, default=12)
    parser.add_argument("--min-depth-m", type=float, default=0.07)
    parser.add_argument("--max-depth-m", type=float, default=1.5)
    parser.add_argument("--ransac-threshold-m", type=float, default=0.006)
    parser.add_argument("--min-inlier-ratio", type=float, default=0.15)
    parser.add_argument("--min-inlier-points", type=int, default=220)
    parser.add_argument("--max-p95-residual-m", type=float, default=0.006)
    parser.add_argument("--world-angle-gate-deg", type=float, default=4.0)
    parser.add_argument("--world-offset-gate-m", type=float, default=0.025)
    parser.add_argument("--max-horizontal-tilt-deg", type=float, default=12.0)
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
) -> np.ndarray:
    margin_x = intrinsics.width // 10
    margin_y = intrinsics.height // 10
    ys = np.arange(margin_y, intrinsics.height - margin_y, stride)
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
    )
    temporal_report = None
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
    local_passes = sum(item.local_gate_pass for item in observations)
    report: dict[str, object] = {
        "result": "PROTOTYPE_ONLY",
        "session": str(args.session.resolve()),
        "db3": str(db3),
        "trajectory": str(args.trajectory.resolve()) if args.trajectory else None,
        "intrinsics": asdict(intrinsics),
        "depth_unit_m": depth_unit_m,
        "sample_every": args.sample_every,
        "observations": len(observations),
        "local_gate_passes": local_passes,
        "local_gate_fraction": local_passes / len(observations),
        "local_gate_thresholds": {
            "min_inlier_ratio": args.min_inlier_ratio,
            "min_inlier_points": args.min_inlier_points,
            "max_p95_residual_m": args.max_p95_residual_m,
        },
        "world_gate_thresholds": {
            "max_horizontal_tilt_deg": args.max_horizontal_tilt_deg,
            "max_normal_cluster_error_deg": args.world_angle_gate_deg,
            "max_plane_offset_cluster_error_m": args.world_offset_gate_m,
        },
        "temporal_gate": temporal_report,
        "constraint_policy": (
            "Only observations passing both the local depth-plane gate and the "
            "world-plane temporal gate may create a soft plane residual. Camera "
            "height is never assumed constant."
        ),
    }
    write_outputs(args.output_dir.resolve(), observations, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
