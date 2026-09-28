import importlib.util
from pathlib import Path

import numpy as np
import pytest

HOOK = (Path(__file__).resolve().parents[1]
        / ".planning/metric_window_bundle_20260928/backend_hook_matches/sitecustomize.py")
spec = importlib.util.spec_from_file_location("backend_match_sample_test", HOOK)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_sample_correspondences_is_deterministic_and_q_gated():
    index_map = np.arange(10)[::-1]
    valid = np.ones(10, dtype=bool)
    confidence = np.arange(10, dtype=float)
    target, source = module.sample_correspondences(index_map, valid, confidence, 3, limit=3)
    assert target.tolist() == [4, 6, 9]
    assert source.tolist() == [5, 3, 0]


def test_malformed_match_shape_fails_closed():
    with pytest.raises(ValueError, match="shapes"):
        module.sample_correspondences([0, 1], [True], [2, 3], 1)
