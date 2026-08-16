import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from extract_aprilgrid_ground_truth import ros_image_to_gray, session_format


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
