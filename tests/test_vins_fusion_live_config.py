from pathlib import Path
import re

import numpy as np
import yaml

from ego_vio.config import load_config
from ego_vio.imu.calibration import IMUCalibration
from ego_vio.imu.imu_reader import ImuSample
from ego_vio.imu.vins_transform import load_vins_imu_rotation
from ego_vio.vio.openvins_ros2_bridge import _rotate_imu_to_vins


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


def test_product_live_config_locks_stm32_protocol_and_current_devices() -> None:
    config = load_config(ROOT / "config/devices_product_live_stm32.yaml")
    unit = config.units[0]

    assert unit.imu.protocol == "stm32_combined_v1"
    assert "Silicon_Labs_CP2102N" in unit.imu.port
    assert unit.camera.serial == "260322273737"
    assert unit.camera.stereo_ir is True
    assert unit.camera.rgb_preview is True
    assert unit.camera.auto_exposure is True
    assert unit.camera.auto_exposure_limit_us == 8000
    assert unit.camera.auto_gain_limit == 248
    assert unit.vio.odom_topic == "/odometry_rect"

    vins = (ROOT / "config/product_live_stm32/vins_config.yaml").read_text(
        encoding="utf-8"
    )
    assert "td: -0.009312" in vins
    assert "estimate_td: 0" in vins
    assert "confirmed_loop_correction_ceiling_m: 0.50" in vins
    assert "raw_odometry_failure_step_m: 0.05" in vins


def test_product_live_z_candidate_is_isolated_and_applies_092447_accel() -> None:
    baseline = load_config(ROOT / "config/devices_product_live_stm32.yaml")
    candidate = load_config(
        ROOT / "config/devices_product_live_z_candidate.yaml"
    )

    assert baseline.units[0].imu.calibration == ""
    calibration_path = Path(candidate.units[0].imu.calibration)
    assert calibration_path.is_file()
    calibration = IMUCalibration.load(calibration_path)
    assert calibration.calibration_id == "imu_accel_092447_runtime_candidate_20260823"
    np.testing.assert_allclose(
        calibration.accel_matrix,
        [
            [0.9998117562600883, -0.00041625862959804305, -0.0005567968634530573],
            [-0.00041625862959804305, 0.999859786653393, 0.0000854783833772188],
            [-0.0005567968634530018, 0.00008547838337724656, 0.9994799773983573],
        ],
        atol=1e-15,
    )
    np.testing.assert_allclose(
        calibration.accel_offset_g,
        [0.0021808864839423345, 0.0010338718236909095, -0.0030959932145016886],
        atol=1e-15,
    )
    assert candidate.units[0].vio.imu_level_calibration == ""

    source_report = yaml.safe_load(
        Path(
            "/home/robot/ego_vio_calib_kit/imu_bench_results/"
            "20260821_092447_483220_imu_multipose_bench/report.yaml"
        ).read_text(encoding="utf-8")
    )
    source_matrix = np.asarray(source_report["correction_matrix"], dtype=float)
    source_bias_m_s2 = np.asarray(source_report["bias_m_s2"], dtype=float)
    gravity = float(source_report["gravity_m_s2"])
    np.testing.assert_allclose(calibration.accel_matrix, source_matrix, atol=1e-15)
    np.testing.assert_allclose(
        calibration.accel_offset_g,
        -(source_matrix @ source_bias_m_s2) / gravity,
        atol=1e-15,
    )

    # Runtime applies the ellipsoid correction in raw IMU axes first, then
    # preserves the historical 91.42-degree VINS-axis rotation.  Lock the
    # actual production methods and their units/order against the source
    # report formula.
    raw_accel_g = np.asarray([0.13, -0.47, 0.86], dtype=float)
    rotation = load_vins_imu_rotation()
    raw_sample = ImuSample(
        ts=1.0,
        counter=7,
        gx=1.2,
        gy=-2.3,
        gz=3.4,
        ax=float(raw_accel_g[0]),
        ay=float(raw_accel_g[1]),
        az=float(raw_accel_g[2]),
        temp=25.0,
        rx_time=1.0,
    )
    calibrated_sample = calibration.apply(raw_sample)
    _, runtime_accel_m_s2 = _rotate_imu_to_vins(
        np.asarray(
            [calibrated_sample.gx, calibrated_sample.gy, calibrated_sample.gz],
            dtype=float,
        ),
        np.asarray(
            [calibrated_sample.ax, calibrated_sample.ay, calibrated_sample.az],
            dtype=float,
        ),
        rotation=rotation,
    )
    source_accel_m_s2 = rotation @ (
        source_matrix @ (raw_accel_g * gravity - source_bias_m_s2)
    )
    np.testing.assert_allclose(runtime_accel_m_s2, source_accel_m_s2, atol=1e-12)

    baseline_vins = (ROOT / "config/product_live_stm32/vins_config.yaml").read_text(
        encoding="utf-8"
    )
    candidate_vins = (
        ROOT / "config/product_live_z_candidate/vins_config.yaml"
    ).read_text(encoding="utf-8")
    assert "acc_n: 8.28e-3" in baseline_vins
    assert "acc_n: 0.1" in candidate_vins
    for locked_line in (
        "td: -0.009312",
        "estimate_td: 0",
        "confirmed_loop_correction_ceiling_m: 0.50",
    ):
        assert locked_line in baseline_vins
        assert locked_line in candidate_vins

    def without_comments(document: str) -> str:
        return "\n".join(
            line
            for line in document.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )

    normalized_candidate = candidate_vins.replace(
        "/tmp/ego_vio_product_live_z_candidate_output/",
        "/tmp/ego_vio_product_live_output/",
    ).replace("acc_n: 0.1", "acc_n: 8.28e-3")
    assert without_comments(normalized_candidate) == without_comments(baseline_vins)
    assert (
        ROOT / "config/product_live_z_candidate/left.yaml"
    ).read_bytes() == (ROOT / "config/product_live_stm32/left.yaml").read_bytes()
    assert (
        ROOT / "config/product_live_z_candidate/right.yaml"
    ).read_bytes() == (ROOT / "config/product_live_stm32/right.yaml").read_bytes()


