"""The diagnostic inlier filter handles missing depth without inventing errors."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest


MODULE = Path(__file__).resolve().parents[1] / ".planning/metric_window_bundle_20260928/edge_inlier_probe/sitecustomize.py"
spec = importlib.util.spec_from_file_location("edge_inlier_probe", MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_rejected_directions_are_from_geometry_only():
    report = {"schema": "accepted_backend_edge_stereo_check_v1", "results": [
        {"first_raw": 2, "second_raw": 5, "accepted": False,
         "forward": {"accepted": True}, "reverse": {"accepted": False}},
        {"first_raw": 5, "second_raw": 6, "accepted": True,
         "forward": {"accepted": True}, "reverse": {"accepted": True}}]}
    assert module.failed_directions(report) == {(2, 5): (False, True)}
    with pytest.raises(ValueError, match="schema"):
        module.failed_directions({"schema": "ground_truth", "results": []})


def test_no_stereo_depth_masks_nothing():
    mapping = np.arange(16)
    rejected, info = module.pnp_discordant_pixels(
        mapping, np.ones(16, bool), np.full(16, 3.0),
        np.full((4, 4), np.nan), (4, 4), (4, 4), np.eye(3), 1.5)
    assert len(rejected) == 0
    assert info["reason"] == "too_few_depth_matches"
