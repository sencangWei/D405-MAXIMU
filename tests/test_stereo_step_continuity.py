import importlib.util
from pathlib import Path

import numpy as np


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "align_mast3r_scale_with_stereo.py"
SPEC = importlib.util.spec_from_file_location(SOURCE.stem, SOURCE)
stereo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stereo)


def test_rejects_isolated_metric_jump():
    positions = np.column_stack((np.arange(90) * 0.002, np.zeros(90), np.zeros(90)))
    positions[50, 1] = 0.055

    quality = stereo.trajectory_step_continuity(positions, 1.0)

    assert quality["result"] == "FAIL"
    assert quality["jump_count"] == 2
    assert quality["max_step_m"] > 0.05


def test_keeps_sustained_fast_motion():
    positions = np.column_stack((np.arange(90) * 0.032, np.zeros(90), np.zeros(90)))

    quality = stereo.trajectory_step_continuity(positions, 1.0)

    assert quality["result"] == "PASS"
    assert quality["jump_count"] == 0


def test_rejects_nonfinite_positions():
    positions = np.array([[0.0, 0.0, 0.0], [float("nan"), 0.0, 0.0]])

    quality = stereo.trajectory_step_continuity(positions, 1.0)

    assert quality["result"] == "FAIL"
    assert quality["reason"] == "invalid_trajectory"


def test_long_observation_gap_is_reported_not_misread_as_one_frame_jump():
    times = np.arange(90, dtype=float) / 30.0
    times[50:] += 22.0 / 30.0
    positions = np.column_stack((times * 0.20, np.zeros(90), np.zeros(90)))

    quality = stereo.trajectory_step_continuity(positions, 1.0, times)

    assert quality["result"] == "PASS"
    assert quality["unverified_gap_count"] == 1
    assert quality["max_step_m"] > 0.14
    assert quality["max_contiguous_step_m"] < 0.01


def test_real_jump_next_to_observation_gap_is_still_rejected():
    times = np.arange(90, dtype=float) / 30.0
    times[50:] += 22.0 / 30.0
    positions = np.column_stack((times * 0.20, np.zeros(90), np.zeros(90)))
    positions[55, 1] = 0.055

    quality = stereo.trajectory_step_continuity(positions, 1.0, times)

    assert quality["result"] == "FAIL"
    assert quality["jump_count"] == 2
    assert quality["unverified_gap_count"] == 1