def test_product_live_z_candidate_has_an_explicit_non_release_entrypoint() -> None:
    wrapper = (ROOT / "run_vins_realtime.sh").read_text(encoding="utf-8")

    assert "product-live-z-candidate" in wrapper
    assert "092447" in wrapper
    assert "acc_n=0.1" in wrapper
    assert "未签发" in wrapper


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


def test_product_live_wrapper_pins_candidate_identity_and_health_monitor() -> None:
    wrapper = (ROOT / "run_vins_realtime.sh").read_text(encoding="utf-8")

    assert "product-live" in wrapper
    assert "dbb0b8d78aa62c6f93577f12639702e37315ff62d794ee356fcb7a6c3e7fd956" in wrapper
    assert "186c7db6c224469ab18309cdc24904c2e42c2ba2a3a1a9b5c1e1e22c497c2f80" in wrapper
    assert "ca6ca5ab02ca10d3ddce2bfb77448ba0fbb6fe48ce215e7f5ae77f08defb70b4" in wrapper
    assert "PRODUCT_LIVE_VINS_LIBRARY" in wrapper
    assert "actual_product_vins_library_sha256" in wrapper
    assert "devices_product_live_stm32.yaml" in wrapper
    assert "config/product_live_stm32/vins_config.yaml" in wrapper
    assert "slam_runtime_watchdog.py" in wrapper
    assert "--corrected-topic /odometry_rect" in wrapper
    assert "--raw-odom-topic /odometry" in wrapper
    assert "--propagated-topic /imu_propagate" in wrapper
    assert "产品模式可视化与传感器主链进程隔离" in wrapper
    assert "--image-topic /rgb_preview/image_raw" in wrapper
    assert "EXTRA_RUNTIME_ARGS+=(--no-viz)" in wrapper
    assert "--max-data-age-s 0.5" in wrapper
    assert "动态近景失效保护: 单步>0.05m即锁存失败" in wrapper
    assert "PYTHONUNBUFFERED=1" in wrapper
    assert "rerun_vio_viewer\\.py" in wrapper
    assert 'setsid "$PYTHON_BIN" "$ROOT/scripts/rerun_vio_viewer.py"' in wrapper
    assert "nice -n 10" not in wrapper
    assert "Rerun可视化进程异常退出" in wrapper
    assert 'kill -0 "$VIEWER_PID"' in wrapper
    assert 'wait_with_viewer_supervision "$CAPTURE_PID"' in wrapper
    assert 'wait_with_viewer_supervision "$RUNTIME_PID"' in wrapper
    assert "EGO_VIO_FAIL_FAST_SLAM" in wrapper
    assert "estimator_pose_integrity:" in wrapper
    assert "产品主链立即停止" in wrapper
    assert 'if is_product_live_mode; then\n  setsid env LD_LIBRARY_PATH=' in wrapper
    assert (
        'if is_product_live_mode && [[ "$DISABLE_VIEWER" != "1" ]]; then'
        not in wrapper
    )
    assert "EGO_VIO_PRODUCT_LIVE_DEVICE_CONFIG" in wrapper
    assert "EGO_VIO_PRODUCT_LIVE_CONFIG" in wrapper
    assert "EGO_VIO_PRODUCT_CALIBRATION_LABEL" in wrapper
    assert "EGO_VIO_RUN_DIR" in wrapper
    assert "EGO_VIO_DISABLE_VIEWER" in wrapper
    assert "EGO_VIO_PRODUCT_LIVE_BUILD_ROOT" in wrapper
    assert "EGO_VIO_PRODUCT_LIVE_VINS_WS" in wrapper
    assert "EGO_VIO_PRODUCT_LIVE_LOOP_WS" in wrapper
    assert "EGO_VIO_PRODUCT_LIVE_HASH_MANIFEST" in wrapper
    assert ".product_live_build" in wrapper
    assert "product_live_hashes.env" in wrapper
    assert 'EXTRA_RUNTIME_ARGS+=("${CAPTURE_ARGS[@]}")' in wrapper

    viewer = (ROOT / "scripts/rerun_vio_viewer.py").read_text(encoding="utf-8")
    assert 'parser.add_argument("--image-topic"' in viewer
    assert "ReliabilityPolicy.BEST_EFFORT" in viewer
    assert "depth=1" in viewer

    watchdog = (ROOT / "scripts/slam_runtime_watchdog.py").read_text(
        encoding="utf-8"
    )
    assert "watch_qos = QoSProfile(" in watchdog
    assert "ReliabilityPolicy.BEST_EFFORT" in watchdog
    assert "depth=1" in watchdog
    assert 'default="/vins/pose_integrity"' in watchdog
    assert "DurabilityPolicy.TRANSIENT_LOCAL" in watchdog

    runtime = (ROOT / "ego_vio/runtime.py").read_text(encoding="utf-8")
    assert 'preview_topic="/rgb_preview/image_raw"' in runtime
    assert "preview_hz=30.0" in runtime


