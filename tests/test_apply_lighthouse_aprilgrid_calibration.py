import csv
import importlib.util
import json
from pathlib import Path
import sys

import cv2
import numpy as np
from scipy.spatial.transform import Rotation


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/apply_lighthouse_aprilgrid_calibration.py"
)
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location(
    "apply_lighthouse_aprilgrid_calibration", SCRIPT
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def pose_matrix(position, euler_deg):
    pose = np.eye(4)
    pose[:3, :3] = Rotation.from_euler("xyz", euler_deg, degrees=True).as_matrix()
    pose[:3, 3] = position
    return pose


def write_tracker(path: Path, times: np.ndarray, poses: np.ndarray, offset: float):
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "host_monotonic_ns",
                "host_realtime_ns",
                "device_time_s",
                "name",
                "serial",
                "px_m",
                "py_m",
                "pz_m",
                "qw",
                "qx",
                "qy",
                "qz",
            ]
        )
        for timestamp, pose in zip(times, poses):
            quaternion = Rotation.from_matrix(pose[:3, :3]).as_quat()
            writer.writerow(
                [
                    int((timestamp + offset) * 1e9),
                    int((timestamp + offset + 1_788_000_000.0) * 1e9),
                    timestamp + offset,
                    "WM0",
                    "LHR-TEST",
                    *pose[:3, 3],
                    quaternion[3],
                    *quaternion[:3],
                ]
            )


def test_apply_uses_only_query_timestamps_and_converts_camera_to_body(
    tmp_path: Path,
) -> None:
    query_relative = np.arange(1.0, 3.0, 1.0 / 30.0)
    tracker_times = np.arange(0.0, 4.0, 1.0 / 132.0)
    tracker_poses = np.tile(np.eye(4), (len(tracker_times), 1, 1))
    tracker_poses[:, 0, 3] = 0.1 * tracker_times
    tracker_path = tmp_path / "tracker.csv"
    write_tracker(tracker_path, tracker_times, tracker_poses, offset=0.0)

    epoch = 1_788_000_000.0
    query_path = tmp_path / "estimate.csv"
    with query_path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["t_sec", "x", "y", "z"])
        for timestamp in query_relative:
            writer.writerow([epoch + timestamp, 999, 999, 999])
    frames_path = tmp_path / "d405_frames.csv"
    with frames_path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["sensor_event_wall", "sensor_event_mono"])
        for timestamp in query_relative:
            writer.writerow([epoch + timestamp, timestamp])

    tracker_T_camera = pose_matrix([0.03, -0.02, 0.04], [5, -3, 7])
    calibration_path = tmp_path / "calibration.json"
    calibration_path.write_text(
        json.dumps(
            {
                "schema": "lighthouse_d405_aprilgrid_joint_handeye_v1",
                "result": "PASS_CANDIDATE",
                "slam_supervision": False,
                "slam_inputs": [],
                "tracker_serial": "LHR-TEST",
                "tracker_time_source": "host_monotonic",
                "tracker_query_offset_ms": 0.0,
                "tracker_T_d405_left_camera": tracker_T_camera.tolist(),
            }
        )
    )
    body_T_camera = pose_matrix([-0.01, 0.02, -0.03], [2, 1, -4])
    body_camera_path = tmp_path / "vins.yaml"
    storage = cv2.FileStorage(str(body_camera_path), cv2.FileStorage_WRITE)
    storage.write("body_T_cam0", body_T_camera)
    storage.release()
    output = tmp_path / "ground_truth.csv"

    report = MODULE.write_ground_truth(
        query_path,
        tracker_path,
        calibration_path,
        frames_path,
        body_camera_path,
        output,
        target="body",
        max_gap_s=0.03,
    )

    expected = tracker_T_camera @ np.linalg.inv(body_T_camera)
    assert report["result"] == "PASS"
    assert report["slam_supervision"] is False
    assert report["query_columns_used"] == ["t_sec"]
    assert report["pose_columns_from_query_used"] == []
    assert report["tracker_query_offset_ms"] == 0.0
    assert report["offset_semantics"] == (
        "interpolate Tracker at mapped camera timestamp + tracker_query_offset_ms"
    )
    assert np.allclose(report["tracker_T_target"], expected)
    rows = list(csv.DictReader(output.open()))
    assert len(rows) == len(query_relative)
    assert float(rows[0]["x"]) != 999.0


def test_apply_prefers_canonical_tracker_to_body_transform(tmp_path: Path) -> None:
    query_relative = np.arange(1.0, 2.0, 1.0 / 30.0)
    tracker_times = np.arange(0.0, 3.0, 1.0 / 132.0)
    tracker_poses = np.tile(np.eye(4), (len(tracker_times), 1, 1))
    tracker_path = tmp_path / "tracker.csv"
    write_tracker(tracker_path, tracker_times, tracker_poses, offset=0.0)

    epoch = 1_788_000_000.0
    query_path = tmp_path / "estimate.csv"
    with query_path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["t_sec"])
        for timestamp in query_relative:
            writer.writerow([epoch + timestamp])
    frames_path = tmp_path / "d405_frames.csv"
    with frames_path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["sensor_event_wall", "sensor_event_mono"])
        for timestamp in query_relative:
            writer.writerow([epoch + timestamp, timestamp])

    tracker_T_body = pose_matrix([0.009, -0.002, 0.033], [3, -2, 5])
    calibration_path = tmp_path / "calibration.json"
    calibration_path.write_text(
        json.dumps(
            {
                "schema": "lighthouse_d405_aprilgrid_handeye_v1",
                "result": "PASS_CANDIDATE",
                "slam_supervision": False,
                "slam_inputs": [],
                "tracker_serial": "LHR-TEST",
                "tracker_time_source": "host_monotonic",
                "tracker_query_offset_ms": 0.0,
                "tracker_T_body": tracker_T_body.tolist(),
                "tracker_T_d405_left_camera": np.eye(4).tolist(),
            }
        )
    )
    body_camera_path = tmp_path / "vins.yaml"
    storage = cv2.FileStorage(str(body_camera_path), cv2.FileStorage_WRITE)
    storage.write("body_T_cam0", pose_matrix([1, 2, 3], [10, 20, 30]))
    storage.release()

    report = MODULE.write_ground_truth(
        query_path,
        tracker_path,
        calibration_path,
        frames_path,
        body_camera_path,
        tmp_path / "ground_truth.csv",
        target="body",
        max_gap_s=0.03,
    )

    assert report["target_transform_source"] == "tracker_T_body"
    assert np.allclose(report["tracker_T_target"], tracker_T_body)
