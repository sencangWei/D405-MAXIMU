import importlib.util
from pathlib import Path

import numpy as np
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "smooth_pose_trajectory.py"
SPEC = importlib.util.spec_from_file_location("smooth_pose_trajectory", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_smooth_positions_reduces_isolated_spike():
    positions = np.column_stack((np.arange(9), np.zeros(9), np.zeros(9)))
    positions[4, 1] = 0.02
    smoothed = MODULE.smooth_positions(positions, 5, 2)
    assert abs(smoothed[4, 1]) < abs(positions[4, 1])
    np.testing.assert_allclose(smoothed[:, 0], positions[:, 0], atol=1e-12)


def test_gaussian_smoothing_reduces_isolated_spike():
    times = np.arange(9, dtype=float) / 30.0
    positions = np.column_stack((np.arange(9), np.zeros(9), np.zeros(9)))
    positions[4, 1] = 0.02

    smoothed, sigma_samples, gap_count = MODULE.gaussian_smooth_positions(
        times, positions, 0.05
    )

    assert sigma_samples == pytest.approx(1.5)
    assert gap_count == 0
    assert abs(smoothed[4, 1]) < abs(positions[4, 1])


def test_gaussian_smoothing_keeps_missing_frame_gap_separate():
    times = np.r_[np.arange(5) / 30.0, 1.0 + np.arange(5) / 30.0]
    positions = np.column_stack(
        (np.r_[np.zeros(5), np.ones(5)], np.zeros(10), np.zeros(10))
    )

    smoothed, _sigma_samples, gap_count = MODULE.gaussian_smooth_positions(
        times, positions, 0.05
    )

    assert gap_count == 1
    np.testing.assert_allclose(smoothed, positions)


def test_gaussian_smoothing_rejects_irregular_non_gap_timestamps():
    times = np.array([0.0, 0.033, 0.076, 0.109, 0.142])
    with pytest.raises(ValueError, match="near-uniform"):
        MODULE.gaussian_smooth_positions(times, np.zeros((5, 3)), 0.05)


@pytest.mark.parametrize("window", [0, 2, 4])
def test_smooth_positions_rejects_invalid_window(window):
    with pytest.raises(ValueError):
        MODULE.smooth_positions(np.zeros((9, 3)), window, 2)
