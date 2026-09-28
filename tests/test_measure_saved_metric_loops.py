import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".planning" / "metric_window_bundle_20260928"))
from measure_saved_metric_loops import (
    measured_relative_camera_i,
    visual_relative_camera_i,
    visual_relative_rotation,
)


def test_pnp_source_to_target_translation_is_target_centre_in_source():
    rotation = Rotation.from_euler("z", 90, degrees=True).as_matrix()
    target_centre_in_source = np.array([0.2, -0.1, 0.05])
    transform = np.eye(4)
    transform[:3, :3] = rotation
    transform[:3, 3] = -rotation @ target_centre_in_source
    np.testing.assert_allclose(measured_relative_camera_i(transform), target_centre_in_source)


def test_visual_displacement_rotated_to_first_camera_frame_and_scaled():
    rotation = Rotation.from_euler("z", 90, degrees=True)
    x, y, z, w = rotation.as_quat()
    rows = [
        dict(x="0", y="0", z="0", qw=str(w), qx=str(x), qy=str(y), qz=str(z)),
        dict(x="0", y="2", z="0", qw="1", qx="0", qy="0", qz="0"),
    ]
    np.testing.assert_allclose(visual_relative_camera_i(rows, 0, 1, 0.5), [1, 0, 0], atol=1e-15)
    np.testing.assert_allclose(
        visual_relative_rotation(rows, 0, 1).as_matrix(), rotation.as_matrix()
    )
