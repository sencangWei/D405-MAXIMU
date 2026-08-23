import queue
import threading

import numpy as np

from ego_vio.camera.realsense_capture import CameraFrame
from ego_vio.vio.openvins_ros2_bridge import OpenVINSROS2Bridge
from ego_vio.vio.openvins_ros2_bridge import _put_latest
from ego_vio.vio.openvins_ros2_bridge import _rotate_imu_to_vins
from ego_vio.vio.openvins_ros2_bridge import _summarize_camera_publish_timings


def test_live_imu_transform_matches_vins_replay_matrix():
    gyro, accel = _rotate_imu_to_vins(
        np.array([1.0, 2.0, 3.0]),
        np.array([0.0, -1.0, 0.0]),
    )

    expected_rotation = np.array(
        [
            [0.99980212, -0.01423891, -0.01389161],
            [-0.01423891, -0.02458715, -0.99959628],
            [0.01389161, 0.99959628, -0.02478503],
        ]
    )
    np.testing.assert_allclose(
        gyro, expected_rotation @ np.radians([1.0, 2.0, 3.0]), rtol=0, atol=1e-12
    )
    np.testing.assert_allclose(
        accel, expected_rotation @ np.array([0.0, -9.80665, 0.0]), rtol=0, atol=1e-12
    )
    assert accel[2] < -9.7


def _bridge_at_transport_time(latest_imu_t: float) -> OpenVINSROS2Bridge:
    bridge = OpenVINSROS2Bridge.__new__(OpenVINSROS2Bridge)
    bridge._stereo = True
    bridge._epoch_offset = 0.0
    bridge._cam_latency_s = 0.0
    bridge._latest_imu_t = latest_imu_t
    bridge._imu_guard_s = 0.010
    bridge._imu_ready = threading.Condition()
    bridge._cam_queue = queue.Queue(maxsize=3)
    bridge._cam_warmup_discarded = 0
    bridge._cam_queue_dropped = 0
    bridge._preview_pub = None
    return bridge


def _stereo_frame(ts: float) -> CameraFrame:
    left = np.zeros((4, 6), dtype=np.uint8)
    right = np.ones((4, 6), dtype=np.uint8)
    return CameraFrame(
        ts=ts,
        color=left,
        depth=None,
        frame_idx=1,
        infrared_left=left,
        infrared_right=right,
    )


def test_live_camera_queues_after_imu_transport_starts_even_before_guard_lead():
    bridge = _bridge_at_transport_time(latest_imu_t=100.0)

    bridge.feed_camera(_stereo_frame(ts=100.0))

    assert bridge._cam_queue.qsize() == 1
    assert bridge._cam_warmup_discarded == 0


def test_live_camera_discards_only_until_first_post_warmup_imu():
    bridge = _bridge_at_transport_time(latest_imu_t=float("-inf"))

    bridge.feed_camera(_stereo_frame(ts=100.0))

    assert bridge._cam_queue.empty()
    assert bridge._cam_warmup_discarded == 1


def test_operator_preview_queue_replaces_old_frame_without_blocking():
    preview_queue = queue.Queue(maxsize=1)

    assert _put_latest(preview_queue, "old") == 0
    assert _put_latest(preview_queue, "new") == 1

    assert preview_queue.qsize() == 1
    assert preview_queue.get_nowait() == "new"


def test_product_rgb_preview_is_downsampled_and_replaces_stale_frame():
    bridge = _bridge_at_transport_time(latest_imu_t=100.0)
    bridge._preview_pub = object()
    bridge._preview_queue = queue.Queue(maxsize=1)
    bridge._preview_period_s = 0.1
    bridge._preview_last_enqueued_t = float("-inf")
    bridge._preview_queue_dropped = 0

    first = _stereo_frame(ts=100.0)
    first.color = np.arange(4 * 6 * 3, dtype=np.uint8).reshape(4, 6, 3)
    bridge.feed_camera(first)

    queued = bridge._preview_queue.get_nowait()
    assert queued[:3] == (100.0, 2, 3)
    assert queued[3] == np.ascontiguousarray(first.color[::2, ::2]).tobytes()

    # A slow viewer may leave the previous preview queued.  The producer must
    # replace it immediately instead of ever blocking the calibrated IR path.
    bridge._preview_queue.put_nowait(queued)
    second = _stereo_frame(ts=100.2)
    second.color = np.full((4, 6, 3), 255, dtype=np.uint8)
    bridge.feed_camera(second)

    latest = bridge._preview_queue.get_nowait()
    assert latest[:3] == (100.2, 2, 3)
    assert latest[3] == np.ascontiguousarray(second.color[::2, ::2]).tobytes()
    assert bridge._preview_queue_dropped == 1


def test_product_rgb_preview_default_is_30_hz():
    import inspect

    default = inspect.signature(OpenVINSROS2Bridge).parameters["preview_hz"].default

    assert default == 30.0


def test_product_rgb_preview_keeps_30_hz_at_epoch_timestamps():
    bridge = _bridge_at_transport_time(latest_imu_t=100.0)
    bridge._preview_pub = object()
    bridge._preview_queue = queue.Queue(maxsize=1)
    bridge._preview_period_s = 1.0 / 30.0
    bridge._preview_last_enqueued_t = float("-inf")
    bridge._preview_queue_dropped = 0

    epoch = 1_777_000_000.0
    for index in range(300):
        frame = _stereo_frame(ts=epoch + index / 30.0)
        frame.color = np.zeros((4, 6, 3), dtype=np.uint8)
        bridge.feed_camera(frame)

    accepted = bridge._preview_queue_dropped + bridge._preview_queue.qsize()
    assert accepted >= 299


def test_camera_publish_timing_summary_separates_copy_and_dds_wait():
    samples = [
        {"prepare0": 1.0, "publish0": 2.0, "prepare1": 3.0, "publish1": 4.0, "cycle": 10.0},
        {"prepare0": 2.0, "publish0": 3.0, "prepare1": 4.0, "publish1": 5.0, "cycle": 14.0},
        {"prepare0": 3.0, "publish0": 4.0, "prepare1": 5.0, "publish1": 6.0, "cycle": 18.0},
    ]

    stats = _summarize_camera_publish_timings(samples)

    assert stats["ros_cam_prepare0_p50_ms"] == 2.0
    assert stats["ros_cam_publish1_p50_ms"] == 5.0
    assert stats["ros_cam_cycle_max_ms"] == 18.0
