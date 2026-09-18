#!/usr/bin/env python3
"""Extract timestamped camera poses from a fixed AprilGrid calibration session."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np
from scipy.optimize import least_squares
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


def rsusb_stereo_image_iter(
    db3: Path, max_pair_delta_s: float = 0.002
) -> Iterator[tuple[float, np.ndarray, np.ndarray]]:
    from replay_db3_to_ros2 import bag_event_iter

    pending: dict[str, tuple[float, np.ndarray]] = {}
    for timestamp, stream, message in bag_event_iter(db3, "stereo"):
        pending[stream] = (timestamp, ros_image_to_gray(message))
        if "ir_left" not in pending or "ir_right" not in pending:
            continue
        left_time, left_image = pending["ir_left"]
        right_time, right_image = pending["ir_right"]
        delta = left_time - right_time
        if abs(delta) <= max_pair_delta_s:
            yield (left_time + right_time) / 2.0, left_image, right_image
            pending.clear()
        elif delta < 0.0:
            pending.pop("ir_left")
        else:
            pending.pop("ir_right")


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


def load_camera_yaml(path: Path) -> tuple[np.ndarray, np.ndarray]:
    camera = cv2.FileStorage(str(path), cv2.FileStorage_READ)
    if not camera.isOpened():
        raise ValueError(f"cannot open camera calibration: {path}")
    projection = camera.getNode("projection_parameters")
    distortion = camera.getNode("distortion_parameters")
    intrinsic = np.array(
        [
            [projection.getNode("fx").real(), 0.0, projection.getNode("cx").real()],
            [0.0, projection.getNode("fy").real(), projection.getNode("cy").real()],
            [0.0, 0.0, 1.0],
        ]
    )
    coefficients = np.asarray(
        [distortion.getNode(name).real() for name in ("k1", "k2", "p1", "p2")]
    )
    camera.release()
    return intrinsic, coefficients


def load_cam1_T_cam0(path: Path) -> np.ndarray:
    calibration = cv2.FileStorage(str(path), cv2.FileStorage_READ)
    if not calibration.isOpened():
        raise ValueError(f"cannot open stereo calibration: {path}")
    body_T_cam0 = calibration.getNode("body_T_cam0").mat()
    body_T_cam1 = calibration.getNode("body_T_cam1").mat()
    calibration.release()
    if body_T_cam0 is None or body_T_cam1 is None:
        raise ValueError(f"body_T_cam0/body_T_cam1 missing from {path}")
    return np.linalg.inv(body_T_cam1) @ body_T_cam0


def detected_points(detector, image: np.ndarray, grid: dict):
    object_points: list[np.ndarray] = []
    image_points: list[np.ndarray] = []
    tag_count = 0
    for detection in detector.detect(image):
        tag_id = int(detection.tag_id)
        if tag_id < 0 or tag_id >= grid["tagCols"] * grid["tagRows"]:
            continue
        corners = np.asarray(detection.corners, dtype=np.float32).reshape(-1, 2)
        if corners.shape != (4, 2):
            continue
        object_points.append(object_corners(tag_id, grid))
        image_points.append(corners)
        tag_count += 1
    if not object_points:
        return np.empty((0, 3)), np.empty((0, 2)), 0
    return np.vstack(object_points), np.vstack(image_points), tag_count


def stereo_pose(
    left_object: np.ndarray,
    left_image: np.ndarray,
    right_object: np.ndarray,
    right_image: np.ndarray,
    left_intrinsic: np.ndarray,
    left_distortion: np.ndarray,
    right_intrinsic: np.ndarray,
    right_distortion: np.ndarray,
    cam1_T_cam0: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, float]:
    ok, rvec, tvec = cv2.solvePnP(
        left_object,
        left_image,
        left_intrinsic,
        left_distortion,
        flags=cv2.SOLVEPNP_ITERATIVE,
    )
    if not ok:
        raise ValueError("left-camera AprilGrid PnP failed")
    initial = np.r_[rvec.ravel(), tvec.ravel()]
    cam1_R_cam0 = cam1_T_cam0[:3, :3]
    cam1_t_cam0 = cam1_T_cam0[:3, 3]

    def residual(parameters: np.ndarray) -> np.ndarray:
        cam0_R_grid = Rotation.from_rotvec(parameters[:3]).as_matrix()
        cam0_t_grid = parameters[3:]
        left_projected, _ = cv2.projectPoints(
            left_object,
            parameters[:3],
            cam0_t_grid,
            left_intrinsic,
            left_distortion,
        )
        cam1_R_grid = cam1_R_cam0 @ cam0_R_grid
        cam1_t_grid = cam1_R_cam0 @ cam0_t_grid + cam1_t_cam0
        right_projected, _ = cv2.projectPoints(
            right_object,
            Rotation.from_matrix(cam1_R_grid).as_rotvec(),
            cam1_t_grid,
            right_intrinsic,
            right_distortion,
        )
        return np.concatenate(
            (
                (left_projected.reshape(-1, 2) - left_image).ravel(),
                (right_projected.reshape(-1, 2) - right_image).ravel(),
            )
        )

    fitted = least_squares(
        residual,
        initial,
        loss="huber",
        f_scale=1.0,
        max_nfev=100,
    )
    rmse = float(np.sqrt(np.mean(residual(fitted.x) ** 2)))
    return fitted.x[:3], fitted.x[3:], rmse


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
    parser.add_argument("--right-camera-yaml", type=Path)
    parser.add_argument(
        "--stereo-config",
        type=Path,
        help="包含body_T_cam0/body_T_cam1的VINS配置；与右相机内参一起启用双目联合PnP",
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
    intrinsic, distortion = load_camera_yaml(args.camera_yaml)
    stereo_enabled = args.right_camera_yaml is not None or args.stereo_config is not None
    if stereo_enabled and (
        args.right_camera_yaml is None or args.stereo_config is None
    ):
        raise ValueError("--right-camera-yaml and --stereo-config must be supplied together")
    if stereo_enabled:
        right_intrinsic, right_distortion = load_camera_yaml(args.right_camera_yaml)
        cam1_T_cam0 = load_cam1_T_cam0(args.stereo_config)
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
        samples = (
            rsusb_stereo_image_iter(source_db3)
            if stereo_enabled
            else rsusb_left_image_iter(source_db3)
        )
        time_fit = None
    output_rows: list[list[float]] = []
    reprojection_rmse: list[float] = []
    input_times: list[float] = []

    for sample in samples:
        fitted_time, image = sample[:2]
        input_times.append(float(fitted_time))
        if image is None:
            continue
        object_array, image_array, tag_count = detected_points(detector, image, grid)
        if tag_count < args.min_tags:
            continue
        if stereo_enabled:
            right_image = sample[2]
            right_object, right_points, right_tag_count = detected_points(
                detector, right_image, grid
            )
            if right_tag_count < args.min_tags:
                continue
            try:
                rvec, tvec, rmse = stereo_pose(
                    object_array,
                    image_array,
                    right_object,
                    right_points,
                    intrinsic,
                    distortion,
                    right_intrinsic,
                    right_distortion,
                    cam1_T_cam0,
                )
            except ValueError:
                continue
        else:
            ok, rvec, tvec = cv2.solvePnP(
                object_array,
                image_array,
                intrinsic,
                distortion,
                flags=cv2.SOLVEPNP_ITERATIVE,
            )
            if not ok:
                continue
            projected, _ = cv2.projectPoints(
                object_array, rvec, tvec, intrinsic, distortion
            )
            rmse = float(
                np.sqrt(
                    np.mean(
                        np.sum(
                            (projected.reshape(-1, 2) - image_array) ** 2, axis=1
                        )
                    )
                )
            )
        if rmse > args.max_reprojection_rmse_px:
            continue
        board_to_camera = Rotation.from_rotvec(np.asarray(rvec).ravel()).as_matrix()
        camera_to_board = board_to_camera.T
        position = (-camera_to_board @ np.asarray(tvec).reshape(3)).reshape(3)
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
        f"AprilGrid GT ({source_format}, {'stereo' if stereo_enabled else 'mono'}): "
        f"{len(output_rows)}/{len(input_times)} poses, "
        f"reprojection RMSE median={np.median(reprojection_rmse):.3f}px, "
        f"p95={np.percentile(reprojection_rmse, 95):.3f}px, "
        f"camera time fit={time_fit['rate_hz']:.3f}fps/"
        f"{time_fit['sigma_ms']:.3f}ms"
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
