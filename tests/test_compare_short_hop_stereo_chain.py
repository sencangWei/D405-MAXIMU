import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/".planning/frontend_pivot_20260929"))
from compare_short_hop_stereo_chain import common_current_matches, integrate_pairs


def test_common_keyframe_pixels_keep_both_current_image_coordinates():
    common, left, right = common_current_matches(
        np.array([2, 5, 9]), np.array([20, 50, 90]),
        np.array([1, 5, 9]), np.array([10, 55, 99]))
    np.testing.assert_array_equal(common, [5, 9])
    np.testing.assert_array_equal(left, [50, 90])
    np.testing.assert_array_equal(right, [55, 99])


def test_short_hop_translations_compose_in_start_camera_frame():
    rotation = Rotation.from_euler("z", 90, degrees=True)
    poses = [Rotation.identity(), rotation, rotation]
    result = integrate_pairs(0, 2, poses, {0: [1, 0, 0], 1: [1, 0, 0]})
    np.testing.assert_allclose(result, [1, 1, 0], atol=1e-12)
