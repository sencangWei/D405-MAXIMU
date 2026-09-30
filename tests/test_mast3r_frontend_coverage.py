"""Production fusion must not accept a visual trajectory with an unobserved tail."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "convert_mast3r_slam_trajectory",
    ROOT / "scripts" / "convert_mast3r_slam_trajectory.py",
)
convert = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(convert)


def pose_rows(times):
    return np.column_stack((times, np.zeros((len(times), 7))))


def test_rejects_lost_tail():
    times = np.arange(1200, dtype=float) / 30.0
    with pytest.raises(ValueError, match="959/1200.*longest untracked run 241"):
        convert.require_complete_visual_coverage(pose_rows(times[:959]), times)


def test_allows_two_frame_gap():
    times = np.arange(300, dtype=float) / 30.0
    convert.require_complete_visual_coverage(pose_rows(np.delete(times, [35, 36])), times)


def test_rejects_internal_gap():
    times = np.arange(300, dtype=float) / 30.0
    with pytest.raises(ValueError, match="longest untracked run 3"):
        convert.require_complete_visual_coverage(
            pose_rows(np.delete(times, [35, 36, 37])), times
        )
