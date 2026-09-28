import importlib.util
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


SOURCE = Path(__file__).resolve().parents[1] / ".planning/metric_window_bundle_20260928/compare_metric_boundary.py"
SPEC = importlib.util.spec_from_file_location("compare_metric_boundary_tested", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_window_uses_exposure_times_not_shifted_product_rows():
    raw = np.arange(100, dtype=float) / 30
    product = raw[5:]
    errors = np.arange(5, 100, dtype=float)
    assert MODULE.window_stats(product, errors, raw, 10, 12) == {
        "count": 3, "median_mm": 11.0, "max_mm": 12.0,
    }


def test_missing_timestamp_window_fails_closed():
    raw = np.arange(100, dtype=float) / 30
    with pytest.raises(ValueError, match="no matched poses"):
        MODULE.window_stats(raw[:5], np.zeros(5), raw, 10, 12)


def test_local_displacement_is_body_frame_and_time_matched():
    times = np.array([0.0, 1.0])
    positions = np.array([[0., 0., 0.], [1., 0., 0.]])
    quat = Rotation.from_euler("z", 90, degrees=True).as_quat()
    quaternions = np.stack([quat, quat])
    np.testing.assert_allclose(
        MODULE.local_displacement(times, positions, quaternions, 0.0, 1.0),
        [0., -1., 0.], atol=1e-12,
    )
    with pytest.raises(ValueError, match="not timestamp-matched"):
        MODULE.local_displacement(times, positions, quaternions, 0.1, 1.0)
