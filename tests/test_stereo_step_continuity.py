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
