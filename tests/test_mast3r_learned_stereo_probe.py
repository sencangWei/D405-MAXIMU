import numpy as np
import pytest

from scripts.probe_mast3r_learned_stereo import metric_matches


def test_metric_matches_rejects_wrong_epipolar_or_disparity():
    left = np.array([[30, 10], [30, 10], [30, 10], [30, 10]])
    right = np.array([[20, 10], [20, 14], [31, 10], [29.9, 10]])
    valid, depth, epipolar = metric_matches(left, right, 260, 0.018)

    np.testing.assert_array_equal(valid, [True, False, False, False])
    assert depth[0] == pytest.approx(0.468)
    assert np.isnan(depth[1:]).all()
    assert epipolar[1] == pytest.approx(4.0)
