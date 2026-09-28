"""Pure sparse row-splice helper for diagnostic stereo-window shape groups.

This module does not run native fusion, choose weights, or define covariance.
It only replaces the two same-source endpoint stereo rows for a complete
accepted group with prebuilt correlated shape rows.
"""
from __future__ import annotations

import numpy as np
from scipy.sparse import csr_matrix, vstack


def _strict_int(value, name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{name} must be integer")
    value = int(value)
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} below minimum")
    return value


def _strict_bool(value, name: str) -> bool:
    if not isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{name} must be boolean")
    return bool(value)


def stereo_row_start(node_count: int, *, relative_motion: bool) -> int:
    node_count = _strict_int(node_count, "node_count", minimum=1)
    relative_motion = _strict_bool(relative_motion, "relative_motion")
    relative_rows = node_count - 1 if relative_motion else 0
    return 3 * node_count + 3 + 3 * max(node_count - 2, 0) + 3 * relative_rows + 6 * (node_count - 1)


def expected_shape(node_count: int, edge_count: int, *, relative_motion: bool, static_pair_count: int, scale_active: bool) -> tuple[int, int, int]:
    node_count = _strict_int(node_count, "node_count", minimum=1)
    edge_count = _strict_int(edge_count, "edge_count", minimum=0)
    static_pair_count = _strict_int(static_pair_count, "static_pair_count", minimum=0)
    relative_motion = _strict_bool(relative_motion, "relative_motion")
    scale_active = _strict_bool(scale_active, "scale_active")
    start = stereo_row_start(node_count, relative_motion=relative_motion)
    rows = start + 3 * edge_count + 6 + 3 * static_pair_count
    columns = 6 * node_count + 6 + (1 if scale_active else 0)
    return rows, columns, start


def _group_key(edge: dict):
    if not isinstance(edge, dict):
        raise ValueError("accepted observations must be dictionaries")
    return edge.get("correlated_factor_group_id")


def edge_identity(edge: dict) -> dict:
    """Canonical accepted-edge identity used to freeze native order."""
    if not isinstance(edge, dict):
        raise ValueError("accepted observations must be dictionaries")
    first = _strict_int(edge.get("first_index"), "first_index", minimum=0)
    second = _strict_int(edge.get("second_index"), "second_index", minimum=0)
    if second <= first:
        raise ValueError("accepted edge requires first_index < second_index")
    group_id = edge.get("correlated_factor_group_id")
    part = edge.get("correlated_factor_group_part")
    size = edge.get("correlated_factor_group_size")
    if group_id is not None:
        if not isinstance(group_id, str) or not group_id:
            raise ValueError("shape group id must be nonempty string")
        part = _strict_int(part, "correlated_factor_group_part")
        size = _strict_int(size, "correlated_factor_group_size")
        if part not in (0, 1) or size != 2:
            raise ValueError("shape endpoint canonical identity malformed")
    elif part is not None or size is not None:
        raise ValueError("ungrouped edge must not carry shape part/size")
    return {
        "first_index": first,
        "second_index": second,
        "metric_source": edge.get("metric_source"),
        "correlated_factor_group_id": group_id,
        "correlated_factor_group_part": part,
        "correlated_factor_group_size": size,
    }


def _validate_expected_order(accepted: list[dict], expected) -> None:
    if not isinstance(expected, (list, tuple)) or len(expected) != len(accepted):
        raise ValueError("accepted order identity required")
    actual = [edge_identity(edge) for edge in accepted]
    if list(expected) != actual:
        raise ValueError("accepted observation order/identity changed")


def _validate_group_edges(group_id, indices: list[int], accepted: list[dict]) -> None:
    if len(indices) != 2:
        raise ValueError(f"shape group {group_id} incomplete or duplicate")
    if indices != sorted(indices):
        raise ValueError(f"shape group {group_id} accepted order mismatch")
    first, second = (accepted[index] for index in indices)
    if first.get("metric_source") != second.get("metric_source"):
        raise ValueError(f"shape group {group_id} metric_source mismatch")
    identities = [edge_identity(first), edge_identity(second)]
    if {identities[0]["correlated_factor_group_part"], identities[1]["correlated_factor_group_part"]} == {0, 1} and identities[0]["correlated_factor_group_part"] != 0:
        raise ValueError(f"shape group {group_id} accepted order mismatch")
    if (
        identities[0]["correlated_factor_group_id"] != group_id
        or identities[0]["correlated_factor_group_part"] != 0
        or identities[0]["correlated_factor_group_size"] != 2
        or identities[1]["correlated_factor_group_id"] != group_id
        or identities[1]["correlated_factor_group_part"] != 1
        or identities[1]["correlated_factor_group_size"] != 2
    ):
        raise ValueError(f"shape group {group_id} canonical part/size mismatch")
    if identities[0]["second_index"] != identities[1]["first_index"]:
        raise ValueError(f"shape group {group_id} endpoint continuity mismatch")


