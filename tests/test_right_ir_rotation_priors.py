import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from scripts.prepare_mast3r_imu_rotation_priors import body_t_camera_for_stream


def test_right_ir_imu_prior_uses_factory_rigid_transform():
    body_t_left = np.eye(4)
    factory = {
        "right_rotation_from_left": Rotation.from_euler(
            "y", 0.05, degrees=True
        ).as_matrix(),
        "right_translation_from_left_m": np.array([-0.018083254, 0.0, 0.0]),
    }

    body_t_right = body_t_camera_for_stream(body_t_left, factory, "infrared_right")
    right_t_left = np.eye(4)
    right_t_left[:3, :3] = factory["right_rotation_from_left"]
    right_t_left[:3, 3] = factory["right_translation_from_left_m"]

    np.testing.assert_allclose(body_t_right @ right_t_left, body_t_left, atol=1e-12)
    assert body_t_right[0, 3] == pytest.approx(0.018083254, abs=1e-8)
