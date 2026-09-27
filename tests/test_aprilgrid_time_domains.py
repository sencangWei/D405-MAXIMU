"""Sign and single-compensation regressions for independent Tracker timing."""
from pathlib import Path

import cv2
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from test_calibrate_lighthouse_aprilgrid import (
    MODULE, trajectory, write_d405_frames, write_pose_csv, write_tracker_csv,
)


def write_config(path, td_ms=-9.109323, estimate_td=0):
    fs = cv2.FileStorage(str(path), cv2.FileStorage_WRITE)
    fs.write("body_T_cam0", np.eye(4))
    if td_ms is not None:
        fs.write("td", td_ms / 1000)
    fs.write("estimate_td", estimate_td)
    fs.release()


@pytest.mark.parametrize("td_ms,imu_offset_ms", [(-9.109323, -3.677539), (9.0, 4.0), (0.0, 0.0)])
def test_known_handeye_recovers_with_composed_camera_time(tmp_path, td_ms, imu_offset_ms):
    epoch = 1_788_000_000.0
    epoch_offset = epoch - 5000.0
    ct = np.arange(0.0, 16.0, 1 / 30)
    tt = np.arange(-0.2, 16.2, 1 / 132)
    x = np.eye(4)
    x[:3, :3] = Rotation.from_euler("xyz", [18, -11, 27], degrees=True).as_matrix()
    x[:3, 3] = [0.036, -0.052, 0.081]
    camera, tracker, frames, grid, config = [tmp_path / name for name in
        ("camera.csv", "tracker.csv", "frames.csv", "grid.yaml", "vins.yaml")]
    write_pose_csv(camera, epoch + ct, trajectory(ct))
    write_tracker_csv(tracker, epoch + tt, trajectory(tt) @ np.linalg.inv(x),
                      (td_ms + imu_offset_ms) / 1000, epoch_offset)
    write_d405_frames(frames, epoch + ct, epoch_offset)
    grid.write_text("target_type: aprilgrid\ntagCols: 6\ntagRows: 6\ntagSize: 0.0352\ntagSpacing: 0.3\n")
    write_config(config, td_ms)
    result = MODULE.calibrate(camera, tracker, grid, "host_monotonic", frames,
        30.0, 0.03, config, imu_tracker_query_offset_ms=imu_offset_ms)
    estimated = np.asarray(result["tracker_T_body"])
    assert result["tracker_query_offset_ms"] == pytest.approx(td_ms + imu_offset_ms)
    assert result["time_alignment"]["camera_imu_td_ms"] == pytest.approx(td_ms)
    assert result["time_alignment"]["imu_tracker_query_offset_ms"] == imu_offset_ms
    assert result["time_offset_policy"] == "fixed_imu_tracker_sync_composed_with_camera_imu_td"
    assert np.linalg.norm(estimated[:3, 3] - x[:3, 3]) < 0.0002
    assert np.degrees(Rotation.from_matrix(estimated[:3, :3] @ x[:3, :3].T).magnitude()) < 0.03


def test_direct_camera_offset_is_not_compensated_twice():
    value, metadata = MODULE.resolve_tracker_query_offset(12.0, None, None)
    assert value == 12.0
    assert metadata["input_domain"] == "camera"


@pytest.mark.parametrize("td_ms,estimate_td", [(None, 0), (-9.109323, 1), (float("nan"), 0)])
def test_invalid_formal_timing_is_rejected(tmp_path, td_ms, estimate_td):
    config = tmp_path / "bad.yaml"
    write_config(config, td_ms, estimate_td)
    with pytest.raises(ValueError):
        MODULE.resolve_tracker_query_offset(None, -3.0, config)


def test_imu_domain_requires_config_and_rejects_double_input():
    with pytest.raises(ValueError):
        MODULE.resolve_tracker_query_offset(None, -3.0, None)
    with pytest.raises(ValueError):
        MODULE.resolve_tracker_query_offset(1.0, -3.0, Path("unused.yaml"))


@pytest.mark.parametrize("camera,imu", [(float("nan"), None), (None, float("inf"))])
def test_nonfinite_offsets_are_rejected(camera, imu):
    with pytest.raises(ValueError):
        MODULE.resolve_tracker_query_offset(camera, imu, None)
