import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".planning/metric_window_bundle_20260928"))
from inspect_frozen_graph_corrections import correction_stats, stereo_stats


def test_correction_stats_keeps_signed_endpoint_change():
    times = np.arange(1121, dtype=float)
    original = np.zeros((1121, 3))
    updated = original.copy()
    updated[1053:1079, 0] = np.linspace(0.001, 0.003, 26)
    values = correction_stats(times, original, times, updated)
    assert values["bad_index_band"]["count"] == 26
    assert values["bad_index_band"]["endpoint_change_vector_mm"] == pytest.approx([2, 0, 0])
    assert values["bad_index_band"]["max_norm_mm"] == pytest.approx(3)


def test_shifted_times_fail_closed():
    times = np.arange(1121, dtype=float)
    positions = np.zeros((1121, 3))
    with pytest.raises(ValueError, match="frame-matched"):
        correction_stats(times, positions, times + 1, positions)


def test_stereo_consistency_uses_rotated_metric_measurement():
    positions = np.zeros((1121, 3))
    updated = positions.copy()
    updated[1054, 1] = 0.002
    quaternions = np.tile([0, 0, np.sin(np.pi / 4), np.cos(np.pi / 4)], (1121, 1))
    item = {"accepted": True, "first_index": 1053, "second_index": 1054,
            "metric_displacement_camera_i_m": [0.002, 0, 0]}
    result = stereo_stats([item], positions, quaternions, updated, quaternions)
    assert result["windows"]["bad_index_band"]["count"] == 1
    assert result["windows"]["bad_index_band"]["before_median_norm_mm"] == pytest.approx(2)
    assert result["windows"]["bad_index_band"]["after_median_norm_mm"] == pytest.approx(0)
