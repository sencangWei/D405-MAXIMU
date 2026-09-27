import csv
import cv2
import importlib.util
from pathlib import Path
import sys

import numpy as np
from scipy.spatial.transform import Rotation


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/calibrate_lighthouse_aprilgrid.py"
)
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location(
    "calibrate_lighthouse_aprilgrid", SCRIPT
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def trajectory(times: np.ndarray) -> np.ndarray:
    poses = np.tile(np.eye(4), (len(times), 1, 1))
    poses[:, :3, 3] = np.column_stack(
        (
            0.25 * np.sin(0.7 * times) + 0.04 * np.sin(2.3 * times),
            0.18 * np.cos(0.5 * times) + 0.03 * np.sin(1.7 * times),
            0.10 * np.sin(0.9 * times) + 0.02 * np.cos(2.1 * times),
        )
    )
    poses[:, :3, :3] = Rotation.from_euler(
        "xyz",
        np.column_stack(
            (
                0.45 * np.sin(0.6 * times),
                0.38 * np.cos(0.8 * times),
                0.55 * np.sin(0.4 * times) + 0.15 * np.sin(1.3 * times),
            )
        ),
    ).as_matrix()
    return poses


def write_pose_csv(path: Path, times: np.ndarray, poses: np.ndarray) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["t_sec", "x", "y", "z", "qw", "qx", "qy", "qz"])
        for timestamp, pose in zip(times, poses):
            quaternion = Rotation.from_matrix(pose[:3, :3]).as_quat()
            writer.writerow(
                [timestamp, *pose[:3, 3], quaternion[3], *quaternion[:3]]
            )


def write_tracker_csv(
    path: Path,
    times: np.ndarray,
    poses: np.ndarray,
    offset_s: float,
    epoch_minus_monotonic_s: float,
) -> None:
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
            reported = timestamp + offset_s
            quaternion = Rotation.from_matrix(pose[:3, :3]).as_quat()
            writer.writerow(
                [
                    int((reported - epoch_minus_monotonic_s) * 1e9),
                    int(reported * 1e9),
                    reported,
                    "WM0",
                    "LHR-APRILGRID-TEST",
                    *pose[:3, 3],
                    quaternion[3],
                    *quaternion[:3],
                ]
            )


def write_d405_frames(
    path: Path, times: np.ndarray, epoch_minus_monotonic_s: float
) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["sensor_event_wall", "sensor_event_mono"])
        for timestamp in times:
            writer.writerow([timestamp, timestamp - epoch_minus_monotonic_s])


def test_independent_aprilgrid_handeye_recovers_tracker_to_camera(
    tmp_path: Path,
) -> None:
    epoch = 1_788_000_000.0
    camera_relative_times = np.arange(0.0, 16.0, 1.0 / 30.0)
    tracker_relative_times = np.arange(-0.2, 16.2, 1.0 / 132.0)
    board_T_camera = trajectory(camera_relative_times)

    tracker_T_camera = np.eye(4)
    tracker_T_camera[:3, :3] = Rotation.from_euler(
        "xyz", [18.0, -11.0, 27.0], degrees=True
    ).as_matrix()
    tracker_T_camera[:3, 3] = [0.036, -0.052, 0.081]
    lighthouse_T_board = np.eye(4)
    lighthouse_T_board[:3, :3] = Rotation.from_euler(
        "xyz", [-9.0, 14.0, 33.0], degrees=True
    ).as_matrix()
    lighthouse_T_board[:3, 3] = [0.7, -0.4, 1.2]
    dense_board_T_camera = trajectory(tracker_relative_times)
    lighthouse_T_tracker = (
        lighthouse_T_board[None, :, :]
        @ dense_board_T_camera
        @ np.linalg.inv(tracker_T_camera)[None, :, :]
    )

    camera_pose_path = tmp_path / "aprilgrid_camera_poses.csv"
    tracker_path = tmp_path / "tracker.csv"
    frames_path = tmp_path / "d405_frames.csv"
    grid_path = tmp_path / "aprilgrid.yaml"
    grid_path.write_text(
        "target_type: aprilgrid\ntagCols: 6\ntagRows: 6\n"
        "tagSize: 0.0352\ntagSpacing: 0.3\n"
    )
    epoch_minus_monotonic_s = epoch - 5000.0
    write_pose_csv(
        camera_pose_path, epoch + camera_relative_times, board_T_camera
    )
    write_tracker_csv(
        tracker_path,
        epoch + tracker_relative_times,
        lighthouse_T_tracker,
        offset_s=0.012,
        epoch_minus_monotonic_s=epoch_minus_monotonic_s,
    )
    write_d405_frames(
        frames_path, epoch + camera_relative_times, epoch_minus_monotonic_s
    )

    result = MODULE.calibrate(
        camera_pose_path,
        tracker_path,
        grid_path,
        time_source="host_monotonic",
        d405_frames_path=frames_path,
        search_ms=30.0,
        max_gap_s=0.03,
    )

    estimated = np.asarray(result["tracker_T_d405_left_camera"])
    assert result["result"] == "PASS_CANDIDATE"
    assert result["independent_ground_truth"] is True
    assert result["slam_supervision"] is False
    assert result["slam_inputs"] == []
    assert "vio" not in str(result["inputs"]).lower()
    assert abs(result["tracker_query_offset_ms"] - 12.0) < 1.0
    assert np.linalg.norm(estimated[:3, 3] - tracker_T_camera[:3, 3]) < 0.002
    rotation_error = Rotation.from_matrix(
        estimated[:3, :3] @ tracker_T_camera[:3, :3].T
    ).magnitude()
    assert np.degrees(rotation_error) < 0.5


