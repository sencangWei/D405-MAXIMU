"""Prototype grouped stereo-window center-shape sensitivity factor.

This module is diagnostic/prototype-only. It reuses the existing stereo bundle
solver unchanged, captures its optimized Jacobian, projects out
rotation/landmark/gyro-bias nuisance columns, and exposes one correlated grouped
center-shape sensitivity. It is not a covariance estimate, not an independent
edge set, and not an exact nonlinear profiled factor. The current transformed
residual/Jacobian affine profile is preserved, not raw-pixel covariance or a
guarantee away from the local linearization point.
"""
from __future__ import annotations

import numpy as np


def center_columns(frames: int) -> np.ndarray:
    if isinstance(frames, (bool, np.bool_)) or not isinstance(frames, (int, np.integer)) or int(frames) < 2:
        raise ValueError("at least two frames required")
    frames = int(frames)
    start = 3 * (frames - 1)
    return np.arange(start, start + 3 * (frames - 1), dtype=int)


def _as_array(value, shape, name: str) -> np.ndarray:
    array = np.asarray(value, dtype=float)
    if array.shape != shape or not np.all(np.isfinite(array)):
        raise ValueError(f"invalid {name}")
    return array


def _rotation_count(rotations) -> int:
    try:
        count = len(rotations)
    except TypeError as error:
        raise ValueError("invalid rotations") from error
    try:
        rotvecs = rotations.as_rotvec()
    except AttributeError as error:
        raise ValueError("invalid rotations") from error
    if np.asarray(rotvecs).shape != (count, 3) or not np.all(np.isfinite(rotvecs)):
        raise ValueError("invalid rotations")
    return count


def _local_center_vector(centers: np.ndarray, rotations) -> np.ndarray:
    frames = len(centers)
    if _rotation_count(rotations) != frames:
        raise ValueError("invalid rotations")
    relative = np.asarray(rotations[0].inv().apply(centers - centers[0]), dtype=float)
    if relative.shape != (frames, 3) or not np.all(np.isfinite(relative)):
        raise ValueError("invalid center gauge")
    return relative[1:].reshape(-1)


def _validate_first_gauge(centers: np.ndarray, rotations) -> None:
    if np.linalg.norm(centers[0]) > 1e-12:
        raise ValueError("first gauge center is not fixed")
    if np.linalg.norm(rotations[0].as_rotvec()) > 1e-12:
        raise ValueError("first gauge rotation is not fixed")


