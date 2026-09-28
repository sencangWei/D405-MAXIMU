import importlib.util
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "stereo_window_shape_graph", ROOT / "scripts" / "stereo_window_shape_graph.py"
)
graph = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(graph)


def factor(reference=None, sensitivity=None, affine=None):
    if reference is None:
        reference = straight_positions()[1:].reshape(-1)
    if sensitivity is None:
        sensitivity = np.eye(24)
    if affine is None:
        affine = np.zeros(np.asarray(sensitivity).shape[0])
    return {
        "diagnostic_only": True,
        "available": True,
        "solver_accepted": True,
        "not_admissible_for_graph": True,
        "frames": 9,
        "gauge": "all_relative_centers_mapped_through_first_camera_rotation",
        "compact_sqrt_sensitivity": np.asarray(sensitivity, dtype=float).tolist(),
        "reference_center_vector_m": np.asarray(reference, dtype=float).tolist(),
        "affine_offset": np.asarray(affine, dtype=float).tolist(),
        "metadata": {
            "calibrated_covariance": False,
            "statistical_independence_claimed": False,
            "available_for_graph": False,
        },
    }


def straight_positions():
    return np.column_stack((np.linspace(0.0, 0.08, 9), np.zeros(9), np.zeros(9)))


def bowed_positions():
    points = straight_positions()
    points[1:8, 1] = [0.003, 0.008, 0.012, 0.0, -0.009, -0.005, -0.002]
    points[4] = straight_positions()[4]
    points[8] = straight_positions()[8]
    return points


def build(positions, *, rotations=None, scale=False, sensitivity=None):
    rotations = Rotation.identity(len(positions)) if rotations is None else rotations
    columns = {index: 3 * index for index in range(9)}
    total = 28 if scale else 27
    return graph.build_shape_group_row(
        factor(sensitivity=sensitivity),
        list(range(9)),
        positions,
        rotations,
        columns,
        total_columns=total,
        scale_column=27 if scale else None,
    )


def physical_residual(row, state):
    return graph.evaluate_shape_group_state(row, state)["residual"]


def explicit_physical_residual(row, state):
    positions = np.asarray(row["_base_positions"], dtype=float).copy()
    base = positions.copy()
    for local, start in enumerate(row["columns"]):
        positions[local] += state[start:start + 3]
    if row["scale_column"] is not None:
        positions += state[row["scale_column"]] * (base - base[0])
    local = row["_first_rotation"].inv().apply(positions[1:] - positions[0]).reshape(-1)
    return row["_affine_offset"] + row["_compact_sqrt_sensitivity"] @ (
        local - row["_reference_center_vector_m"]
    )


def test_all_nine_nodes_added_to_correction_union_without_dropping_existing():
    nodes = {99}
    returned = graph.add_shape_group_nodes([0, 1, 2, 3, 4, 5, 6, 7, 8], nodes)
    assert returned is nodes
    assert nodes == {0, 1, 2, 3, 4, 5, 6, 7, 8, 99}


def test_bent_interior_has_zero_endpoint_edges_but_nonzero_group_and_ls_reduces_bow():
    row = build(bowed_positions())
    endpoint_0_4 = bowed_positions()[4] - bowed_positions()[0] - (
        straight_positions()[4] - straight_positions()[0]
    )
    endpoint_4_8 = bowed_positions()[8] - bowed_positions()[4] - (
        straight_positions()[8] - straight_positions()[4]
    )
    np.testing.assert_allclose(endpoint_0_4, np.zeros(3))
    np.testing.assert_allclose(endpoint_4_8, np.zeros(3))
    assert np.linalg.norm(row["residual"]) > 0.015
    assert row["jacobian"].shape == (24, 27)
    assert len(row["node_indices"]) == 9

    correction, *_ = np.linalg.lstsq(row["jacobian"].toarray(), -row["residual"], rcond=None)
    reduced = physical_residual(row, correction)
    assert np.linalg.norm(reduced) < 1e-10
    assert np.linalg.norm(correction.reshape(9, 3)[1:8, 1]) > 0.010


