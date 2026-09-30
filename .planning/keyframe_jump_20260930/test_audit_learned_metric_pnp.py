import numpy as np
from scipy.spatial.transform import Rotation

from audit_learned_metric_pnp import backproject, metric_pnp, pose_difference


def test_metric_pnp_recovers_known_relative_camera_pose():
    rng = np.random.default_rng(7)
    source = rng.uniform((-0.2, -0.15, 0.5), (0.2, 0.15, 1.1), size=(200, 3))
    rotation = Rotation.from_euler("xyz", (0.02, -0.03, 0.04)).as_matrix()
    translation = np.array((0.015, -0.008, 0.003))
    target = source @ rotation.T + translation
    intrinsics = np.array(((300.0, 0.0, 256.0), (0.0, 300.0, 144.0), (0.0, 0.0, 1.0)))
    pixels = np.column_stack((target[:, 0] / target[:, 2] * 300.0 + 256.0,
                              target[:, 1] / target[:, 2] * 300.0 + 144.0))
    estimated, report = metric_pnp(source, pixels, intrinsics)
    expected = np.eye(4)
    expected[:3, :3], expected[:3, 3] = rotation, translation
    assert report["inliers"] == len(source)
    assert report["pixel_median"] < 1e-3
    assert pose_difference(estimated, expected)["translation_mm"] < 0.01
    source_pixels = np.column_stack((source[:, 0] / source[:, 2] * 300.0 + 256.0,
                                     source[:, 1] / source[:, 2] * 300.0 + 144.0))
    assert np.allclose(backproject(source_pixels, source[:, 2], intrinsics), source)
