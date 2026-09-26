import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_lighthouse_handeye_rotation import axis_mapping, integrated_rotations, rotation_difference


def test_constant_coordinate_change_is_not_sensor_error():
    vectors = np.array([[.1, 0, 0], [0, .2, 0], [0, 0, .3], [.2, .1, -.1]])
    expected = Rotation.from_euler("xyz", [30, -15, 80], degrees=True)
    fitted, errors = axis_mapping(vectors, expected.apply(vectors))
    assert rotation_difference(expected, fitted) < 1e-10
    assert errors["max"] < 1e-10


def test_gyro_integration_composes_in_body_frame():
    times = np.arange(5.)
    gyro = np.array([[1., 0, 0], [1., 0, 0], [0, 0, 1.], [0, 0, 1.], [0, 0, 1.]])
    expected = Rotation.identity()
    for a, b in zip(gyro[:-1], gyro[1:]):
        expected = expected * Rotation.from_rotvec((a + b) / 2)
    actual = integrated_rotations(times, gyro)(times[-1])
    assert rotation_difference(expected, actual) < 1e-10
    assert rotation_difference(Rotation.from_rotvec(np.sum((gyro[:-1] + gyro[1:]) / 2, axis=0)), actual) > 1.
