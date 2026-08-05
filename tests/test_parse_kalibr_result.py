import numpy as np

from scripts.parse_kalibr_result import _to_openvins


def test_openvins_uses_kalibr_time_shift_without_sign_flip():
    cam = {
        "T_cam_imu": np.eye(4).tolist(),
        "timeshift_cam_imu": -0.0125,
        "camera_model": "pinhole",
        "distortion_model": "radtan",
        "resolution": [640, 480],
        "fx": 400.0,
        "fy": 400.0,
        "cx": 320.0,
        "cy": 240.0,
        "distortion_coeffs": [0.0, 0.0, 0.0, 0.0],
    }
    imu = {
        "accelerometer_noise_density": 1e-3,
        "accelerometer_random_walk": 1e-4,
        "gyroscope_noise_density": 1e-4,
        "gyroscope_random_walk": 1e-5,
        "update_rate": 400.0,
    }

    parsed = _to_openvins(cam, imu)

    assert parsed["imu"]["time_offset"] == -0.0125


def test_openvins_camera_to_imu_transform_is_inverse_of_kalibr():
    T_cam_imu = np.eye(4)
    T_cam_imu[:3, 3] = [0.01, -0.02, 0.03]
    cam = {
        "T_cam_imu": T_cam_imu.tolist(),
        "timeshift_cam_imu": 0.0,
        "camera_model": "pinhole",
        "distortion_model": "radtan",
        "resolution": [640, 480],
        "fx": 400.0,
        "fy": 400.0,
        "cx": 320.0,
        "cy": 240.0,
        "distortion_coeffs": [0.0, 0.0, 0.0, 0.0],
    }
    imu = {
        "accelerometer_noise_density": 1e-3,
        "accelerometer_random_walk": 1e-4,
        "gyroscope_noise_density": 1e-4,
        "gyroscope_random_walk": 1e-5,
        "update_rate": 400.0,
    }

    parsed = _to_openvins(cam, imu)

    assert np.allclose(parsed["camera"]["T_imu_cam"], np.linalg.inv(T_cam_imu))
