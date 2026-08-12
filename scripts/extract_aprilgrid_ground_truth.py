#!/usr/bin/env python3
"""Extract timestamped camera poses from a fixed AprilGrid calibration session."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

from collect_calib_data import load_aprilgrid_config
from convert_to_kalibr_bag import read_camera_ts
from ego_vio.imu.imu_reader import fit_counter_timestamps


ROOT = Path(__file__).resolve().parents[1]


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
    args = parser.parse_args()

    from aprilgrid import Detector

    unit = args.session.resolve() / "left_hand"
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
    camera_rows = read_camera_ts(unit / "camera_ts.csv")
    fitted_times, time_fit = fit_counter_timestamps(
        [row[2] for row in camera_rows], [row[1] for row in camera_rows]
    )
    output_rows: list[list[float]] = []
    reprojection_rmse: list[float] = []

    for row, fitted_time in zip(camera_rows, fitted_times):
        index = row[0]
        image = cv2.imread(
            str(unit / "frames" / f"{index:06d}.jpg"), cv2.IMREAD_GRAYSCALE
        )
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
    print(
        f"AprilGrid GT: {len(output_rows)}/{len(camera_rows)} poses, "
        f"reprojection RMSE median={np.median(reprojection_rmse):.3f}px, "
        f"p95={np.percentile(reprojection_rmse, 95):.3f}px, "
        f"camera time fit={time_fit['rate_hz']:.3f}fps/"
        f"{time_fit['sigma_ms']:.3f}ms"
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