def _accepted_groups(accepted: list[dict]) -> dict:
    groups: dict[object, list[int]] = {}
    seen_parts: set[tuple[object, int]] = set()
    for index, edge in enumerate(accepted):
        group_id = _group_key(edge)
        if group_id is None:
            continue
        identity = edge_identity(edge)
        part = identity["correlated_factor_group_part"]
        key = (group_id, part)
        if key in seen_parts:
            raise ValueError(f"shape group {group_id} duplicate endpoint part")
        seen_parts.add(key)
        groups.setdefault(group_id, []).append(index)
    for group_id, indices in groups.items():
        _validate_group_edges(group_id, indices, accepted)
    return groups


def _shape_columns_are_allowed(jacobian: csr_matrix, node_count: int, *, scale_active: bool) -> bool:
    if jacobian.nnz == 0:
        return True
    nonzero = np.flatnonzero(jacobian.data != 0.0)
    if len(nonzero) == 0:
        return True
    forbidden = set(range(3 * node_count, 6 * node_count + 6))
    if not scale_active and jacobian.shape[1] > 6 * node_count + 6:
        forbidden.add(6 * node_count + 6)
    return not any(int(jacobian.indices[index]) in forbidden for index in nonzero)


def _validate_shape_row(row: dict, total_columns: int, edge_count: int, accepted: list[dict], node_count: int, scale_active: bool) -> tuple[object, list[int], csr_matrix, np.ndarray, int]:
    if not isinstance(row, dict):
        raise ValueError("shape row must be dictionary")
    group_id = row.get("group_id")
    endpoints = row.get("endpoint_edge_indices")
    if (
        not isinstance(group_id, str)
        or not group_id
        or not isinstance(endpoints, (list, tuple))
        or len(endpoints) != 2
        or any(isinstance(value, bool) or not isinstance(value, (int, np.integer)) for value in endpoints)
    ):
        raise ValueError("shape row endpoint mapping malformed")
    endpoints = [_strict_int(value, "endpoint_edge_index", minimum=0) for value in endpoints]
    if endpoints[0] == endpoints[1]:
        raise ValueError("shape row duplicate endpoint mapping")
    if any(value < 0 or value >= edge_count for value in endpoints):
        raise ValueError("shape row endpoint mapping out of range")
    expected_identities = row.get("endpoint_identities")
    if not isinstance(expected_identities, (list, tuple)) or len(expected_identities) != 2:
        raise ValueError("shape row endpoint identities required")
    actual_identities = [edge_identity(accepted[index]) for index in endpoints]
    if list(expected_identities) != actual_identities:
        raise ValueError("shape row endpoint identity/order changed")
    metric_source = row.get("metric_source", actual_identities[0]["metric_source"])
    if metric_source != actual_identities[0]["metric_source"] or metric_source != actual_identities[1]["metric_source"]:
        raise ValueError("shape row metric_source mismatch")
    jacobian = row.get("jacobian")
    if not isinstance(jacobian, csr_matrix):
        try:
            jacobian = csr_matrix(jacobian)
        except Exception as error:  # pragma: no cover - scipy provides detail
            raise ValueError("shape jacobian malformed") from error
    jacobian = jacobian.tocsr()
    residual = np.asarray(row.get("residual"), dtype=float)
    if jacobian.ndim != 2 or jacobian.shape[1] != total_columns:
        raise ValueError("shape jacobian width mismatch")
    if not _shape_columns_are_allowed(jacobian, node_count, scale_active=scale_active):
        raise ValueError("shape jacobian may only use position and active scale columns")
    if residual.shape != (jacobian.shape[0],) or not np.all(np.isfinite(residual)):
        raise ValueError("shape residual must be finite and match rows")
    if not np.all(np.isfinite(jacobian.data)):
        raise ValueError("shape jacobian data must be finite")
    rank = row.get("rank", jacobian.shape[0])
    if _strict_int(rank, "rank", minimum=0) != jacobian.shape[0]:
        raise ValueError("shape row rank mismatch")
    return group_id, endpoints, jacobian, residual, int(rank)


