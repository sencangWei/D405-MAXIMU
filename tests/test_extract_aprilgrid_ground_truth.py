import sys
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from extract_aprilgrid_ground_truth import (
    ros_image_to_gray,
    session_format,
    stereo_pose,
)


def test_session_format_prefers_legacy_calibration_layout(tmp_path: Path):
    session = tmp_path / "session"
    (session / "left_hand").mkdir(parents=True)
    (session / "left_hand" / "camera_ts.csv").write_text("index,ts,counter\n")
    (session / "capture.db3").write_bytes(b"db3")

    assert session_format(session) == "legacy_frames"


def test_session_format_accepts_rsusb_db3_layout(tmp_path: Path):
    session = tmp_path / "session"
    session.mkdir()
    (session / "capture.db3").write_bytes(b"db3")

    assert session_format(session) == "rsusb_db3"


def test_ros_image_to_gray_removes_row_padding_without_copying_pixels():
    message = SimpleNamespace(
        encoding="mono8",
        width=3,
        height=2,
        step=5,
        data=bytes([1, 2, 3, 99, 99, 4, 5, 6, 88, 88]),
    )

    image = ros_image_to_gray(message)

    assert image.dtype == np.uint8
    assert image.tolist() == [[1, 2, 3], [4, 5, 6]]


def test_ros_image_to_gray_rejects_non_mono8():
    message = SimpleNamespace(
        encoding="rgb8",
        width=1,
        height=1,
        step=3,
        data=bytes([1, 2, 3]),
    )

    try:
        ros_image_to_gray(message)
    except ValueError as error:
        assert "mono8" in str(error)
    else:
        raise AssertionError("rgb8 should be rejected")


def test_stereo_pose_recovers_grid_pose() -> None:
    intrinsic = np.array(
        [[649.2, 0.0, 638.4], [0.0, 649.2, 354.5], [0.0, 0.0, 1.0]]
    )
    distortion = np.zeros(4)
    object_points = np.array(
        [
            [0.00, 0.00, 0.0],
            [0.20, 0.00, 0.0],
            [0.20, 0.20, 0.0],
            [0.00, 0.20, 0.0],
            [0.05, 0.05, 0.0],
            [0.15, 0.05, 0.0],
            [0.15, 0.15, 0.0],
            [0.05, 0.15, 0.0],
        ],
        dtype=np.float64,
    )
    rvec = np.array([0.12, -0.18, 0.05])
    tvec = np.array([0.02, -0.03, 0.55])
    cam1_T_cam0 = np.eye(4)
    cam1_T_cam0[0, 3] = -0.018083253875
    left_image, _ = cv2.projectPoints(
        object_points, rvec, tvec, intrinsic, distortion
    )
    cam0_R_grid = Rotation.from_rotvec(rvec).as_matrix()
    cam1_R_grid = cam1_T_cam0[:3, :3] @ cam0_R_grid
    cam1_t_grid = cam1_T_cam0[:3, :3] @ tvec + cam1_T_cam0[:3, 3]
    right_image, _ = cv2.projectPoints(
        object_points,
        Rotation.from_matrix(cam1_R_grid).as_rotvec(),
        cam1_t_grid,
        intrinsic,
        distortion,
    )

    estimated_rvec, estimated_tvec, rmse = stereo_pose(
        object_points,
        left_image.reshape(-1, 2),
        object_points,
        right_image.reshape(-1, 2),
        intrinsic,
        distortion,
        intrinsic,
        distortion,
        cam1_T_cam0,
    )

    assert np.linalg.norm(estimated_tvec - tvec) < 1.0e-6
    rotation_error = Rotation.from_rotvec(estimated_rvec).inv() * Rotation.from_rotvec(
        rvec
    )
    assert rotation_error.magnitude() < 1.0e-6
    assert rmse < 1.0e-6
