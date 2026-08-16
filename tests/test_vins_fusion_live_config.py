from pathlib import Path

import numpy as np

from ego_vio.config import load_config
from ego_vio.imu.calibration import IMUCalibration


ROOT = Path(__file__).resolve().parents[1]


def test_live_config_pairs_vins_backend_with_level_calibration() -> None:
    stable = load_config(ROOT / "config/devices_vins_fusion_live.yaml")
    assert stable.units[0].vio.imu_level_calibration == ""
    assert stable.units[0].vio.odom_topic == "/odometry_rect"
    calibration = IMUCalibration.load(stable.units[0].imu.calibration)
    assert calibration.calibration_id == "imu_runtime_accel_calibrated_raw_gyro_20260816"
    assert np.allclose(calibration.gyro_matrix, np.eye(3))
    assert np.allclose(calibration.gyro_bias_deg_s, np.zeros(3))

    config = load_config(ROOT / "config/devices_vins_fusion_live_level_candidate.yaml")
    unit = config.units[0]
    assert unit.vio.backend == "vins_fusion_ros2"
    assert unit.vio.stereo is True
    assert unit.camera.width == 1280
    assert unit.camera.height == 720
    assert unit.camera.fps == 30
    assert unit.vio.odom_topic == "/odometry_rect"
    assert Path(unit.vio.imu_level_calibration).is_file()


def test_realtime_wrapper_hard_disables_failed_level_candidate() -> None:
    wrapper = (ROOT / "run_vins_realtime.sh").read_text(encoding="utf-8")

    assert "level-candidate已由跨会话A/B否决" in wrapper
    assert "prepare_level_vins_config.py" not in wrapper
    assert "[stable|smoke]" in wrapper


def test_jazzy_handoff_entrypoints_do_not_pin_humble_or_python310() -> None:
    realtime = (ROOT / "run_vins_realtime.sh").read_text(encoding="utf-8")
    capture = (ROOT / "capture_d405_720p_rgb_stereo_ir_rsusb.sh").read_text(
        encoding="utf-8"
    )
    builder = (ROOT / "scripts/build_librealsense_rsusb.sh").read_text(
        encoding="utf-8"
    )

    assert "source /opt/ros/humble/setup.bash" not in realtime
    assert 'source "$ROS_SETUP"' in realtime
    assert "EGO_VIO_ROS_DISTRO" in realtime
    assert "EGO_VIO_ROS_WS" in realtime
    assert "cpython-310" not in capture
    assert "sysconfig.get_config_var" in builder
    assert "cpython-310" not in builder
