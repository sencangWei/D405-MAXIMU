import numpy as np
import pytest

from audit_learned_long_loop_metric import pixels


def test_saved_linear_match_ids_map_to_camera_pixels():
    assert np.array_equal(pixels([0, 511, 512, 147455], (288, 512)),
                          [[0, 0], [511, 0], [0, 1], [511, 287]])


def test_saved_match_id_outside_image_is_rejected():
    with pytest.raises(ValueError, match="outside image"):
        pixels([512], (1, 512))
