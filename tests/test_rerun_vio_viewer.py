import math

import numpy as np

from scripts.rerun_vio_viewer import (
    LoopCorrection,
    SlamHealthDisplay,
    _managed_rerun_command,
    _shutdown_rclpy_once,
)


def test_viewer_spin_exception_remains_in_main_process() -> None:
    source = __import__("inspect").getsource(
        __import__("scripts.rerun_vio_viewer", fromlist=["main"])
    )

    assert "rclpy.spin(node)" in source
    assert "target=lambda: rclpy.spin(node)" not in source


def test_viewer_reuses_original_rich_visualizer_at_30_hz() -> None:
    source = __import__("inspect").getsource(
        __import__("scripts.rerun_vio_viewer", fromlist=["main"])
    )

    assert 'RerunVisualizer(["left_hand"]' in source
    assert 'viz.log_image("left_hand", cv_img, ts, max_hz=30.0)' in source
    assert 'viz.log_pose("left_hand", pose' in source
    assert 'parser.add_argument("--propagated-topic", default="/imu_propagate")' in source
    assert 'parser.add_argument("--raw-odom-topic", default="/odometry")' in source
    assert '"display": "odometry_rect/latest-only"' in source


def test_managed_rerun_is_memory_and_latency_bounded() -> None:
    command = _managed_rerun_command("/opt/rerun")

    assert command[0] == "/opt/rerun"
    assert "--port=9876" in command
    assert "--memory-limit=2GB" in command
    assert "--drop-at-latency=250ms" in command


class _AlreadyShutdownRclpy:
    shutdown_calls = 0

    @staticmethod
    def ok() -> bool:
        return False

    @classmethod
    def shutdown(cls) -> None:
        cls.shutdown_calls += 1


def test_viewer_does_not_shutdown_an_already_stopped_ros_context() -> None:
    _AlreadyShutdownRclpy.shutdown_calls = 0

    _shutdown_rclpy_once(_AlreadyShutdownRclpy)

    assert _AlreadyShutdownRclpy.shutdown_calls == 0


def test_loop_correction_applies_latest_backend_transform_to_fast_pose() -> None:
    tracker = LoopCorrection()
    yaw_90 = np.array(
        [0.0, 0.0, math.sin(math.pi / 4.0), math.cos(math.pi / 4.0)]
    )

    # raw [1,0,0] becomes rectified [0,2,0] with +90 degree yaw, so the
    # world correction is Rz(90) followed by translation [0,1,0].
    tracker.add_raw(7.0, np.array([1.0, 0.0, 0.0]), np.array([0, 0, 0, 1]))
    tracker.add_corrected(7.0, np.array([0.0, 2.0, 0.0]), yaw_90)

    position, orientation = tracker.apply(
        np.array([2.0, 0.0, 0.0]), np.array([0, 0, 0, 1])
    )

    np.testing.assert_allclose(position, [0.0, 3.0, 0.0], atol=1e-12)
    np.testing.assert_allclose(orientation, yaw_90, atol=1e-12)
    assert tracker.valid is True


def test_loop_correction_pairs_backend_messages_in_either_arrival_order() -> None:
    tracker = LoopCorrection()
    identity = np.array([0.0, 0.0, 0.0, 1.0])

    tracker.add_corrected(11.0, np.array([0.2, -0.1, 0.3]), identity)
    assert tracker.valid is False
    tracker.add_raw(11.0, np.zeros(3), identity)

    position, orientation = tracker.apply(np.array([1.0, 2.0, 3.0]), identity)
    np.testing.assert_allclose(position, [1.2, 1.9, 3.3], atol=1e-12)
    np.testing.assert_allclose(orientation, identity, atol=1e-12)


def test_loop_correction_never_rolls_back_to_an_older_pair() -> None:
    tracker = LoopCorrection()
    identity = np.array([0.0, 0.0, 0.0, 1.0])

    tracker.add_raw((12, 0), np.zeros(3), identity)
    tracker.add_corrected((12, 0), np.array([2.0, 0.0, 0.0]), identity)
    tracker.add_raw((11, 0), np.zeros(3), identity)
    tracker.add_corrected((11, 0), np.array([1.0, 0.0, 0.0]), identity)

    position, _ = tracker.apply(np.zeros(3), identity)
    np.testing.assert_allclose(position, [2.0, 0.0, 0.0], atol=1e-12)
    assert tracker.paired_updates == 1
    assert tracker.rejected_out_of_order == 1


def test_backend_pairing_keeps_a_short_non_blocking_history() -> None:
    source = __import__("inspect").getsource(
        __import__("scripts.rerun_vio_viewer", fromlist=["main"])
    )

    assert "backend_pair_qos = QoSProfile(\n        depth=32" in source
    assert "ReliabilityPolicy.BEST_EFFORT" in source
    assert "on_raw_odom, backend_pair_qos" in source
    assert "on_corrected_odom, backend_pair_qos" in source


def test_viewer_health_display_exposes_latched_failure() -> None:
    health = SlamHealthDisplay()

    health.update(
        '{"state":"SLAM_FAILED","product_usable":false,'
        '"failures":["estimator_pose_integrity:position_step_exceeded"]}'
    )

    assert health.state == "SLAM_FAILED"
    assert health.product_usable == "false"
    assert health.failures == "estimator_pose_integrity:position_step_exceeded"


def test_viewer_health_display_ignores_malformed_update() -> None:
    health = SlamHealthDisplay()
    health.update('{"state":"SLAM_HEALTHY","product_usable":true}')
    health.update("not-json")

    assert health.state == "SLAM_HEALTHY"
    assert health.product_usable == "true"