def _projected_sensitivity(jacobian: np.ndarray, target_columns: np.ndarray, residual=None) -> dict:
    jac = np.asarray(jacobian, dtype=float)
    cols = np.asarray(target_columns, dtype=int)
    r0 = np.zeros(jac.shape[0], dtype=float) if residual is None else np.asarray(residual, dtype=float)
    if (
        jac.ndim != 2
        or jac.shape[0] == 0
        or not np.all(np.isfinite(jac))
        or r0.shape != (jac.shape[0],)
        or not np.all(np.isfinite(r0))
        or cols.ndim != 1
        or len(cols) == 0
        or len(set(cols.tolist())) != len(cols)
        or np.any(cols < 0)
        or np.any(cols >= jac.shape[1])
    ):
        raise ValueError("invalid Jacobian/variable packing")
    target = jac[:, cols]
    nuisance_mask = np.ones(jac.shape[1], dtype=bool)
    nuisance_mask[cols] = False
    nuisance = jac[:, nuisance_mask]
    norms = np.linalg.norm(nuisance, axis=0)
    nuisance = nuisance[:, norms > 0.0] / norms[norms > 0.0]
    nuisance_rank = 0
    conditional = target.copy()
    projected_residual = r0.copy()
    if nuisance.shape[1]:
        u, singular, _ = np.linalg.svd(nuisance, full_matrices=False)
        tolerance = np.finfo(float).eps * max(nuisance.shape) * singular[0]
        nuisance_rank = int(np.count_nonzero(singular > tolerance))
        if nuisance_rank:
            basis = u[:, :nuisance_rank]
            conditional -= basis @ (basis.T @ target)
            projected_residual -= basis @ (basis.T @ projected_residual)
    full_for_null = conditional.shape[0] < conditional.shape[1]
    _, singular, vh = np.linalg.svd(conditional, full_matrices=full_for_null)
    scale = max(float(np.linalg.norm(target, ord=2)), float(singular[0]) if len(singular) else 0.0)
    tolerance = np.finfo(float).eps * max(jac.shape) * max(scale, 1.0)
    rank = int(np.count_nonzero(singular > tolerance))
    compact = (
        np.zeros((0, len(cols)), dtype=float)
        if rank == 0
        else singular[:rank, None] * vh[:rank]
    )
    retained = np.zeros(0, dtype=float) if rank == 0 else vh[:rank] @ np.zeros(len(cols))
    if rank:
        # U columns are obtained without keeping the MxM matrix. Since
        # conditional @ V / s == U for retained singular values:
        retained_basis = conditional @ vh[:rank].T / singular[:rank]
        affine = retained_basis.T @ projected_residual
        dropped = projected_residual - retained_basis @ affine
    else:
        affine = retained
        dropped = projected_residual
    return {
        "nuisance_rank": nuisance_rank,
        "conditional_rank": rank,
        "rank_deficient": rank < len(cols),
        "singular_values_per_m": singular.tolist(),
        "rank_tolerance_per_m": float(tolerance),
        "compact_sqrt_sensitivity": compact.tolist(),
        "affine_offset": affine.tolist(),
        "dropped_constant_squared_norm": float(np.dot(dropped, dropped)),
        "weak_axis_center_gauge": (vh[-1].tolist() if len(vh) else [0.0] * len(cols)),
        "null_axes_center_gauge": vh[rank:].tolist(),
        "weak_response_mm_per_unit_normalized_residual": (
            float(1000.0 / singular[rank - 1]) if rank == len(cols) else None
        ),
    }


def build_shape_factor(
    jacobian,
    centers,
    rotations,
    *,
    point_count: int,
    residual=None,
) -> dict:
    centers = np.asarray(centers, dtype=float)
    if centers.ndim != 2 or centers.shape[1] != 3 or len(centers) < 2:
        raise ValueError("invalid centers")
    if not np.all(np.isfinite(centers)):
        raise ValueError("invalid centers")
    frames = len(centers)
    if _rotation_count(rotations) != frames:
        raise ValueError("invalid rotations")
    _validate_first_gauge(centers, rotations)
    if isinstance(point_count, (bool, np.bool_)) or not isinstance(point_count, (int, np.integer)):
        raise ValueError("invalid point count")
    point_count = int(point_count)
    if point_count < 0:
        raise ValueError("invalid point count")
    expected_columns = 6 * (frames - 1) + 3 * point_count + 3
    jac = np.asarray(jacobian, dtype=float)
    if jac.ndim != 2 or jac.shape[1] != expected_columns:
        raise ValueError("Jacobian/variable packing mismatch")
    cols = center_columns(frames)
    sensitivity = _projected_sensitivity(jac, cols, residual=residual)
    reference = _local_center_vector(centers, rotations)
    return {
        "diagnostic_only": True,
        "available": True,
        "frames": int(frames),
        "point_count": int(point_count),
        "gauge": "all_relative_centers_mapped_through_first_camera_rotation",
        "center_parameter_columns": cols.tolist(),
        "reference_center_vector_m": reference.tolist(),
        "optimized_centers_m": centers.tolist(),
        "optimized_rotvecs_camera_to_window": rotations.as_rotvec().tolist(),
        "nuisance_columns_projected": "rotations_landmarks_gyro_bias_normalized_svd",
        "gyro_factors_reused_from_solver": True,
        "metadata": {
            "metric_source": "stereo_window_shape_factor_v1",
            "correlated_factor_group_size": 1,
            "correlated_factor_group_part": 0,
            "calibrated_covariance": False,
            "statistical_independence_claimed": False,
            "prototype_only": True,
            "available_for_graph": False,
            "fixed_weight_or_density_sweep": False,
        },
        "interpretation": (
            "local linear sensitivity only; not calibrated covariance, not an "
            "independent edge set, and not exact nonlinear profiling because "
            "only the current transformed residual/Jacobian local affine "
            "profile is preserved; this is not raw-pixel covariance or a "
            "4mm stochastic sigma"
        ),
        **sensitivity,
    }


