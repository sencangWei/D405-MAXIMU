from types import SimpleNamespace

import cv2
import numpy as np

from ego_vio.imu.imu_reader import ImuSample
from scripts.collect_calib_data import AprilGridPoseTracker, StageQuality


def _sample(ts, accel):
    return ImuSample(
        ts=ts,
        counter=int(ts * 400),
        gx=0.0,
        gy=0.0,
        gz=0.0,
        ax=float(accel[0]),
        ay=float(accel[1]),
        az=float(accel[2]),
        temp=25.0,
        rx_time=ts,
    )


def test_static_gravity_does_not_count_as_imu_excitation():
    quality = StageQuality()
    for index in range(100):
        quality.feed_imu(_sample(index / 400.0, (0.0, 0.0, 1.0)))

    assert quality.accel_std == 0.0


def test_rotation_changes_axis_components_even_when_norm_stays_one_g():
    quality = StageQuality()
    for index, angle in enumerate(np.linspace(0.0, np.pi / 2.0, 100)):
        accel = (np.sin(angle), 0.0, np.cos(angle))
        quality.feed_imu(_sample(index / 400.0, accel))

    assert quality.accel_std > 0.05


def _project_grid_detections(tracker, rvec, tvec):
    detections = []
    for tag_id in range(36):
        corners, _ = cv2.projectPoints(
            tracker._tag_corners_3d(tag_id), rvec, tvec, tracker.K, tracker.D
        )
        detections.append(SimpleNamespace(
            tag_id=tag_id,
            corners=corners.reshape(4, 2).astype(np.float32),
        ))
    return detections


def test_pnp_translation_uses_camera_axes_and_peak_to_peak_span():
    grid = {"tagCols": 6, "tagRows": 6, "tagSize": 0.0352, "tagSpacing": 0.3}
    K = np.array([
        [650.0, 0.0, 640.0],
        [0.0, 650.0, 360.0],
        [0.0, 0.0, 1.0],
    ])
    tracker = AprilGridPoseTracker(object(), grid, K, np.zeros(4), track_camera_motion=True)
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    # Rotate the board frame 90 degrees relative to the camera. This proves
    # the reported translation is expressed in the initial camera frame,
    # rather than silently inheriting the board's X/Y orientation.
    rvec = np.array([[0.0], [0.0], [np.pi / 2.0]], dtype=np.float64)
    rotation, _ = cv2.Rodrigues(rvec)
    initial_tvec = np.array([[0.0], [0.0], [1.0]])
    initial_center = -rotation.T @ initial_tvec

    # Camera center moves from X=0 to +50 mm, then to -50 mm. The old
    # max-from-start metric reports only 50 mm; peak-to-peak coverage is 100 mm.
    for camera_x in (0.0, 0.05, -0.05):
        displacement_in_initial_camera = np.array([[camera_x], [0.0], [0.0]])
        camera_center = initial_center + rotation.T @ displacement_in_initial_camera
        tvec = -rotation @ camera_center
        detections = _project_grid_detections(tracker, rvec, tvec)
        assert tracker.feed_detections(image, detections)

    np.testing.assert_allclose(tracker.max_t, [0.05, 0.0, 0.0], atol=1e-5)
    np.testing.assert_allclose(tracker.span_t, [0.10, 0.0, 0.0], atol=1e-5)


def test_stage_gate_uses_coverage_span_instead_of_total_path_or_net_offset():
    tracker = SimpleNamespace(
        span_t=np.array([0.10, 0.0, 0.0]),
        current_t=np.zeros(3),
        path_length=1.0,
        max_r=np.zeros(3),
    )
    quality = StageQuality(pose_tracker=tracker)
    quality.pose_ok_frames = 3
    quality.tag_counts = [36] * 10
    quality.max_accel_std = 0.03

    ok, failures, messages = quality.check({"tags_min": 6, "tx": 0.08, "imu_excite": 0.02})

    assert ok
    assert not failures
    assert any("X=100.0mm" in message for message in messages)