def test_global_rigid_frame_invariance():
    local = build(bowed_positions())
    basis = Rotation.from_rotvec([0.2, -0.1, 0.05])
    translation = np.array([0.4, -0.2, 0.1])
    world_positions = basis.apply(bowed_positions()) + translation
    world_rotations = basis * Rotation.identity(9)
    world = build(world_positions, rotations=world_rotations)
    np.testing.assert_allclose(world["residual"], local["residual"], atol=1e-12)
    state = np.zeros(27)
    state[3:6] = [0.001, -0.002, 0.003]
    state[12:15] = [-0.002, 0.004, -0.001]
    world_state = state.copy()
    for node in range(9):
        world_state[3 * node:3 * node + 3] = basis.apply(state[3 * node:3 * node + 3])
    np.testing.assert_allclose(
        physical_residual(world, world_state) - world["residual"],
        physical_residual(local, state) - local["residual"],
        atol=1e-12,
    )


def test_jacobian_matches_physical_finite_difference_including_scale_and_common_translation_zero():
    base = bowed_positions()
    basis = Rotation.from_rotvec([0.1, -0.2, 0.07])
    positions = basis.apply(base) + np.array([0.3, -0.2, 0.4])
    rotations = basis * Rotation.identity(9)
    rng = np.random.default_rng(7)
    sensitivity = rng.normal(0.0, 0.4, (24, 24))
    sensitivity += np.eye(24)
    affine = rng.normal(0.0, 0.1, 24)
    columns = {index: 3 * index for index in range(9)}
    row = graph.build_shape_group_row(
        factor(sensitivity=sensitivity, affine=affine),
        list(range(9)), positions, rotations, columns, total_columns=28, scale_column=27,
    )
    rng = np.random.default_rng(42)
    state = rng.normal(0.0, 0.002, 28)
    state[27] = 0.03
    analytic = row["jacobian"].toarray()
    eps = 1e-7
    for column in [0, 1, 7, 12, 23, 26, 27]:
        plus = state.copy()
        minus = state.copy()
        plus[column] += eps
        minus[column] -= eps
        finite = (physical_residual(row, plus) - physical_residual(row, minus)) / (2 * eps)
        np.testing.assert_allclose(finite, analytic[:, column], atol=1e-9)
    np.testing.assert_allclose(physical_residual(row, state), explicit_physical_residual(row, state), atol=1e-12)

    common = np.tile([0.01, -0.02, 0.03], 9)
    np.testing.assert_allclose(row["jacobian"][:, :27] @ common, np.zeros(24), atol=1e-12)
    assert np.linalg.norm(analytic[:, 27]) > 0.0
    final = graph.evaluate_shape_group_state(row, state)
    assert final["group_residual_norm_final"] == pytest.approx(np.linalg.norm(physical_residual(row, state)))
    assert final["max_local_node_delta_from_initial_m"] > 0.0
    assert final["max_local_node_delta_from_ba_m"] > 0.0
    common_state = np.zeros(28)
    common_state[:27] = common
    common_final = graph.evaluate_shape_group_state(row, common_state)
    assert common_final["max_local_node_delta_from_initial_m"] == pytest.approx(0.0, abs=1e-12)


def test_missing_interior_node_rejects_instead_of_interpolating():
    columns = {index: 3 * index for index in range(9)}
    del columns[5]
    with pytest.raises(ValueError, match="all nine"):
        graph.build_shape_group_row(
            factor(),
            list(range(9)),
            straight_positions(),
            Rotation.identity(9),
            columns,
            total_columns=27,
        )


