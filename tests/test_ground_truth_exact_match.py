"""Gap gates constrain interpolation, not already-observed exact timestamps."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from evaluate_slam_ground_truth import interpolate_ground_truth


def test_exact_observations_survive_long_neighbor_gaps():
    times = np.array([0.0, 1.0, 3.0])
    positions = np.column_stack([times, times * 0, times * 0])
    quaternions = np.tile([0, 0, 0, 1], (3, 1))
    inside, valid, pose, quaternion = interpolate_ground_truth(
        times, times, positions, quaternions, 0.05)
    assert inside.all() and valid.all()
    assert np.array_equal(pose[:, 1:], positions)
    assert np.allclose(quaternion, quaternions)


def test_genuine_missing_timestamps_still_obey_interpolation_gap():
    gt = np.array([0.0, 0.02, 1.0])
    query = np.array([0.0, 0.01, 0.5, 1.0])
    positions = np.column_stack([gt, gt * 0, gt * 0])
    quaternions = np.tile([0, 0, 0, 1], (3, 1))
    inside, valid, pose, _ = interpolate_ground_truth(query, gt, positions, quaternions, 0.05)
    assert inside.all()
    assert valid.tolist() == [True, True, False, True]
    assert np.allclose(pose[:, 0], [0, 0.01, 1])
