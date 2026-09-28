"""Diagnostic-only correlated graph row for stereo-window shape factors.

This prototype does not modify native fusion and does not define a production
weight, covariance, or independent edge family. It converts one validated
9-center shape diagnostic into a single affine residual block over all nine
camera-translation graph nodes, preserving the compact sensitivity correlations.
"""
from __future__ import annotations

import numpy as np
from scipy.sparse import csr_matrix
from scipy.spatial.transform import Rotation


def add_shape_group_nodes(indices, correction_nodes: set[int]) -> set[int]:
    """Validate a 9-node group and add every node to the correction set."""
    group = _validate_indices(indices)
    if not isinstance(correction_nodes, set):
        raise ValueError("correction node union must be a set")
    correction_nodes.update(group)
    return correction_nodes


def _validate_indices(indices) -> list[int]:
    if not isinstance(indices, (list, tuple)) or len(indices) != 9:
        raise ValueError("shape group requires exactly nine indices")
    group = []
    for value in indices:
        if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
            raise ValueError("shape group indices must be integers")
        group.append(int(value))
    if any(value < 0 for value in group):
        raise ValueError("shape group indices must be nonnegative")
    if any(b <= a for a, b in zip(group, group[1:])):
        raise ValueError("shape group indices must be strictly ordered")
    return group


