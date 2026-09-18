#!/usr/bin/env python3
"""Convert a world-to-camera pose trajectory to the calibrated IMU/body origin."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial.transform import Rotation


FIELDS = ("t_sec", "x", "y", "z", "qw", "qx", "qy", "qz")


def load_trajectory(path: Path) -> tuple[np.ndarray, np.ndarray, Rotation]:
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    if not rows or not set(FIELDS).issubset(rows[0]):
        raise ValueError(f"invalid trajectory schema: {path}")
    times = np.asarray([float(row["t_sec"]) for row in rows])
    positions = np.asarray(
        [[float(row[key]) for key in ("x", "y", "z")] for row in rows]
    )
    quaternions = np.asarray(
        [
            [float(row[key]) for key in ("qx", "qy", "qz", "qw")]
            for row in rows
        ]
    )
    if np.any(np.diff(times) <= 0.0):
        raise ValueError("trajectory timestamps must be strictly increasing")
    if not np.all(np.isfinite(positions)) or not np.all(np.isfinite(quaternions)):
        raise ValueError("trajectory contains non-finite poses")
    return times, positions, Rotation.from_quat(quaternions)


def load_body_t_camera(path: Path, key: str) -> np.ndarray:
    storage = cv2.FileStorage(str(path), cv2.FileStorage_READ)
    matrix = storage.getNode(key).mat()
    storage.release()
    if matrix is None or matrix.shape != (4, 4):
        raise ValueError(f"missing 4x4 {key} in {path}")
    if not np.all(np.isfinite(matrix)):
        raise ValueError(f"non-finite {key} in {path}")
    return matrix


def camera_to_body(
    positions: np.ndarray,
    rotations: Rotation,
    body_t_camera: np.ndarray,
) -> tuple[np.ndarray, Rotation]:
    body_from_camera = Rotation.from_matrix(body_t_camera[:3, :3])
    body_rotations = rotations * body_from_camera.inv()
    body_positions = positions - body_rotations.apply(body_t_camera[:3, 3])
    return body_positions, body_rotations


def write_trajectory(
    path: Path,
    times: np.ndarray,
    positions: np.ndarray,
    rotations: Rotation,
) -> None:
    quaternions = rotations.as_quat()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(FIELDS)
        for time_s, position, quaternion in zip(times, positions, quaternions):
            writer.writerow(
                [
                    f"{time_s:.9f}",
                    *(f"{value:.9f}" for value in position),
                    f"{quaternion[3]:.9f}",
                    *(f"{value:.9f}" for value in quaternion[:3]),
                ]
            )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--body-t-camera-yaml", type=Path, required=True)
    parser.add_argument("--body-t-camera-key", default="body_T_cam0")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    times, positions, rotations = load_trajectory(args.input)
    body_t_camera = load_body_t_camera(
        args.body_t_camera_yaml, args.body_t_camera_key
    )
    body_positions, body_rotations = camera_to_body(
        positions, rotations, body_t_camera
    )
    write_trajectory(args.output, times, body_positions, body_rotations)

    report = {
        "schema": "umi_camera_trajectory_to_body_v1",
        "result": "PASS",
        "slam_supervision": False,
        "external_ground_truth_used": False,
        "input_frame": "left_infrared_camera_optical_origin",
        "output_frame": "body_imu_origin",
        "samples": int(len(times)),
        "input": str(args.input.resolve()),
        "output": str(args.output.resolve()),
        "body_t_camera_yaml": str(args.body_t_camera_yaml.resolve()),
        "body_t_camera_yaml_sha256": sha256(args.body_t_camera_yaml),
        "body_t_camera_key": args.body_t_camera_key,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
