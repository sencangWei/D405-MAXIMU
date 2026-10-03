import importlib.util
from pathlib import Path

import numpy as np
import pytest

BASE = Path(__file__).resolve().parents[1] / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
spec = importlib.util.spec_from_file_location("joint_metric_native_solver", BASE / "joint_metric_native_solver.py")
solver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(solver)


def test_native_blocks_accumulate_both_directions_and_pin0():
    blocks = np.zeros((4, 2, 7, 7))
    for edge in range(2):
        blocks[0, edge] = blocks[3, edge] = np.eye(7)
        blocks[1, edge] = blocks[2, edge] = -np.eye(7)
    gradients = np.zeros((2, 2, 7))
    gradients[0, 0] = -1
    gradients[1, 0] = 1
    H, g = solver.assemble_native_blocks(blocks, gradients, [0, 1], [1, 0], 2)
    np.testing.assert_array_equal(H[:7, :7], 2 * np.eye(7))
    np.testing.assert_array_equal(H[:7, 7:], -2 * np.eye(7))
    dx = solver.pinned_increment(H, g)
    np.testing.assert_array_equal(dx[0], 0)
    np.testing.assert_allclose(dx[1], -.5, atol=2e-16, rtol=0)


def test_sign_reduces_convex_normal_objective():
    H, g = np.eye(21), np.arange(21, dtype=float)
    dx = solver.pinned_increment(H, g).reshape(-1)
    assert .5 * dx @ H @ dx + g @ dx < 0
    assert not np.any(dx[:7])


def test_no_silent_zero_solution_for_singular_or_invalid_input():
    with pytest.raises(np.linalg.LinAlgError):
        solver.pinned_increment(np.zeros((14, 14)), np.ones(14))
    with pytest.raises(ValueError):
        solver.assemble_native_blocks(np.zeros((4, 1, 7, 7)), np.zeros((2, 1, 7)), [2], [1], 2)
    with pytest.raises(ValueError):
        solver.pinned_increment(np.eye(14), np.full(14, np.nan))