def test_host_monotonic_requires_d405_clock_mapping() -> None:
    try:
        MODULE.map_camera_times(
            np.asarray([1_788_000_000.0]), "host_monotonic", None
        )
    except ValueError as error:
        assert "--d405-frames is required" in str(error)
    else:
        raise AssertionError("host_monotonic must require d405_frames.csv")


def test_body_target_calibration_outputs_tracker_to_body_directly(
    tmp_path: Path,
) -> None:
    epoch = 1_788_000_000.0
    camera_relative_times = np.arange(0.0, 16.0, 1.0 / 30.0)
    tracker_relative_times = np.arange(-0.2, 16.2, 1.0 / 132.0)
    board_T_camera = trajectory(camera_relative_times)

    body_T_camera = np.eye(4)
    body_T_camera[:3, :3] = Rotation.from_euler(
        "xyz", [1.0, 89.0, -2.0], degrees=True
    ).as_matrix()
    body_T_camera[:3, 3] = [-0.014, -0.024, -0.010]
    tracker_T_body = np.eye(4)
    tracker_T_body[:3, :3] = Rotation.from_euler(
        "xyz", [3.0, -2.0, 5.0], degrees=True
    ).as_matrix()
    tracker_T_body[:3, 3] = [0.009, -0.002, 0.033]
    tracker_T_camera = tracker_T_body @ body_T_camera
    lighthouse_T_board = np.eye(4)
    lighthouse_T_board[:3, :3] = Rotation.from_euler(
        "xyz", [-9.0, 14.0, 33.0], degrees=True
    ).as_matrix()
    lighthouse_T_board[:3, 3] = [0.7, -0.4, 1.2]
    dense_board_T_camera = trajectory(tracker_relative_times)
    lighthouse_T_tracker = (
        lighthouse_T_board[None, :, :]
        @ dense_board_T_camera
        @ np.linalg.inv(tracker_T_camera)[None, :, :]
    )

    camera_pose_path = tmp_path / "aprilgrid_camera_poses.csv"
    tracker_path = tmp_path / "tracker.csv"
    frames_path = tmp_path / "d405_frames.csv"
    grid_path = tmp_path / "aprilgrid.yaml"
    body_camera_path = tmp_path / "vins.yaml"
    grid_path.write_text(
        "target_type: aprilgrid\ntagCols: 6\ntagRows: 6\n"
        "tagSize: 0.0352\ntagSpacing: 0.3\n"
    )
    storage = cv2.FileStorage(str(body_camera_path), cv2.FileStorage_WRITE)
    storage.write("body_T_cam0", body_T_camera)
    storage.release()
    epoch_minus_monotonic_s = epoch - 5000.0
    write_pose_csv(camera_pose_path, epoch + camera_relative_times, board_T_camera)
    write_tracker_csv(
        tracker_path,
        epoch + tracker_relative_times,
        lighthouse_T_tracker,
        offset_s=0.012,
        epoch_minus_monotonic_s=epoch_minus_monotonic_s,
    )
    write_d405_frames(
        frames_path, epoch + camera_relative_times, epoch_minus_monotonic_s
    )

    result = MODULE.calibrate(
        camera_pose_path,
        tracker_path,
        grid_path,
        time_source="host_monotonic",
        d405_frames_path=frames_path,
        search_ms=30.0,
        max_gap_s=0.03,
        body_camera_config_path=body_camera_path,
        tracker_query_offset_ms=12.0,
    )

    estimated = np.asarray(result["tracker_T_body"])
    assert result["calibration_target_frame"] == "docker2_vins_body"
    assert result["time_offset_policy"] == "fixed_from_independent_imu_tracker_sync"
    assert result["tracker_query_offset_ms"] == 12.0
    assert np.linalg.norm(estimated[:3, 3] - tracker_T_body[:3, 3]) < 0.002
    rotation_error = Rotation.from_matrix(
        estimated[:3, :3] @ tracker_T_body[:3, :3].T
    ).magnitude()
    assert np.degrees(rotation_error) < 0.5
    assert np.allclose(
        np.asarray(result["tracker_T_d405_left_camera"]),
        estimated @ body_T_camera,
    )
