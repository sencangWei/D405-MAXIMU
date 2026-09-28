import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".planning/metric_window_bundle_20260928"))
from scan_relative_disagreement import scan


def test_scan_keeps_all_valid_fixed_schedule_windows():
    rows = [{"first": index, "second": index + 1,
             "residual_mm": [1 if index < 100 else 2, 0, 0]}
            for index in range(40, 127, 5)]
    result = scan(rows, 100, 100, 5)
    assert len(result) == 1
    assert result[0]["ratio"] == pytest.approx(2)


def test_insufficient_coverage_is_unknown_not_zero():
    assert scan([], 100, 100, 5) == []