def _validate_factor(factor: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if not isinstance(factor, dict):
        raise ValueError("shape factor must be a dictionary")
    if factor.get("diagnostic_only") is not True:
        raise ValueError("shape factor must be diagnostic_only")
    if factor.get("available") is not True:
        raise ValueError("shape factor must be available")
    if factor.get("solver_accepted") is not True:
        raise ValueError("shape factor must come from an accepted solver")
    if factor.get("not_admissible_for_graph") is not True:
        raise ValueError("shape factor must remain marked not admissible for graph")
    metadata = factor.get("metadata", {})
    if metadata.get("calibrated_covariance") is not False:
        raise ValueError("shape factor must not claim calibrated covariance")
    if metadata.get("statistical_independence_claimed") is not False:
        raise ValueError("shape factor must not claim statistical independence")
    if metadata.get("available_for_graph") is not False:
        raise ValueError("shape factor must remain unavailable for production graph use")
    frames = factor.get("frames")
    if isinstance(frames, (bool, np.bool_)) or not isinstance(frames, (int, np.integer)) or int(frames) != 9:
        raise ValueError("shape factor must describe exactly nine frames")
    if factor.get("gauge") != "all_relative_centers_mapped_through_first_camera_rotation":
        raise ValueError("shape factor gauge mismatch")
    sensitivity = np.asarray(factor.get("compact_sqrt_sensitivity"), dtype=float)
    if sensitivity.size == 0:
        sensitivity = np.zeros((0, 24), dtype=float)
    reference = np.asarray(factor.get("reference_center_vector_m"), dtype=float)
    affine = np.asarray(factor.get("affine_offset", []), dtype=float)
    if sensitivity.ndim != 2 or sensitivity.shape[1] != 24 or not np.all(np.isfinite(sensitivity)):
        raise ValueError("shape sensitivity must be finite Mx24")
    if reference.shape != (24,) or not np.all(np.isfinite(reference)):
        raise ValueError("shape reference must be finite length 24")
    if affine.shape != (sensitivity.shape[0],) or not np.all(np.isfinite(affine)):
        raise ValueError("shape affine offset malformed")
    return sensitivity, reference, affine


def _positions_for_group(camera_positions, group: list[int]) -> np.ndarray:
    positions = np.asarray(camera_positions, dtype=float)
    if positions.ndim != 2 or positions.shape[1] != 3:
        raise ValueError("camera positions must be Nx3")
    if max(group) >= len(positions) or not np.all(np.isfinite(positions[group])):
        raise ValueError("camera positions missing/nonfinite group nodes")
    return positions[group]


def _rotation_for_first(camera_rotations, first_index: int) -> Rotation:
    if not isinstance(camera_rotations, Rotation):
        raise ValueError("camera rotations missing group nodes")
    try:
        count = len(camera_rotations)
    except TypeError as error:
        raise ValueError("camera rotations missing group nodes") from error
    if count <= first_index:
        raise ValueError("camera rotations missing group nodes")
    rotvec = camera_rotations[first_index].as_rotvec()
    if not np.all(np.isfinite(rotvec)):
        raise ValueError("camera rotation nonfinite")
    return camera_rotations[first_index]


def _node_columns(group: list[int], node_column_map: dict[int, int]) -> list[int]:
    columns = []
    for node in group:
        if node not in node_column_map:
            raise ValueError("shape factor requires all nine interior graph nodes")
        start = node_column_map[node]
        if isinstance(start, bool) or not isinstance(start, (int, np.integer)) or int(start) < 0:
            raise ValueError("node column map values must be nonnegative integers")
        columns.append(int(start))
    used = []
    for start in columns:
        used.extend([start, start + 1, start + 2])
    if len(set(used)) != len(used):
        raise ValueError("node column triplets must not overlap")
    return columns


def _local_vector(positions: np.ndarray, first_rotation: Rotation) -> np.ndarray:
    return first_rotation.inv().apply(positions[1:] - positions[0]).reshape(-1)


def build_shape_group_row(
    factor: dict,
    indices,
    camera_positions,
    camera_rotations: Rotation,
    node_column_map: dict[int, int],
    *,
    total_columns: int,
    scale_column: int | None = None,
) -> dict:
    """Construct ``r = A x + b`` for one 9-center diagnostic shape group.

    The state vector contains camera-origin translation corrections at the
    supplied node columns. If ``scale_column`` is provided, it applies a single
    global camera-translation scale perturbation to the frozen camera baselines.
    """
    group = _validate_indices(indices)
    sensitivity, reference, affine = _validate_factor(factor)
    positions = _positions_for_group(camera_positions, group)
    first_rotation = _rotation_for_first(camera_rotations, group[0])
    starts = _node_columns(group, node_column_map)
    if isinstance(total_columns, bool) or not isinstance(total_columns, (int, np.integer)) or int(total_columns) <= 0:
        raise ValueError("total_columns must be positive integer")
    total_columns = int(total_columns)
    if any(start + 2 >= total_columns for start in starts):
        raise ValueError("node columns exceed total_columns")
    used_node_columns = {column for start in starts for column in range(start, start + 3)}
    if scale_column is not None:
        if isinstance(scale_column, bool) or not isinstance(scale_column, (int, np.integer)):
            raise ValueError("scale column must be integer")
        scale_column = int(scale_column)
        if scale_column < 0 or scale_column >= total_columns:
            raise ValueError("scale column exceeds total_columns")
        if scale_column in used_node_columns:
            raise ValueError("scale column must not overlap node columns")

    inv_matrix = first_rotation.inv().as_matrix()
    local_base = _local_vector(positions, first_rotation)
    residual0 = affine + sensitivity @ (local_base - reference)
    rows = []
    cols = []
    values = []
    for local_node in range(1, 9):
        block = sensitivity[:, 3 * (local_node - 1) : 3 * local_node] @ inv_matrix
        first_block = -block
        for axis in range(3):
            column = starts[local_node] + axis
            rows.extend(range(sensitivity.shape[0]))
            cols.extend([column] * sensitivity.shape[0])
            values.extend(block[:, axis].tolist())
            first_column = starts[0] + axis
            rows.extend(range(sensitivity.shape[0]))
            cols.extend([first_column] * sensitivity.shape[0])
            values.extend(first_block[:, axis].tolist())
    if scale_column is not None:
        scale_jac = sensitivity @ local_base
        rows.extend(range(sensitivity.shape[0]))
        cols.extend([scale_column] * sensitivity.shape[0])
        values.extend(scale_jac.tolist())

    jacobian = csr_matrix((values, (rows, cols)), shape=(sensitivity.shape[0], total_columns))
    return {
        "diagnostic_only": True,
        "available_for_graph": False,
        "calibrated_covariance": False,
        "statistical_independence_claimed": False,
        "profile_conditioning": "pixel_and_gyro_conditioned_local_affine_shape_diagnostic",
        "node_indices": group,
        "columns": starts,
        "scale_column": scale_column,
        "local_delta_from_ba_m": (local_base - reference).tolist(),
        "group_residual_norm_initial": float(np.linalg.norm(residual0)),
        "group_residual_norm_final": float(np.linalg.norm(residual0)),
        "max_local_node_delta_from_initial_m": 0.0,
        "max_local_node_delta_from_ba_m": float(np.max(np.linalg.norm((local_base - reference).reshape(8, 3), axis=1))),
        "residual": residual0,
        "jacobian": jacobian,
        "_reference_center_vector_m": reference,
        "_compact_sqrt_sensitivity": sensitivity,
        "_affine_offset": affine,
        "_base_positions": positions,
        "_first_rotation": first_rotation,
    }


def evaluate_shape_group_state(row: dict, state) -> dict:
    """Evaluate final diagnostic residual/nodal displacement for one row."""
    state = np.asarray(state, dtype=float)
    jacobian = row.get("jacobian")
    if not isinstance(jacobian, csr_matrix) or state.shape != (jacobian.shape[1],) or not np.all(np.isfinite(state)):
        raise ValueError("invalid graph state for shape row")
    group = row["node_indices"]
    starts = row["columns"]
    positions = np.asarray(row["_base_positions"], dtype=float).copy()
    base = positions.copy()
    for local, start in enumerate(starts):
        positions[local] += state[start:start + 3]
    scale_column = row.get("scale_column")
    if scale_column is not None:
        positions += state[scale_column] * (base - base[0])
    local_vector = _local_vector(positions, row["_first_rotation"])
    residual = row["_affine_offset"] + row["_compact_sqrt_sensitivity"] @ (
        local_vector - row["_reference_center_vector_m"]
    )
    local_initial = _local_vector(base, row["_first_rotation"])
    local_delta_from_initial = local_vector - local_initial
    local_delta_from_ba = local_vector - row["_reference_center_vector_m"]
    return {
        "diagnostic_only": True,
        "node_indices": list(group),
        "local_delta_from_ba_m": local_delta_from_ba.tolist(),
        "group_residual_norm_final": float(np.linalg.norm(residual)),
        "max_local_node_delta_from_initial_m": float(np.max(np.linalg.norm(local_delta_from_initial.reshape(8, 3), axis=1))),
        "max_local_node_delta_from_ba_m": float(np.max(np.linalg.norm(local_delta_from_ba.reshape(8, 3), axis=1))),
        "residual": residual,
    }
