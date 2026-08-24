from pathlib import Path
import re

import yaml

from ego_vio.config import load_config


ROOT = Path(__file__).resolve().parents[1]


def test_customer_wrapper_defaults_to_product_and_rejects_legacy_modes() -> None:
    wrapper = (ROOT / "run_vins_realtime.sh").read_text(encoding="utf-8")

    assert 'MODE="${1:-product-live}"' in wrapper
    assert '[[ "$MODE" != "product-live" ]]' in wrapper
    assert "只允许 product-live" in wrapper
    assert "/home/robot/ros2_ws" not in wrapper
    assert "LEGACY_PRODUCT" not in wrapper
    assert "/.planning/" not in wrapper


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


def test_product_live_wrapper_pins_release_identity_and_health_monitor() -> None:
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
    assert "EXTRA_RUNTIME_ARGS=(--no-viz)" in wrapper
    assert "--max-data-age-s 0.5" in wrapper
    assert "动态近景失效保护: 单步>0.05m即锁存失败" in wrapper
    assert "PYTHONUNBUFFERED=1" in wrapper
    assert "rerun_vio_viewer\\.py" in wrapper
    assert 'setsid "$PYTHON_BIN" "$ROOT/scripts/rerun_vio_viewer.py"' in wrapper
    assert "nice -n 10" not in wrapper
    assert "Rerun可视化进程异常退出" in wrapper
    assert 'kill -0 "$VIEWER_PID"' in wrapper
    assert 'wait_with_viewer_supervision "$RUNTIME_PID"' in wrapper
    assert "EGO_VIO_FAIL_FAST_SLAM" in wrapper
    assert "estimator_pose_integrity:" in wrapper
    assert "产品主链立即停止" in wrapper
    assert 'setsid env LD_LIBRARY_PATH="$PRODUCT_LIVE_VINS_WS/build' in wrapper
    assert 'if [[ "$DISABLE_VIEWER" != "1" ]]; then' in wrapper
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
    assert "imu_lead_guard_ms=unit.vio.imu_lead_guard_ms" in runtime

    product_device = yaml.safe_load(
        (ROOT / "config/devices_product_live_stm32.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert product_device["units"][0]["vio"]["imu_lead_guard_ms"] == -6.812


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
    assert "const bool enqueue_for_backend = inputImageCount % 2 == 0;" in estimator
    assert "inputImageCount % 2 == 0 || featureBuffer.empty()" not in estimator
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
    assert 'setsid env PYTHONPATH="$RSUSB_PYTHON:$ROOT' in wrapper
    assert '"${RUNTIME_PID:-}"' in wrapper
    assert 'wait "$owner_pid"' in wrapper


def test_customer_entrypoint_pins_humble_and_uses_current_python_abi() -> None:
    realtime = (ROOT / "run_vins_realtime.sh").read_text(encoding="utf-8")
    capture = (ROOT / "capture_d405_720p_rgb_stereo_ir_rsusb.sh").read_text(
        encoding="utf-8"
    )
    builder = (ROOT / "scripts/build_librealsense_rsusb.sh").read_text(
        encoding="utf-8"
    )

    assert 'source "$ROS_SETUP"' in realtime
    assert 'ROS_DISTRO_NAME="${EGO_VIO_ROS_DISTRO:-humble}"' in realtime
    assert '[[ "$ROS_DISTRO_NAME" != "humble" ]]' in realtime
    assert "EGO_VIO_ROS_DISTRO" in realtime
    assert "EGO_VIO_ROS_WS" not in realtime
    assert "cpython-310" not in capture
    assert "sysconfig.get_config_var" in builder
    assert "cpython-310" not in builder


def test_customer_offline_entrypoint_uses_only_signed_product_artifacts() -> None:
    wrapper = (ROOT / "run_slam_postprocess.sh").read_text(encoding="utf-8")

    assert "config/product_live_stm32/vins_config.yaml" in wrapper
    assert ".product_live_build/vins_ws" not in wrapper
    assert 'BUILD_ROOT="$ROOT/.product_live_build"' in wrapper
    assert "PRODUCT_LIVE_VINS_SHA256" in wrapper
    assert "PRODUCT_LIVE_LOOP_SHA256" in wrapper
    assert "PRODUCT_LIVE_REPLAY_SHA256" in wrapper
    assert "/home/robot/ros2_ws" not in wrapper
    assert "/.planning/" not in wrapper
    assert "product_live_z_candidate" not in wrapper