def test_strict_shape_index_and_flag_guards():
    with pytest.raises(ValueError, match="exactly nine"):
        graph.add_shape_group_nodes([0, 1, 2], set())
    with pytest.raises(ValueError, match="strictly ordered"):
        graph.add_shape_group_nodes([0, 1, 1, 3, 4, 5, 6, 7, 8], set())
    with pytest.raises(ValueError, match="nonnegative"):
        graph.add_shape_group_nodes([-1, 0, 1, 2, 3, 4, 5, 6, 7], set())
    with pytest.raises(ValueError, match="overlap"):
        graph.build_shape_group_row(
            factor(), list(range(9)), straight_positions(), Rotation.identity(9),
            {index: index for index in range(9)}, total_columns=27
        )
    with pytest.raises(ValueError, match="scale column"):
        graph.build_shape_group_row(
            factor(), list(range(9)), straight_positions(), Rotation.identity(9),
            {index: 3 * index for index in range(9)}, total_columns=27, scale_column=3
        )
    bad = factor()
    bad["metadata"]["calibrated_covariance"] = True
    with pytest.raises(ValueError, match="covariance"):
        graph.build_shape_group_row(
            bad, list(range(9)), straight_positions(), Rotation.identity(9),
            {index: 3 * index for index in range(9)}, total_columns=27
        )
    bad = factor()
    bad["solver_accepted"] = False
    with pytest.raises(ValueError, match="accepted solver"):
        graph.build_shape_group_row(
            bad, list(range(9)), straight_positions(), Rotation.identity(9),
            {index: 3 * index for index in range(9)}, total_columns=27
        )
    bad = factor()
    bad.pop("available")
    with pytest.raises(ValueError, match="available"):
        graph.build_shape_group_row(
            bad, list(range(9)), straight_positions(), Rotation.identity(9),
            {index: 3 * index for index in range(9)}, total_columns=27
        )
    bad = factor()
    bad["frames"] = 8
    with pytest.raises(ValueError, match="nine frames"):
        graph.build_shape_group_row(
            bad, list(range(9)), straight_positions(), Rotation.identity(9),
            {index: 3 * index for index in range(9)}, total_columns=27
        )
    bad = factor()
    bad["frames"] = 9.0
    with pytest.raises(ValueError, match="nine frames"):
        graph.build_shape_group_row(
            bad, list(range(9)), straight_positions(), Rotation.identity(9),
            {index: 3 * index for index in range(9)}, total_columns=27
        )
    bad = factor()
    bad["frames"] = True
    with pytest.raises(ValueError, match="nine frames"):
        graph.build_shape_group_row(
            bad, list(range(9)), straight_positions(), Rotation.identity(9),
            {index: 3 * index for index in range(9)}, total_columns=27
        )
    bad = factor()
    bad["gauge"] = "wrong"
    with pytest.raises(ValueError, match="gauge"):
        graph.build_shape_group_row(
            bad, list(range(9)), straight_positions(), Rotation.identity(9),
            {index: 3 * index for index in range(9)}, total_columns=27
        )
    with pytest.raises(ValueError, match="rotations"):
        graph.build_shape_group_row(
            factor(), list(range(9)), straight_positions(), Rotation.identity(),
            {index: 3 * index for index in range(9)}, total_columns=27
        )
    bad = factor(sensitivity=np.eye(23, 24))
    bad["affine_offset"] = [0.0] * 24
    with pytest.raises(ValueError, match="affine offset"):
        graph.build_shape_group_row(
            bad, list(range(9)), straight_positions(), Rotation.identity(9),
            {index: 3 * index for index in range(9)}, total_columns=27
        )


def test_rank_zero_empty_shape_group_smoke():
    row = graph.build_shape_group_row(
        factor(sensitivity=np.zeros((0, 24)), affine=np.zeros(0)),
        list(range(9)),
        straight_positions(),
        Rotation.identity(9),
        {index: 3 * index for index in range(9)},
        total_columns=27,
    )
    assert row["jacobian"].shape == (0, 27)
    assert row["residual"].shape == (0,)
    final = graph.evaluate_shape_group_state(row, np.zeros(27))
    assert final["residual"].shape == (0,)
    assert final["group_residual_norm_final"] == 0.0
