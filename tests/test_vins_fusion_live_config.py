from pathlib import Path

import yaml

from ego_vio.config import load_config


ROOT = Path(__file__).resolve().parents[1]


def test_live_config_pairs_vins_backend_with_level_calibration() -> None:
    stable = load_config(ROOT / "config/devices_vins_fusion_live.yaml")
    assert stable.units[0].vio.imu_level_calibration == ""
    assert stable.units[0].vio.odom_topic == "/odometry_rect"
    assert stable.units[0].imu.calibration == ""

    rejected = yaml.safe_load(
        (ROOT / "config/imu_runtime_accel_calibrated_raw_gyro_20260816.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert rejected["acceptance"]["status"] == "FAIL"
    assert rejected["acceptance"]["runtime_applied"] is False

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
    assert "level-candidate" in wrapper


def test_frozen_record_mode_keeps_recording_and_live_vins_on_one_sensor_owner() -> None:
    wrapper = (ROOT / "run_vins_realtime.sh").read_text(encoding="utf-8")

    assert "frozen-record" in wrapper
    assert "capture_d405_720p_rgb_stereo_ir_rsusb.sh" in wrapper
    assert "--publish-vins" in wrapper


def test_frozen_mode_does_not_require_the_current_workspace_config() -> None:
    wrapper = (ROOT / "run_vins_realtime.sh").read_text(encoding="utf-8")
    capture = (ROOT / "scripts/capture_d405_720p_rgb_stereo_ir.py").read_text(
        encoding="utf-8"
    )

    assert 'required_files=("$ROS_SETUP" "$RSUSB_MODULE")' in wrapper
    assert 'default=None' in capture
    assert 'else "raw_unmodified"' in capture


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
