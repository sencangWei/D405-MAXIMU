import numpy as np

from ego_vio.vio.openvins_ros2_bridge import _rotate_imu_to_vins


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
