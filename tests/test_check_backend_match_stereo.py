"""Boundaries of the observation-only accepted-edge stereo diagnostic."""
import importlib.util
from pathlib import Path

import numpy as np


MODULE = Path(__file__).resolve().parents[1] / ".planning/metric_window_bundle_20260928/check_backend_match_stereo.py"
spec = importlib.util.spec_from_file_location("check_backend_match_stereo", MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_insufficient_metric_depth_is_unknown_not_zero_error():
    transform, report = module.check_direction(
        np.arange(16), np.arange(16), np.full((4, 4), np.nan),
        (4, 4), (4, 4), np.eye(3))
    assert transform is None
    assert report["reason"] == "insufficient_metric_correspondences"
    assert report["metric_depth_matches"] == 0


def test_reject_out_of_range_sample_pixel():
    try:
        module.check_direction([16], [0], np.ones((4, 4)),
                               (4, 4), (4, 4), np.eye(3))
    except ValueError as error:
        assert "outside frame" in str(error)
    else:
        raise AssertionError("invalid pixel ID must fail")
