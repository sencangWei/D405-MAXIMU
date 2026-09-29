import importlib.util
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


PATH = Path(__file__).resolve().parents[1]/".planning/frontend_pivot_20260929/compare_dense_stereo_translation.py"
SPEC = importlib.util.spec_from_file_location("compare_dense_stereo_translation", PATH)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_dense_stereo_translation_uses_mapped_current_pixels():
    h, w = 20, 20
    y, x = np.mgrid[:h, 1:w]
    key_ids = (y*w+x).ravel()
    current_ids = key_ids-1
    depth = np.full((h, w), 0.3)
    K = np.array([[100., 0., 10.], [0., 100., 10.], [0., 0., 1.]])
    result = mod.translation_observation(key_ids, current_ids, depth, depth, K, Rotation.identity())
    assert result["status"] == "OK" and result["points"] == h*(w-1)
    np.testing.assert_allclose(result["translation_m"], [0.003, 0, 0], atol=1e-12)
    assert result["residual_median_mm"] < 1e-10
    with pytest.raises(ValueError, match="outside"):
        mod.translation_observation(key_ids, current_ids+w*w, depth, depth, K, Rotation.identity())


def test_rotation_witness_requires_independent_stereo_and_spatial_consistency():
    args = (1.5, 0.4, 1.2, 0.9, 1.2, 30000, 1.5)
    assert mod.stereo_imu_rotation_witness(*args)
    for position, bad in ((0, 0.5), (1, 0.9), (2, 0.5), (3, 0.6),
                          (4, 2.0), (5, 100), (6, 5.0)):
        altered = list(args)
        altered[position] = bad
        assert not mod.stereo_imu_rotation_witness(*altered)


def test_pnp_camera_center_uses_inverse_rotation():
    rotation = Rotation.from_euler("z", 90, degrees=True).as_matrix()
    center = np.array([0.02, -0.03, 0.01])
    transform = np.eye(4)
    transform[:3, :3] = rotation
    transform[:3, 3] = -rotation @ center
    np.testing.assert_allclose(mod.camera_center_from_pnp(transform), center)