def splice_shape_rows(
    matrix,
    target,
    accepted_observations: list[dict],
    shape_rows: list[dict],
    *,
    node_count: int,
    relative_motion: bool = True,
    static_pair_count: int = 0,
    scale_active: bool = True,
    accepted_order_identities=None,
):
    """Replace grouped endpoint stereo rows with supplied correlated rows.

    Empty ``shape_rows`` is an identity no-op returning the same matrix and
    target objects. Otherwise the input matrix/target are never mutated.
    """
    if not isinstance(shape_rows, (list, tuple)):
        raise ValueError("shape_rows must be list or tuple")
    if not shape_rows:
        return matrix, target, {
            "shape_group_count": 0,
            "accepted_edge_count": len(accepted_observations),
            "removed_endpoint_edge_indices": [],
            "removed_row_indices": [],
            "appended_rank_rows": 0,
        }
    node_count = _strict_int(node_count, "node_count", minimum=1)
    relative_motion = _strict_bool(relative_motion, "relative_motion")
    static_pair_count = _strict_int(static_pair_count, "static_pair_count", minimum=0)
    scale_active = _strict_bool(scale_active, "scale_active")
    if not isinstance(matrix, csr_matrix):
        matrix = csr_matrix(matrix)
    target_array = np.asarray(target, dtype=float)
    if target_array.ndim != 1 or target_array.shape[0] != matrix.shape[0] or not np.all(np.isfinite(target_array)):
        raise ValueError("target must be finite and match matrix rows")
    edge_count = len(accepted_observations)
    expected_rows, expected_columns, stereo_start = expected_shape(
        node_count,
        edge_count,
        relative_motion=relative_motion,
        static_pair_count=static_pair_count,
        scale_active=scale_active,
    )
    if matrix.shape != (expected_rows, expected_columns):
        raise ValueError("native row layout mismatch")
    if not np.all(np.isfinite(matrix.data)):
        raise ValueError("native matrix data must be finite")
    _validate_expected_order(accepted_observations, accepted_order_identities)
    groups = _accepted_groups(accepted_observations)
    seen_shape_groups = set()
    removed_edges: list[int] = []
    removed_rows: list[int] = []
    appended_matrices = []
    appended_targets = []
    for row in shape_rows:
        group_id, endpoints, jacobian, residual, rank = _validate_shape_row(
            row, expected_columns, edge_count, accepted_observations, node_count, scale_active
        )
        if group_id in seen_shape_groups:
            raise ValueError(f"shape group {group_id} duplicate replacement")
        seen_shape_groups.add(group_id)
        if group_id not in groups:
            raise ValueError(f"shape group {group_id} missing accepted endpoints")
        if endpoints != groups[group_id]:
            raise ValueError(f"shape group {group_id} accepted order/mapping mismatch")
        for edge_index in endpoints:
            start = stereo_start + 3 * edge_index
            removed_rows.extend([start, start + 1, start + 2])
        removed_edges.extend(endpoints)
        appended_matrices.append(jacobian)
        appended_targets.append(-residual)
        if rank != jacobian.shape[0]:
            raise ValueError("shape rank mismatch")
    if len(set(removed_edges)) != len(removed_edges):
        raise ValueError("shape rows remove duplicate endpoints")
    keep_mask = np.ones(matrix.shape[0], dtype=bool)
    keep_mask[removed_rows] = False
    kept = matrix[keep_mask].tocsr()
    out_matrix = vstack([kept] + appended_matrices).tocsr()
    out_target = np.concatenate([target_array[keep_mask]] + appended_targets)
    report = {
        "shape_group_count": len(shape_rows),
        "accepted_edge_count": edge_count,
        "removed_endpoint_edge_indices": removed_edges,
        "removed_row_indices": removed_rows,
        "appended_rank_rows": int(sum(block.shape[0] for block in appended_matrices)),
        "stereo_row_start": stereo_start,
    }
    return out_matrix, out_target, report
