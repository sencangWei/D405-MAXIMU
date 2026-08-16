#!/usr/bin/env python3
"""Extract timestamped camera poses from a fixed AprilGrid calibration session."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

from collect_calib_data import load_aprilgrid_config
from convert_to_kalibr_bag import read_camera_ts
from ego_vio.imu.imu_reader import fit_counter_timestamps
from replay_db3_to_ros2 import META_TS_RE, STREAM_TOPICS, select_db3


ROOT = Path(__file__).resolve().parents[1]


def session_format(session: Path) -> str:
    if (session / "left_hand" / "camera_ts.csv").is_file():
        return "legacy_frames"
    if any(path.stat().st_size > 0 for path in session.glob("*.db3")):
        return "rsusb_db3"
    raise FileNotFoundError(f"无法识别AprilGrid会话格式: {session}")


def ros_image_to_gray(message) -> np.ndarray:
    if message.encoding.lower() not in {"mono8", "8uc1", "y8"}:
        raise ValueError(f"AprilGrid左IR要求mono8，实际为{message.encoding}")
    if message.step < message.width:
        raise ValueError(
            f"图像step小于width: step={message.step}, width={message.width}"
        )
    raw = np.frombuffer(message.data, dtype=np.uint8)
    expected = message.height * message.step
    if raw.size < expected:
        raise ValueError(f"图像数据不足: {raw.size} < {expected}")
    return raw[:expected].reshape(message.height, message.step)[:, : message.width]


def rsusb_left_image_iter(db3: Path) -> Iterator[tuple[float, np.ndarray]]:
    import rosbag2_py
    from rclpy.serialization import deserialize_message
    from sensor_msgs.msg import Image as RosImage
    from std_msgs.msg import String

    data_topic = STREAM_TOPICS["ir_left"]
    metadata_topic = STREAM_TOPICS["ir_left_meta"]
    reader = rosbag2_py.SequentialReader()
    reader.open(
        rosbag2_py.StorageOptions(uri=str(db3), storage_id="sqlite3"),
        rosbag2_py.ConverterOptions("", ""),
    )
    reader.set_filter(
        rosbag2_py.StorageFilter(topics=[data_topic, metadata_topic])
    )
    pending_image = None
    pending_timestamp = None
    while reader.has_next():
        topic, data, _ = reader.read_next()
        if topic == data_topic:
            pending_image = deserialize_message(data, RosImage)
        elif topic == metadata_topic:
            match = META_TS_RE.search(deserialize_message(data, String).data)
            if match:
                pending_timestamp = float(match.group(1)) / 1000.0
        if pending_image is not None and pending_timestamp is not None:
            yield pending_timestamp, ros_image_to_gray(pending_image)
            pending_image = None
            pending_timestamp = None


def object_corners(tag_id: int, grid: dict) -> np.ndarray:
    row, column = divmod(tag_id, grid["tagCols"])
    pitch = grid["tagSize"] * (1.0 + grid["tagSpacing"])
    x0, y0 = column * pitch, row * pitch
    size = grid["tagSize"]
    return np.array(
        [
            [x0, y0, 0.0],
            [x0 + size, y0, 0.0],
            [x0 + size, y0 + size, 0.0],
            [x0, y0 + size, 0.0],
        ],
        dtype=np.float32,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--aprilgrid",
        type=Path,
        default=ROOT / "config/aprilgrid_6x6_35mm.yaml",
    )
    parser.add_argument(
        "--camera-yaml",
        type=Path,
        default=Path(
            "/home/robot/ros2_ws/src/vins_fusion_ros2/config/"
            "d405_stereo_imu/left.yaml"
        ),
    )
    parser.add_argument("--min-tags", type=int, default=4)
    parser.add_argument("--max-reprojection-rmse-px", type=float, default=1.5)
    parser.add_argument(
        "--image-db3",
        type=Path,
        help="RSUSB会话可选轻量双IR DB3；未指定时读取会话内最大非空DB3",
    )
    args = parser.parse_args()

    from aprilgrid import Detector

    session = args.session.resolve()
    source_format = session_format(session)
    grid = load_aprilgrid_config(args.aprilgrid)
    camera = cv2.FileStorage(str(args.camera_yaml), cv2.FileStorage_READ)
    projection = camera.getNode("projection_parameters")
    intrinsic = np.array(
        [
            [projection.getNode("fx").real(), 0.0, projection.getNode("cx").real()],
            [0.0, projection.getNode("fy").real(), projection.getNode("cy").real()],
            [0.0, 0.0, 1.0],
        ]
    )
    camera.release()
    detector = Detector("t36h11")
    if source_format == "legacy_frames":
        unit = session / "left_hand"
        camera_rows = read_camera_ts(unit / "camera_ts.csv")
        fitted_times, time_fit = fit_counter_timestamps(
            [row[2] for row in camera_rows], [row[1] for row in camera_rows]
        )
        samples = (
            (
                float(fitted_time),
                cv2.imread(
                    str(unit / "frames" / f"{row[0]:06d}.jpg"),
                    cv2.IMREAD_GRAYSCALE,
                ),
            )
            for row, fitted_time in zip(camera_rows, fitted_times)
        )
    else:
        source_db3 = (
            args.image_db3.resolve() if args.image_db3 else select_db3(session)
        )
        samples = rsusb_left_image_iter(source_db3)
        time_fit = None
    output_rows: list[list[float]] = []
    reprojection_rmse: list[float] = []
    input_times: list[float] = []

    for fitted_time, image in samples:
        input_times.append(float(fitted_time))
        if image is None:
            continue
        object_points: list[np.ndarray] = []
        image_points: list[np.ndarray] = []
        for detection in detector.detect(image):
            tag_id = int(detection.tag_id)
            if tag_id < 0 or tag_id >= grid["tagCols"] * grid["tagRows"]:
                continue
            corners = np.asarray(detection.corners, dtype=np.float32).reshape(-1, 2)
            if corners.shape != (4, 2):
                continue
            object_points.append(object_corners(tag_id, grid))
            image_points.append(corners)
        if len(object_points) < args.min_tags:
            continue
        object_array = np.vstack(object_points)
        image_array = np.vstack(image_points)
        ok, rvec, tvec = cv2.solvePnP(
            object_array,
            image_array,
            intrinsic,
            np.zeros(5),
            flags=cv2.SOLVEPNP_ITERATIVE,
        )
        if not ok:
            continue
        projected, _ = cv2.projectPoints(
            object_array, rvec, tvec, intrinsic, np.zeros(5)
        )
        rmse = float(
            np.sqrt(np.mean(np.sum((projected.reshape(-1, 2) - image_array) ** 2, axis=1)))
        )
        if rmse > args.max_reprojection_rmse_px:
            continue
        board_to_camera, _ = cv2.Rodrigues(rvec)
        camera_to_board = board_to_camera.T
        position = (-camera_to_board @ tvec).reshape(3)
        quaternion = Rotation.from_matrix(camera_to_board).as_quat()
        output_rows.append(
            [
                float(fitted_time),
                *position,
                quaternion[3],
                quaternion[0],
                quaternion[1],
                quaternion[2],
            ]
        )
        reprojection_rmse.append(rmse)

    if len(output_rows) < 20:
        raise RuntimeError(f"only {len(output_rows)} valid AprilGrid poses")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["t_sec", "x", "y", "z", "qw", "qx", "qy", "qz"])
        writer.writerows(output_rows)
    if time_fit is None:
        intervals = np.diff(input_times)
        positive_intervals = intervals[intervals > 0]
        if len(positive_intervals) == 0:
            raise RuntimeError("RSUSB左IR时间戳不足或不递增")
        time_fit = {
            "rate_hz": float(1.0 / np.median(positive_intervals)),
            "sigma_ms": float(np.std(positive_intervals) * 1000.0),
        }
    print(
        f"AprilGrid GT ({source_format}): {len(output_rows)}/{len(input_times)} poses, "
        f"reprojection RMSE median={np.median(reprojection_rmse):.3f}px, "
        f"p95={np.percentile(reprojection_rmse, 95):.3f}px, "
        f"camera time fit={time_fit['rate_hz']:.3f}fps/"
        f"{time_fit['sigma_ms']:.3f}ms"
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
