#!/usr/bin/env python3
"""Generate Lighthouse camera/body ground truth at requested timestamps."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

from calibrate_lighthouse_aprilgrid import map_camera_times
from calibrate_lighthouse_umi import interpolate_tracker, load_tracker


def load_query_times(path: Path) -> np.ndarray:
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    if not rows or "t_sec" not in rows[0]:
        raise ValueError(f"query timestamp CSV must contain t_sec: {path}")
    times = np.asarray([float(row["t_sec"]) for row in rows], dtype=float)
    if not np.all(np.isfinite(times)) or np.any(np.diff(times) <= 0.0):
        raise ValueError(f"query timestamps must be finite and increasing: {path}")
    return times


def load_body_T_cam0(path: Path) -> np.ndarray:
    calibration = cv2.FileStorage(str(path), cv2.FileStorage_READ)
    if not calibration.isOpened():
        raise ValueError(f"cannot open body-camera calibration: {path}")
    body_T_camera = calibration.getNode("body_T_cam0").mat()
    calibration.release()
    if body_T_camera is None or body_T_camera.shape != (4, 4):
        raise ValueError(f"body_T_cam0 missing or invalid in {path}")
    return np.asarray(body_T_camera, dtype=float)


def write_ground_truth(
    query_times_path: Path,
    tracker_path: Path,
    calibration_path: Path,
    d405_frames_path: Path,
    body_camera_config: Path,
    output: Path,
    target: str,
    max_gap_s: float,
) -> dict[str, object]:
    calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
    if calibration.get("schema") not in {
        "lighthouse_d405_aprilgrid_handeye_v1",
        "lighthouse_d405_aprilgrid_joint_handeye_v1",
    }:
        raise ValueError("not an independent AprilGrid Lighthouse calibration")
    if calibration.get("result") != "PASS_CANDIDATE":
        raise ValueError("refusing to apply a calibration that did not pass")
    if calibration.get("slam_supervision") is not False or calibration.get(
        "slam_inputs"
    ) != []:
        raise ValueError("calibration independence provenance is invalid")

    query_times = load_query_times(query_times_path)
    aligned_times, clock_mapping = map_camera_times(
        query_times, calibration["tracker_time_source"], d405_frames_path
    )
    tracker_times, tracker_positions, tracker_quaternions, serial = load_tracker(
        tracker_path, calibration["tracker_time_source"]
    )
    if serial != calibration["tracker_serial"]:
        raise ValueError(
            f"tracker serial mismatch: {serial} != {calibration['tracker_serial']}"
        )
    offset_s = float(calibration["tracker_query_offset_ms"]) / 1000.0
    tracker_poses, valid = interpolate_tracker(
        aligned_times + offset_s,
        tracker_times,
        tracker_positions,
        tracker_quaternions,
        max_gap_s,
    )
    if target == "camera":
        if "tracker_T_d405_left_camera" in calibration:
            tracker_T_target = np.asarray(
                calibration["tracker_T_d405_left_camera"], dtype=float
            )
            target_transform_source = "tracker_T_d405_left_camera"
        elif "tracker_T_body" in calibration:
            body_T_camera = load_body_T_cam0(body_camera_config)
            tracker_T_target = (
                np.asarray(calibration["tracker_T_body"], dtype=float)
                @ body_T_camera
            )
            target_transform_source = "tracker_T_body * body_T_cam0"
        else:
            raise ValueError("calibration has neither camera nor body transform")
    elif target == "body":
        if "tracker_T_body" in calibration:
            tracker_T_target = np.asarray(
                calibration["tracker_T_body"], dtype=float
            )
            target_transform_source = "tracker_T_body"
        elif "tracker_T_d405_left_camera" in calibration:
            body_T_camera = load_body_T_cam0(body_camera_config)
            tracker_T_target = (
                np.asarray(
                    calibration["tracker_T_d405_left_camera"], dtype=float
                )
                @ np.linalg.inv(body_T_camera)
            )
            target_transform_source = (
                "tracker_T_d405_left_camera * inverse(body_T_cam0)"
            )
        else:
            raise ValueError("calibration has neither camera nor body transform")
    else:
        raise ValueError(f"unsupported target: {target}")
    if tracker_T_target.shape != (4, 4):
        raise ValueError(f"{target_transform_source} must produce a 4x4 transform")

    ground_truth_poses = tracker_poses @ tracker_T_target
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["t_sec", "x", "y", "z", "qw", "qx", "qy", "qz"])
        for timestamp, pose in zip(query_times[valid], ground_truth_poses):
            quaternion = Rotation.from_matrix(pose[:3, :3]).as_quat()
            writer.writerow(
                [
                    f"{timestamp:.9f}",
                    *[f"{value:.9f}" for value in pose[:3, 3]],
                    f"{quaternion[3]:.9f}",
                    f"{quaternion[0]:.9f}",
                    f"{quaternion[1]:.9f}",
                    f"{quaternion[2]:.9f}",
                ]
            )
    return {
        "schema": "lighthouse_aprilgrid_ground_truth_provenance_v1",
        "result": "PASS",
        "target": target,
        "target_transform_source": target_transform_source,
        "samples_written": int(valid.sum()),
        "query_samples": len(query_times),
        "timestamp_overlap_ratio": float(valid.mean()),
        "query_columns_used": ["t_sec"],
        "pose_columns_from_query_used": [],
        "slam_supervision": False,
        "clock_mapping": clock_mapping,
        "tracker_query_offset_ms": float(calibration["tracker_query_offset_ms"]),
        "offset_semantics": (
            "interpolate Tracker at mapped camera timestamp + "
            "tracker_query_offset_ms"
        ),
        "tracker_T_target": tracker_T_target.tolist(),
        "inputs": {
            "query_timestamps": str(query_times_path.resolve()),
            "tracker": str(tracker_path.resolve()),
            "calibration": str(calibration_path.resolve()),
            "body_camera_config": str(body_camera_config.resolve()),
            "d405_frames": str(d405_frames_path.resolve()),
        },
        "sha256": {
            "calibration": hashlib.sha256(calibration_path.read_bytes()).hexdigest(),
            "body_camera_config": hashlib.sha256(
                body_camera_config.read_bytes()
            ).hexdigest(),
        },
        "output": str(output.resolve()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query-times", type=Path, required=True)
    parser.add_argument("--tracker", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--d405-frames", type=Path, required=True)
    parser.add_argument("--body-camera-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--target", choices=("body", "camera"), default="body")
    parser.add_argument("--max-gap-ms", type=float, default=30.0)
    args = parser.parse_args()

    report = write_ground_truth(
        args.query_times,
        args.tracker,
        args.calibration,
        args.d405_frames,
        args.body_camera_config,
        args.output,
        args.target,
        args.max_gap_ms / 1000.0,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
