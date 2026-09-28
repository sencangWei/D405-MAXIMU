import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".planning/metric_window_bundle_20260928"))
from summarize_native_solver_trace import factor_stats


def test_factor_midpoint_window_and_signed_mean():
    rows = [{"first": 1050, "second": 1060, "residual_mm": [3, 4, 0]},
            {"first": 1070, "second": 1080, "residual_mm": [-3, 4, 0]},
            {"first": 1000, "second": 1001, "residual_mm": [99, 0, 0]}]
    result = factor_stats(rows, "residual_mm", 1053, 1078)
    assert result["count"] == 2
    assert result["median_norm"] == pytest.approx(5)
    assert result["mean_vector"] == pytest.approx([0, 4, 0])
