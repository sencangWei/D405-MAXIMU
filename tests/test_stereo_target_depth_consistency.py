import importlib.util
from pathlib import Path

import numpy as np

SOURCE = Path(__file__).resolve().parents[1] / '.planning/stereo_spatial_repeatability_20260927/target_depth.py'
spec = importlib.util.spec_from_file_location('target_depth', SOURCE)
depth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(depth)


def scene():
    k = np.array([[650., 0, 640], [0, 650., 360], [0, 0, 1]])
    xyz = np.column_stack((np.linspace(-.1, .1, 40), np.zeros(40), np.linspace(.4, .8, 40)))
    uv = xyz[:, :2] / xyz[:, 2, None] * 650 + [640, 360]
    disparity = 650 * .018 / xyz[:, 2]
    return xyz, uv, k, np.zeros(3), np.zeros(3), disparity, np.ones(40, dtype=bool), .018


def test_exact_pair_has_zero_holdout_residual():
    result = depth.target_depth_consistency(*scene())
    assert result['status'] == 'measured'
    assert result['absolute_depth_p95_mm'] < 1e-9
    assert result['absolute_disparity_p95_px'] < 1e-9
    assert result['is_absolute_accuracy'] is False


def test_wrong_source_scale_can_fit_left_but_fail_target_depth():
    args = list(scene())
    args[0] *= 1.1
    result = depth.target_depth_consistency(*args)
    assert result['fitted_left_reprojection_median_px'] < 1e-9
    assert result['absolute_depth_median_mm'] > 50
    assert abs(result['predicted_to_measured_depth_ratio_median'] - 1.1) < 1e-12


def test_missing_target_depth_is_not_zero_error():
    args = list(scene())
    args[6][:] = False
    result = depth.target_depth_consistency(*args)
    assert result['status'] == 'insufficient_target_depth'
    assert 'absolute_depth_p95_mm' not in result
