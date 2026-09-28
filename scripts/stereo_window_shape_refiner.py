"""Scoped native LSQR adapter for diagnostic stereo-window shape rows.

This module is intentionally small and isolated: it does not launch graph
runs, read trajectories, choose weights, or promote shape factors to
production.  It wraps exactly one native ``refine_positions_visual_inertial``
call and, for grouped diagnostic mode only, replaces the two native endpoint
stereo rows for each complete seam group with a prebuilt correlated shape row.
"""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
from pathlib import Path
import sys
from typing import Any

import numpy as np

def _load_sibling_module(name: str):
    path = Path(__file__).resolve().with_name(f"{name}.py")
    module_name = f"_stereo_window_shape_refiner_{name}"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ModuleNotFoundError(f"cannot load sibling module {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


try:  # support repo-root imports, direct file loading, and standalone pytest collection
    from scripts import stereo_window_shape_graph as shape_graph
    from scripts import stereo_window_shape_system as shape_system
except ModuleNotFoundError:  # pragma: no cover
    shape_graph = _load_sibling_module("stereo_window_shape_graph")
    shape_system = _load_sibling_module("stereo_window_shape_system")


SHAPE_ENDPOINT_SOURCE = "stereo_window_full_seam_pair_v1"
GROUPED_SHAPE_MODE = "grouped_shape"
ENDPOINT_CONTROL_MODE = "endpoint_control"


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strict_group_id(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("shape group_id must be nonempty string")
    return value


def _strict_int(value: Any, name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{name} must be integer")
    value = int(value)
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} below minimum")
    return value


def _accepted_stereo_edges(observations: list[dict]) -> list[dict]:
    return [
        observation
        for observation in observations
        if observation.get("accepted")
        and "metric_displacement_camera_i_m" in observation
    ]


def _bound_native_arguments(native: Any, args: tuple, kwargs: dict) -> inspect.BoundArguments:
    signature = inspect.signature(native.refine_positions_visual_inertial)
    bound = signature.bind(*args, **kwargs)
    bound.apply_defaults()
    return bound


def _regular_nodes(native: Any, position_count: int, node_stride: int) -> set[int]:
    regular = native.regular_node_indices(position_count, node_stride)
    return {_strict_int(index, "regular node index", minimum=0) for index in regular}


def _shape_node_union(native: Any, bound: inspect.BoundArguments, accepted: list[dict], groups: list[dict]) -> np.ndarray:
    positions = np.asarray(bound.arguments["positions"])
    if positions.ndim != 2 or positions.shape[1] != 3 or len(positions) < 2:
        raise ValueError("positions must be Nx3")
    original = bound.arguments.get("correction_node_indices")
    if original is None:
        node_stride = _strict_int(bound.arguments.get("node_stride", 10), "node_stride", minimum=1)
        nodes = _regular_nodes(native, len(positions), node_stride)
        nodes.update(
            _strict_int(edge[key], key, minimum=0)
            for edge in accepted
            for key in ("first_index", "second_index")
        )
    else:
        original_array = np.asarray(original)
        if original_array.ndim != 1:
            raise ValueError("correction_node_indices must be one-dimensional")
        ordered_original = [
            _strict_int(index, "correction node index", minimum=0)
            for index in original_array.tolist()
        ]
        if len(set(ordered_original)) != len(ordered_original):
            raise ValueError("duplicate correction node indices")
        nodes = set(ordered_original)
    nodes.update({0, len(positions) - 1})
    for group in groups:
        nodes.update(_validated_group_indices(group))
    if any(index < 0 or index >= len(positions) for index in nodes):
        raise ValueError("shape correction node index out of trajectory bounds")
    ordered = np.asarray(sorted(nodes), dtype=int)
    if len(ordered) != len(nodes):
        raise ValueError("duplicate correction node indices")
    if ordered[0] != 0 or ordered[-1] != len(positions) - 1:
        raise ValueError("shape correction nodes must include endpoints")
    return ordered


def _validated_group_indices(group: dict) -> list[int]:
    if not isinstance(group, dict):
        raise ValueError("shape group must be dictionary")
    indices = group.get("indices9")
    scratch: set[int] = set()
    shape_graph.add_shape_group_nodes(indices, scratch)
    return [int(index) for index in indices]


def _edge_matches(edge: dict, first: int, second: int, group_id: str, part: int) -> bool:
    try:
        identity = shape_system.edge_identity(edge)
    except ValueError:
        return False
    return (
        identity["first_index"] == first
        and identity["second_index"] == second
        and identity["metric_source"] == SHAPE_ENDPOINT_SOURCE
        and identity["correlated_factor_group_id"] == group_id
        and identity["correlated_factor_group_part"] == part
        and identity["correlated_factor_group_size"] == 2
    )


def _endpoint_indices_for_group(accepted: list[dict], group_id: str, indices: list[int]) -> list[int]:
    wanted = ((indices[0], indices[4], 0), (indices[4], indices[8], 1))
    endpoint_indices: list[int] = []
    for first, second, part in wanted:
        matches = [
            edge_index
            for edge_index, edge in enumerate(accepted)
            if _edge_matches(edge, first, second, group_id, part)
        ]
        if len(matches) != 1:
            raise ValueError(f"shape group {group_id} missing complete accepted endpoint pair")
        endpoint_indices.append(matches[0])
    if endpoint_indices != sorted(endpoint_indices):
        raise ValueError(f"shape group {group_id} endpoint order mismatch")
    return endpoint_indices


def _scale_active(bound: inspect.BoundArguments) -> bool:
    positions = np.asarray(bound.arguments["positions"], dtype=float)
    solve_metric_scale = bool(bound.arguments.get("solve_metric_scale", False))
    sufficient = bool(np.max(np.linalg.norm(positions - positions[0], axis=1)) > 0.001)
    return bool(solve_metric_scale and sufficient)


def _shape_rows_for_groups(
    bound: inspect.BoundArguments,
    groups: list[dict],
    accepted: list[dict],
    node_indices: np.ndarray,
    *,
    scale_active: bool,
) -> list[dict]:
    node_column_map = {int(node): 3 * ordinal for ordinal, node in enumerate(node_indices)}
    total_columns = 6 * len(node_indices) + 6 + int(scale_active)
    scale_column = total_columns - 1 if scale_active else None
    rows = []
    seen: set[str] = set()
    for group in groups:
        group_id = _strict_group_id(group.get("group_id"))
        if group_id in seen:
            raise ValueError(f"duplicate shape group_id {group_id}")
        seen.add(group_id)
        indices = _validated_group_indices(group)
        endpoint_indices = _endpoint_indices_for_group(accepted, group_id, indices)
        factor = group.get("factor")
        if not isinstance(factor, dict) or factor.get("available") is not True:
            raise ValueError(f"shape group {group_id} factor unavailable")
        graph_row = shape_graph.build_shape_group_row(
            factor,
            indices,
            bound.arguments["positions"],
            bound.arguments["camera_rotations"],
            node_column_map,
            total_columns=total_columns,
            scale_column=scale_column,
        )
        endpoint_identities = [
            shape_system.edge_identity(accepted[index]) for index in endpoint_indices
        ]
        rows.append(
            {
                **graph_row,
                "group_id": group_id,
                "endpoint_edge_indices": endpoint_indices,
                "endpoint_identities": endpoint_identities,
                "metric_source": SHAPE_ENDPOINT_SOURCE,
                "rank": int(graph_row["jacobian"].shape[0]),
                "frozen_endpoint_indices": endpoint_indices,
                "full_accepted_identities": [shape_system.edge_identity(edge) for edge in accepted],
                "source": SHAPE_ENDPOINT_SOURCE,
            }
        )
    return rows


def _static_pair_count_from_layout(
    matrix,
    *,
    node_count: int,
    accepted_count: int,
    relative_motion: bool,
) -> int:
    stereo_start = shape_system.stereo_row_start(node_count, relative_motion=relative_motion)
    remainder = int(matrix.shape[0]) - stereo_start - 3 * accepted_count - 6
    if remainder < 0:
        raise ValueError("native row layout has negative static-pair count")
    if remainder % 3:
        raise ValueError("native row layout static-pair rows are not divisible by three")
    return remainder // 3


def _accepted_identities_or_raise(accepted: list[dict]) -> list[dict]:
    return [shape_system.edge_identity(edge) for edge in accepted]


def refine_positions_visual_inertial_with_shape(
    native: Any,
    shape_groups: list[dict] | tuple[dict, ...],
    mode: str,
    *args,
    expected_native_sha256: str | None = None,
    **kwargs,
):
    """Run one native refinement with endpoint-control or grouped-shape rows.

    With zero groups, this is an exact no-op wrapper: the native function is
    called with the original args/kwargs, without hashing or installing a hook,
    and the native return value is returned unchanged.
    """
    if not isinstance(shape_groups, (list, tuple)):
        raise ValueError("shape_groups must be list or tuple")
    groups = list(shape_groups)
    if not groups:
        return native.refine_positions_visual_inertial(*args, **kwargs)
    if mode not in (GROUPED_SHAPE_MODE, ENDPOINT_CONTROL_MODE):
        raise ValueError("shape mode must be endpoint_control or grouped_shape")
    if not expected_native_sha256:
        raise ValueError("expected_native_sha256 is required for active shape groups")
    native_path = getattr(native, "__file__", None)
    if not native_path:
        raise ValueError("native module __file__ is required")
    before_hash = file_sha256(native_path)
    if before_hash != expected_native_sha256:
        raise ValueError("native source sha256 mismatch before interception")

    bound = _bound_native_arguments(native, args, kwargs)
    observations = bound.arguments["observations"]
    if not isinstance(observations, list):
        raise ValueError("observations must be native list")
    accepted = _accepted_stereo_edges(observations)
    accepted_identities = _accepted_identities_or_raise(accepted)
    node_indices = _shape_node_union(native, bound, accepted, groups)
    scale_active = _scale_active(bound)
    shape_rows = _shape_rows_for_groups(
        bound, groups, accepted, node_indices, scale_active=scale_active
    )
    relative_motion = bound.arguments.get("relative_motion_positions_body") is not None
    call_arguments = dict(bound.arguments)
    call_arguments["correction_node_indices"] = node_indices

    original_lsqr = native.lsqr
    in_hook = False
    iteration_reports = []
    last_uncapped_state = None

    def guarded_lsqr(matrix, target, *lsqr_args, **lsqr_kwargs):
        nonlocal in_hook, last_uncapped_state
        if in_hook:
            return original_lsqr(matrix, target, *lsqr_args, **lsqr_kwargs)
        if file_sha256(native_path) != before_hash:
            raise ValueError("native source sha256 changed during interception")
        if _accepted_identities_or_raise(accepted) != accepted_identities:
            raise ValueError("accepted observation order/identity changed")
        static_pair_count = _static_pair_count_from_layout(
            matrix,
            node_count=len(node_indices),
            accepted_count=len(accepted),
            relative_motion=relative_motion,
        )
        if mode == GROUPED_SHAPE_MODE:
            spliced_matrix, spliced_target, splice_report = shape_system.splice_shape_rows(
                matrix,
                target,
                accepted,
                shape_rows,
                node_count=len(node_indices),
                relative_motion=relative_motion,
                static_pair_count=static_pair_count,
                scale_active=scale_active,
                accepted_order_identities=accepted_identities,
            )
        else:
            expected_rows, expected_columns, stereo_start = shape_system.expected_shape(
                len(node_indices),
                len(accepted),
                relative_motion=relative_motion,
                static_pair_count=static_pair_count,
                scale_active=scale_active,
            )
            if matrix.shape != (expected_rows, expected_columns):
                raise ValueError("native row layout mismatch")
            spliced_matrix, spliced_target = matrix, target
            splice_report = {
                "shape_group_count": 0,
                "accepted_edge_count": len(accepted),
                "removed_endpoint_edge_indices": [],
                "removed_row_indices": [],
                "appended_rank_rows": 0,
                "stereo_row_start": stereo_start,
            }
        in_hook = True
        try:
            result = original_lsqr(spliced_matrix, spliced_target, *lsqr_args, **lsqr_kwargs)
        finally:
            in_hook = False
        solution = np.asarray(result[0], dtype=float)
        if solution.shape != (spliced_matrix.shape[1],) or not np.all(np.isfinite(solution)):
            raise ValueError("native lsqr returned invalid solution")
        last_uncapped_state = solution.copy()
        iteration_reports.append(
            {
                **splice_report,
                "matrix_rows_before": int(matrix.shape[0]),
                "matrix_rows_after": int(spliced_matrix.shape[0]),
                "matrix_columns": int(spliced_matrix.shape[1]),
                "static_pair_count": int(static_pair_count),
            }
        )
        return result

    native.lsqr = guarded_lsqr
    try:
        refined, native_report = native.refine_positions_visual_inertial(**call_arguments)
    finally:
        native.lsqr = original_lsqr
    if len(iteration_reports) != 4:
        raise ValueError("native refine must issue exactly four lsqr calls")
    if _accepted_identities_or_raise(accepted) != accepted_identities:
        raise ValueError("accepted observation order/identity changed")
    after_hash = file_sha256(native_path)
    if after_hash != before_hash:
        raise ValueError("native source sha256 changed after interception")

    output_report = dict(native_report)
    diagnostics = {
        "mode": mode,
        "diagnostic_only": True,
        "available_for_production": False,
        "native_sha256": before_hash,
        "native_sha256_after": after_hash,
        "accepted_order_identity_preserved": True,
        "accepted_edge_count": len(accepted),
        "accepted_identities": accepted_identities,
        "node_indices": node_indices.tolist(),
        "node_union_includes_all_shape_indices": all(
            int(index) in set(node_indices.tolist())
            for group in groups
            for index in _validated_group_indices(group)
        ),
        "scale_active": scale_active,
        "shape_groups": [
            {
                "group_id": row["group_id"],
                "node_indices": row["node_indices"],
                "frozen_endpoint_indices": row["frozen_endpoint_indices"],
                "endpoint_identities": row["endpoint_identities"],
                "source": row["source"],
                "rank": row["rank"],
                "group_residual_norm_initial": row["group_residual_norm_initial"],
            }
            for row in shape_rows
        ],
        "iterations": iteration_reports,
        "last_uncapped_lsqr_state": last_uncapped_state.tolist() if last_uncapped_state is not None else None,
        "native_clipping": {
            key: native_report.get(key)
            for key in (
                "position_correction_requested_max_m",
                "position_correction_max_m",
                "position_correction_limit_m",
                "correction_scale",
                "correction_clipped_frames",
            )
            if key in native_report
        },
    }
    if last_uncapped_state is not None:
        diagnostics["last_uncapped_shape_evaluation"] = [
            shape_graph.evaluate_shape_group_state(row, last_uncapped_state)
            for row in shape_rows
        ]
    post_cap_state = np.zeros(6 * len(node_indices) + 6 + int(scale_active))
    initial_positions = np.asarray(bound.arguments["positions"], dtype=float)
    refined_positions = np.asarray(refined, dtype=float)
    for ordinal, node in enumerate(node_indices):
        post_cap_state[3 * ordinal : 3 * ordinal + 3] = (
            refined_positions[int(node)] - initial_positions[int(node)]
        )
    diagnostics["actual_returned_camera_shape_evaluation"] = [
        shape_graph.evaluate_shape_group_state(row, post_cap_state)
        for row in shape_rows
    ]
    output_report["stereo_window_shape_refiner"] = diagnostics
    return refined, output_report