def shape_residual(factor: dict, centers, rotations) -> np.ndarray:
    frames = factor.get("frames", 0)
    if isinstance(frames, (bool, np.bool_)) or not isinstance(frames, (int, np.integer)):
        raise ValueError("invalid factor frames")
    frames = int(frames)
    centers = _as_array(centers, (frames, 3), "centers")
    current = _local_center_vector(centers, rotations)
    reference = np.asarray(factor.get("reference_center_vector_m"), dtype=float)
    if reference.shape != current.shape or not np.all(np.isfinite(reference)):
        raise ValueError("invalid factor reference")
    sensitivity = np.asarray(factor.get("compact_sqrt_sensitivity"), dtype=float)
    if sensitivity.size == 0:
        sensitivity = np.zeros((0, current.size), dtype=float)
    if (
        sensitivity.ndim != 2
        or sensitivity.shape[1] != current.size
        or not np.all(np.isfinite(sensitivity))
    ):
        raise ValueError("invalid factor sensitivity")
    affine = np.asarray(factor.get("affine_offset", []), dtype=float)
    if affine.shape != (sensitivity.shape[0],) or not np.all(np.isfinite(affine)):
        raise ValueError("invalid factor affine offset")
    return affine + sensitivity @ (current - reference)


def shape_gradient(factor: dict, centers, rotations) -> np.ndarray:
    residual = shape_residual(factor, centers, rotations)
    sensitivity = np.asarray(factor["compact_sqrt_sensitivity"], dtype=float)
    if sensitivity.size == 0:
        sensitivity = np.zeros((0, residual.size if residual.size else 3 * (int(factor["frames"]) - 1)), dtype=float)
    if sensitivity.ndim != 2 or not np.all(np.isfinite(sensitivity)):
        raise ValueError("invalid factor sensitivity")
    return sensitivity.T @ residual


def _capture_shape_factor(bundle, result, solver_args) -> dict:
    frames = int(solver_args[0])
    points = int(solver_args[1])
    expected = 6 * (frames - 1) + 3 * points + 3
    jac = result.jac.toarray() if hasattr(result.jac, "toarray") else np.asarray(result.jac)
    if jac.shape[1] != expected:
        raise ValueError("Jacobian/variable packing mismatch")
    rotations, centers, _, _ = bundle._unpack(result.x, frames, points)
    return build_shape_factor(
        jac,
        centers,
        rotations,
        point_count=points,
        residual=result.fun,
    ) | {
        "solver_optimality": float(result.optimality),
        "solver_cost": float(result.cost),
    }


def solve_with_shape_factor(bundle, *args, **kwargs):
    """Run the original stereo bundle solver and append prototype diagnostics."""
    original = bundle.least_squares
    captured = []

    def intercept(*solver_args, **solver_kwargs):
        result = original(*solver_args, **solver_kwargs)
        captured.append((result, solver_kwargs["args"]))
        return result

    bundle.least_squares = intercept
    try:
        answer = bundle.solve_stereo_window(*args, **kwargs)
    finally:
        bundle.least_squares = original
    if captured and "diagnostics" in answer:
        try:
            record = _capture_shape_factor(bundle, *captured[-1])
        except (ValueError, np.linalg.LinAlgError) as error:
            record = {
                "diagnostic_only": True,
                "available": False,
                "reason": str(error),
                "metadata": {
                    "metric_source": "stereo_window_shape_factor_v1",
                    "calibrated_covariance": False,
                    "prototype_only": True,
                    "available_for_graph": False,
                },
            }
        answer["diagnostics"] = {
            **answer["diagnostics"],
            "stereo_window_shape_factor": {
                **record,
                "solver_accepted": bool(answer.get("accepted", False)),
                "not_admissible_for_graph": True,
            },
        }
    return answer