def test_estimator_logs_each_tracking_constraint_stage() -> None:
    estimator = (
        ROOT
        / "components/vins_fusion_ros2/vins/src/estimator/estimator.cpp"
    ).read_text(encoding="utf-8")

    for field in (
        "left=",
        "temporal=",
        "new=",
        "stereo=",
        "mature30hz=",
        "frame_features=",
        "tracked_from_previous=",
        "new_features=",
        "long_tracks=",
    ):
        assert field in estimator
    assert "[TRACKING-DEGRADED]" in estimator


def test_product_live_tracks_30hz_but_rate_limits_the_backend_like_old_stable() -> None:
    estimator = (
        ROOT / "components/vins_fusion_ros2/vins/src/estimator/estimator.cpp"
    ).read_text(encoding="utf-8")

    track_position = estimator.index("featureTracker.trackImage")
    enqueue_position = estimator.index("const bool enqueue_for_backend")
    assert track_position < enqueue_position
    assert "inputImageCount % 2 == 0 || featureBuffer.empty()" in estimator
    assert "if (enqueue_for_backend)" in estimator


def test_product_live_pose_integrity_failure_blocks_every_pose_consumer() -> None:
    source = (
        ROOT / "components/vins_fusion_ros2/src/vins_estimator.cpp"
    ).read_text(encoding="utf-8")
    header = (
        ROOT
        / "components/vins_fusion_ros2/include/vins_fusion_ros2/"
        "pose_integrity_guard.h"
    ).read_text(encoding="utf-8")

    assert "rawOdometryFailureStepM()" in source
    assert "decision == PoseIntegrityDecision::kLatchFailure" in source
    assert "pose_integrity_failed_.load()) return" in source
    assert "pose_integrity_failed_.load()) continue" in source
    assert '"/vins/pose_integrity"' in source
    assert "transient_local()" in source
    assert "kRejectLatched" in header
    assert "position_step_exceeded" in header


def test_product_live_starts_one_pinned_vins_and_loop_and_cleans_every_pid() -> None:
    wrapper = (ROOT / "run_vins_realtime.sh").read_text(encoding="utf-8")

    vins_launches = re.findall(
        r'^\s*"\$PRODUCT_LIVE_VINS_EXECUTABLE"\s+\\$', wrapper, re.MULTILINE
    )
    loop_launches = re.findall(
        r'^\s*"\$PRODUCT_LIVE_LOOP_EXECUTABLE"\s+"\$VINS_CONFIG"\s+\\$',
        wrapper,
        re.MULTILINE,
    )
    assert len(vins_launches) == 1
    assert len(loop_launches) == 1
    assert "kill -KILL" in wrapper
    assert "pkill -9 -f" not in wrapper
    assert "pgrep -af" in wrapper
    assert 'FROZEN_LOOP_BASENAME="$(basename -- "$FROZEN_LOOP_EXECUTABLE")"' in wrapper
    assert "FROZEN_LOOP_BASENAME_ERE" in wrapper
    assert '${FROZEN_LOOP_BASENAME_ERE}([[:space:]]|$)' in wrapper
    assert "capture_d405_720p_rgb_stereo_ir\\.py" in wrapper
    assert "--publish-vins([[:space:]]|$)" in wrapper
    assert "检测到残留VINS/回环进程" in wrapper
    assert 'flock -n "$LIVE_LOCK_FD"' in wrapper
    assert "拒绝并发启动" in wrapper
    assert "LIVE_LOCK_FD=9" in wrapper
    assert 'exec 9>"$LIVE_LOCK_FILE"' in wrapper
    assert "9>&-" in wrapper
    assert 'kill -- "-$pid"' in wrapper
    assert 'kill -KILL -- "-$pid"' in wrapper
    assert 'setsid env LD_LIBRARY_PATH=' in wrapper
    assert 'setsid "$ROOT/capture_d405_720p_rgb_stereo_ir_rsusb.sh"' in wrapper
    assert '"${CAPTURE_PID:-}"' in wrapper
    assert 'setsid env PYTHONPATH="$RSUSB_PYTHON:$ROOT' in wrapper
    assert '"${RUNTIME_PID:-}"' in wrapper
    assert 'wait "$owner_pid"' in wrapper


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
