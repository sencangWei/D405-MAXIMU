"""Pure experimental joint stereo-SE(3) pose-only solver.

This module intentionally stays outside production runners.  It optimizes only
body poses on already supplied timestamps using onboard factors: selected
physical stereo SE(3) rows, calibrated gyro relative rotations, VINS consecutive
displacement priors, and existing learned world-displacement edges.  It does
not model velocity, acceleration, gravity, scale, caps, ground truth, or any
selector.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import coo_matrix, lil_matrix
from scipy.sparse.linalg import lsmr
from scipy.spatial.transform import Rotation

from ego_vio.vio.dual_ir_factors import (
    MAX_INTERVAL_SAMPLE_GAP_S,
    _as_body_t_camera,
    _as_positions,
    _as_rotations,
    _as_strict_times,
    _interval_has_gap,
    _observation_index,
)


STEREO_TRANSLATION_SIGMA_M = 0.0125
DEFAULT_STEREO_ROTATION_SIGMA_RAD = np.deg2rad(1.0)
VINS_DISPLACEMENT_SIGMA_M = 1.0
LEARNED_DISPLACEMENT_SIGMA_M = 0.2


@dataclass(frozen=True)
class _StereoFactor:
    first: int
    second: int
    body_r_camera: np.ndarray
    body_t_camera: np.ndarray
    rotation_camera_j_from_i: np.ndarray
    displacement_camera_i_m: np.ndarray
    confidence: float
    rotation_sigma_rad: float


@dataclass(frozen=True)
class _RotationFactor:
    first: int
    second: int
    delta_rotation_body_i_to_body_j: np.ndarray
    confidence: float
    rotation_sigma_rad: float
    imu_gap_bridged: bool


@dataclass(frozen=True)
class _DisplacementFactor:
    first: int
    second: int
    displacement_world_m: np.ndarray
    confidence: float
    sigma_m: float


def solve_joint_stereo_se3(
    reference_times: Sequence[float],
    reference_positions: Sequence[Any],
    reference_rotations: Sequence[Any],
    stereo_se3_factors: Sequence[dict[str, Any]],
    gyro_relative_rotations: Sequence[dict[str, Any]],
    learned_displacement_edges: Sequence[dict[str, Any]] = (),
    *,
    optimize_rotations: bool = True,
) -> dict[str, Any]:
    """Optimize body poses from local onboard SE(3)/motion constraints.

    Rotations are world-from-body matrices.  The first body pose is fixed as the
    gauge.  Every input timestamp/node is retained in the returned arrays.
    """

    times = _as_strict_times("reference_times", reference_times)
    if times.size < 2:
        raise ValueError("joint stereo SE3 solver requires at least two poses")
    positions0 = _as_positions("reference_positions", reference_positions, times.size)
    rotations0 = _as_rotations("reference_rotations", reference_rotations, times.size)
    stereo = _validate_stereo_factors(stereo_se3_factors, times)
    gyro = _validate_rotation_factors(
        gyro_relative_rotations,
        times,
        "gyro_relative_rotations",
    )
    learned = _validate_displacement_factors(
        learned_displacement_edges,
        times,
        "learned_displacement_edges",
        LEARNED_DISPLACEMENT_SIGMA_M,
    )
    vins_gap_count = sum(
        int(_interval_has_gap(times, index, index + 1))
        for index in range(times.size - 1)
    )
    vins = [
        _DisplacementFactor(
            index,
            index + 1,
            positions0[index + 1] - positions0[index],
            1.0,
            VINS_DISPLACEMENT_SIGMA_M,
        )
        for index in range(times.size - 1)
    ]
    _validate_connected_pose_graph(
        times.size,
        stereo,
        gyro,
        vins,
        learned,
        optimize_rotations,
    )
    x0 = _pack(rotations0, positions0, optimize_rotations)

    def residual(parameters: np.ndarray) -> np.ndarray:
        rotations, positions = _unpack(
            parameters,
            rotations0,
            positions0,
            optimize_rotations,
        )
        return _residual_vectorized(rotations, positions, stereo, gyro, vins, learned)

    before = residual(x0)
    if optimize_rotations:
        sparsity = _jacobian_sparsity(
            times.size,
            stereo,
            gyro,
            vins,
            learned,
            optimize_rotations,
        )
        result = least_squares(
            residual,
            x0,
            jac_sparsity=sparsity,
            max_nfev=200,
            xtol=1e-10,
            ftol=1e-10,
            gtol=1e-10,
        )
        rotations, positions = _unpack(result.x, rotations0, positions0, optimize_rotations)
        solver_name = "scipy_least_squares_sparse_finite_difference"
        solver_success = bool(result.success)
        solver_status = int(result.status)
        solver_message = str(result.message)
        solver_nfev = int(result.nfev)
        fixed_linear = None
    else:
        linear = _fixed_rotation_linear_system(rotations0, positions0, stereo, vins, learned)
        solution = lsmr(linear["matrix"], linear["rhs"], atol=1e-10, btol=1e-10, maxiter=2000)
        positions = positions0.copy()
        positions[1:] = np.asarray(solution[0], dtype=float).reshape((times.size - 1, 3))
        rotations = rotations0.copy()
        solver_name = "scipy_sparse_lsmr_fixed_rotation_exact_linear_objective"
        solver_status = int(solution[1])
        solver_success = solver_status in {0, 1, 2, 4, 5}
        solver_message = _lsmr_status_message(solver_status)
        solver_nfev = int(solution[2])
        fixed_linear = {
            "matrix_shape": tuple(int(value) for value in linear["matrix"].shape),
            "matrix_nnz": int(linear["matrix"].nnz),
            "lsmr_normr": float(solution[3]),
            "lsmr_normar": float(solution[4]),
            "lsmr_conda": float(solution[6]),
        }
    if not optimize_rotations:
        after = residual(_pack(rotations, positions, optimize_rotations))
    else:
        after = residual(result.x)
    return {
        "schema": "umi_joint_stereo_se3_pose_only_v1",
        "status": "EXPERIMENTAL_NOT_ACCEPTED",
        "accepted": False,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "model_scope": (
            "pose_only_no_accel_no_velocity_no_gravity_no_scale_no_caps_no_selector"
        ),
        "times": times.copy(),
        "positions": positions,
        "rotations": rotations,
        "diagnostic": {
            "schema": "umi_joint_stereo_se3_pose_only_diagnostic_v1",
            "node_count": int(times.size),
            "stereo_factor_count": len(stereo),
            "gyro_factor_count": len(gyro),
            "vins_displacement_prior_count": len(vins),
            "vins_consecutive_gap_prior_count": int(vins_gap_count),
            "learned_displacement_prior_count": len(learned),
            "first_pose_fixed": True,
            "all_nodes_retained": True,
            "mode": (
                "joint_rotation_position"
                if optimize_rotations
                else "fixed_rotation_position_only"
            ),
            "optimize_rotations": bool(optimize_rotations),
            "whitening": {
                "stereo_translation_sigma_m": STEREO_TRANSLATION_SIGMA_M,
                "default_stereo_rotation_sigma_rad": DEFAULT_STEREO_ROTATION_SIGMA_RAD,
                "vins_displacement_sigma_m": VINS_DISPLACEMENT_SIGMA_M,
                "learned_displacement_sigma_m": LEARNED_DISPLACEMENT_SIGMA_M,
                "stereo_rotation_sigma_rad": _sigma_stats(
                    [factor.rotation_sigma_rad for factor in stereo]
                ),
                "gyro_rotation_sigma_rad": _sigma_stats(
                    [factor.rotation_sigma_rad for factor in gyro]
                ),
            },
            "gyro_imu_gap_bridged_count": int(
                sum(factor.imu_gap_bridged for factor in gyro)
            ),
            "missing_physics": ["acceleration", "velocity", "gravity"],
            "cost_before": float(np.dot(before, before)),
            "cost_after": float(np.dot(after, after)),
            "least_squares_success": solver_success,
            "least_squares_status": solver_status,
            "least_squares_message": solver_message,
            "nfev": solver_nfev,
            "solver": solver_name,
            "fixed_rotation_linear_solver": fixed_linear,
        },
    }


def _pack(rotations: np.ndarray, positions: np.ndarray, optimize_rotations: bool) -> np.ndarray:
    if rotations.shape[0] <= 1:
        return np.zeros(0)
    parts = []
    if optimize_rotations:
        parts.append(Rotation.from_matrix(rotations[1:]).as_rotvec().ravel())
    parts.append(positions[1:].ravel())
    return np.concatenate(parts)


def _unpack(
    parameters: np.ndarray,
    reference_rotations: np.ndarray,
    reference_positions: np.ndarray,
    optimize_rotations: bool,
) -> tuple[np.ndarray, np.ndarray]:
    count = reference_positions.shape[0]
    rotations = reference_rotations.copy()
    positions = reference_positions.copy()
    if count <= 1:
        return rotations, positions
    split = 3 * (count - 1) if optimize_rotations else 0
    if optimize_rotations:
        rotations[1:] = Rotation.from_rotvec(
            parameters[:split].reshape((count - 1, 3))
        ).as_matrix()
    positions[1:] = parameters[split:].reshape((count - 1, 3))
    return rotations, positions


def _residual_vectorized(
    rotations: np.ndarray,
    positions: np.ndarray,
    stereo: list[_StereoFactor],
    gyro: list[_RotationFactor],
    vins: list[_DisplacementFactor],
    learned: list[_DisplacementFactor],
) -> np.ndarray:
    values: list[np.ndarray] = []
    if stereo:
        first = np.asarray([factor.first for factor in stereo], dtype=int)
        second = np.asarray([factor.second for factor in stereo], dtype=int)
        body_r_camera = np.asarray([factor.body_r_camera for factor in stereo])
        body_t_camera = np.asarray([factor.body_t_camera for factor in stereo])
        measured_r = np.asarray([factor.rotation_camera_j_from_i for factor in stereo])
        measured_t = np.asarray([factor.displacement_camera_i_m for factor in stereo])
        confidence = np.sqrt(np.asarray([factor.confidence for factor in stereo]))[:, None]
        rotation_sigma = np.asarray([factor.rotation_sigma_rad for factor in stereo])[:, None]
        predicted_r = np.einsum(
            "nij,njk,nkl,nlm->nim",
            np.swapaxes(body_r_camera, 1, 2),
            np.swapaxes(rotations[second], 1, 2),
            rotations[first],
            body_r_camera,
        )
        rotation_error = Rotation.from_matrix(
            np.einsum("nij,njk->nik", np.swapaxes(measured_r, 1, 2), predicted_r)
        ).as_rotvec()
        camera_delta_world = (
            positions[second]
            + np.einsum("nij,nj->ni", rotations[second], body_t_camera)
            - positions[first]
            - np.einsum("nij,nj->ni", rotations[first], body_t_camera)
        )
        predicted_t = np.einsum(
            "nij,nj->ni",
            np.einsum(
                "nij,njk->nik",
                np.swapaxes(body_r_camera, 1, 2),
                np.swapaxes(rotations[first], 1, 2),
            ),
            camera_delta_world,
        )
        values.append(
            np.column_stack(
                [
                    confidence * rotation_error / rotation_sigma,
                    confidence * (predicted_t - measured_t) / STEREO_TRANSLATION_SIGMA_M,
                ]
            ).ravel()
        )
    if gyro:
        first = np.asarray([factor.first for factor in gyro], dtype=int)
        second = np.asarray([factor.second for factor in gyro], dtype=int)
        measured = np.asarray([factor.delta_rotation_body_i_to_body_j for factor in gyro])
        confidence = np.sqrt(np.asarray([factor.confidence for factor in gyro]))[:, None]
        sigma = np.asarray([factor.rotation_sigma_rad for factor in gyro])[:, None]
        predicted = np.einsum(
            "nij,njk->nik",
            np.swapaxes(rotations[first], 1, 2),
            rotations[second],
        )
        values.append(
            (
                confidence
                * Rotation.from_matrix(
                    np.einsum("nij,njk->nik", np.swapaxes(measured, 1, 2), predicted)
                ).as_rotvec()
                / sigma
            ).ravel()
        )
    values.extend(_displacement_residuals(positions, vins))
    values.extend(_displacement_residuals(positions, learned))
    return np.concatenate(values) if values else np.zeros(0)


def _residual_scalar(
    rotations: np.ndarray,
    positions: np.ndarray,
    stereo: list[_StereoFactor],
    gyro: list[_RotationFactor],
    vins: list[_DisplacementFactor],
    learned: list[_DisplacementFactor],
) -> np.ndarray:
    values: list[np.ndarray] = []
    for factor in stereo:
        predicted_r = (
            factor.body_r_camera.T
            @ rotations[factor.second].T
            @ rotations[factor.first]
            @ factor.body_r_camera
        )
        rotation_error = Rotation.from_matrix(
            factor.rotation_camera_j_from_i.T @ predicted_r
        ).as_rotvec()
        first_camera_world = positions[factor.first] + (
            rotations[factor.first] @ factor.body_t_camera
        )
        second_camera_world = positions[factor.second] + (
            rotations[factor.second] @ factor.body_t_camera
        )
        predicted_t = (
            factor.body_r_camera.T
            @ rotations[factor.first].T
            @ (second_camera_world - first_camera_world)
        )
        confidence_scale = np.sqrt(factor.confidence)
        values.append(confidence_scale * rotation_error / factor.rotation_sigma_rad)
        values.append(
            confidence_scale
            * (predicted_t - factor.displacement_camera_i_m)
            / STEREO_TRANSLATION_SIGMA_M
        )
    for factor in gyro:
        predicted = rotations[factor.first].T @ rotations[factor.second]
        values.append(
            np.sqrt(factor.confidence)
            * Rotation.from_matrix(
                factor.delta_rotation_body_i_to_body_j.T @ predicted
            ).as_rotvec()
            / factor.rotation_sigma_rad
        )
    values.extend(_displacement_residuals(positions, vins))
    values.extend(_displacement_residuals(positions, learned))
    return np.concatenate(values) if values else np.zeros(0)


def _displacement_residuals(
    positions: np.ndarray,
    factors: list[_DisplacementFactor],
) -> list[np.ndarray]:
    if not factors:
        return []
    first = np.asarray([factor.first for factor in factors], dtype=int)
    second = np.asarray([factor.second for factor in factors], dtype=int)
    measured = np.asarray([factor.displacement_world_m for factor in factors])
    confidence = np.sqrt(np.asarray([factor.confidence for factor in factors]))[:, None]
    sigma = np.asarray([factor.sigma_m for factor in factors])[:, None]
    return [((confidence * ((positions[second] - positions[first]) - measured) / sigma).ravel())]


def _fixed_rotation_linear_system(
    rotations: np.ndarray,
    positions0: np.ndarray,
    stereo: list[_StereoFactor],
    vins: list[_DisplacementFactor],
    learned: list[_DisplacementFactor],
) -> dict[str, Any]:
    rows: list[int] = []
    cols: list[int] = []
    data: list[float] = []
    rhs: list[float] = []
    row = 0
    node_count = positions0.shape[0]

    def add_block(first: int, second: int, matrix: np.ndarray, target: np.ndarray, scale: float) -> None:
        nonlocal row
        target_scaled = scale * target
        rhs.extend(target_scaled.tolist())
        for local_axis in range(3):
            for node, sign in ((first, -1.0), (second, 1.0)):
                if node == 0:
                    rhs[row + local_axis] -= sign * scale * float(matrix[local_axis] @ positions0[0])
                    continue
                col_base = 3 * (node - 1)
                for xyz in range(3):
                    rows.append(row + local_axis)
                    cols.append(col_base + xyz)
                    data.append(sign * scale * float(matrix[local_axis, xyz]))
        row += 3

    for factor in stereo:
        matrix = factor.body_r_camera.T @ rotations[factor.first].T
        lever = rotations[factor.second] @ factor.body_t_camera - rotations[factor.first] @ factor.body_t_camera
        target = factor.displacement_camera_i_m - matrix @ lever
        scale = np.sqrt(factor.confidence) / STEREO_TRANSLATION_SIGMA_M
        add_block(factor.first, factor.second, matrix, target, scale)
    for factor in (*vins, *learned):
        matrix = np.eye(3)
        scale = np.sqrt(factor.confidence) / factor.sigma_m
        add_block(factor.first, factor.second, matrix, factor.displacement_world_m, scale)
    matrix = coo_matrix(
        (data, (rows, cols)),
        shape=(row, 3 * max(0, node_count - 1)),
    ).tocsr()
    return {"matrix": matrix, "rhs": np.asarray(rhs, dtype=float)}


def _lsmr_status_message(status: int) -> str:
    messages = {
        0: "x=0 is an exact solution",
        1: "Ax-b is small enough",
        2: "least-squares normal equation residual is small enough",
        3: "condition number estimate exceeded conlim",
        4: "Ax-b is small enough for machine precision",
        5: "least-squares residual is small enough for machine precision",
        6: "condition number estimate exceeded machine precision",
        7: "iteration limit reached",
    }
    return messages.get(int(status), f"unknown lsmr status {status}")


def _validate_stereo_factors(
    rows: Sequence[dict[str, Any]],
    times: np.ndarray,
) -> list[_StereoFactor]:
    seen: set[tuple[int, int]] = set()
    factors: list[_StereoFactor] = []
    for row in rows:
        first = _observation_index(row, "first_index", times.size)
        second = _observation_index(row, "second_index", times.size)
        _validate_edge(times, first, second, "stereo")
        key = (first, second)
        if key in seen:
            raise ValueError("duplicate stereo SE3 factor pair")
        seen.add(key)
        body_r_camera, body_t_camera = _as_body_t_camera(
            "stereo body_t_camera", row.get("body_t_camera")
        )
        rotation = _rotation_matrix(
            row,
            ("rotation_camera_j_from_i", "relative_rotation_camera_j_from_i"),
            "stereo relative rotation",
        )
        displacement = _vec3(
            row.get("metric_displacement_camera_i_m"),
            "metric_displacement_camera_i_m",
        )
        confidence = _confidence(row, "confidence")
        rotation_sigma = _positive_sigma(
            row.get("rotation_sigma_rad", DEFAULT_STEREO_ROTATION_SIGMA_RAD),
            "stereo rotation_sigma_rad",
        )
        factors.append(
            _StereoFactor(
                first,
                second,
                body_r_camera,
                body_t_camera,
                rotation,
                displacement,
                confidence,
                rotation_sigma,
            )
        )
    return factors


def _validate_rotation_factors(
    rows: Sequence[dict[str, Any]],
    times: np.ndarray,
    label: str,
) -> list[_RotationFactor]:
    factors: list[_RotationFactor] = []
    for row in rows:
        first = _observation_index(row, "first_index", times.size)
        second = _observation_index(row, "second_index", times.size)
        if second <= first:
            raise ValueError(f"{label} endpoints must be ordered")
        gap = _interval_has_gap(times, first, second)
        if gap:
            _validate_imu_gap_bridge(row, label)
        rotation = _rotation_matrix(
            row,
            ("delta_rotation_body_i_to_body_j", "relative_rotation_body_i_to_body_j"),
            label,
        )
        sigma = _positive_sigma(row.get("rotation_sigma_rad"), "gyro rotation_sigma_rad")
        factors.append(
            _RotationFactor(
                first,
                second,
                rotation,
                _confidence(row, "confidence", default=1.0),
                sigma,
                gap,
            )
        )
    return factors


def _validate_displacement_factors(
    rows: Sequence[dict[str, Any]],
    times: np.ndarray,
    label: str,
    base_sigma: float,
) -> list[_DisplacementFactor]:
    factors: list[_DisplacementFactor] = []
    for row in rows:
        first = _observation_index(row, "first_index", times.size)
        second = _observation_index(row, "second_index", times.size)
        _validate_edge(times, first, second, label)
        displacement = _vec3(
            row.get("metric_displacement_world_m"),
            "metric_displacement_world_m",
        )
        factors.append(
            _DisplacementFactor(
                first,
                second,
                displacement,
                _confidence(row, "confidence", default=1.0),
                base_sigma,
            )
        )
    return factors


def _validate_edge(times: np.ndarray, first: int, second: int, label: str) -> None:
    if second <= first:
        raise ValueError(f"{label} endpoints must be ordered")
    if _interval_has_gap(times, first, second):
        raise ValueError(f"{label} interval has a gap")


def _rotation_matrix(row: dict[str, Any], keys: tuple[str, ...], label: str) -> np.ndarray:
    value = None
    for key in keys:
        if key in row:
            value = row[key]
            break
    if value is None:
        raise ValueError(f"{label} is missing")
    matrix = np.asarray(value, dtype=float)
    _as_rotations(label, matrix[None, :, :], 1)
    return matrix


def _vec3(value: Any, label: str) -> np.ndarray:
    vector = np.asarray(value, dtype=float)
    if vector.shape != (3,) or not np.all(np.isfinite(vector)):
        raise ValueError(f"{label} must be a finite 3-vector")
    return vector


def _confidence(
    row: dict[str, Any],
    key: str,
    *,
    default: float | None = None,
) -> float:
    value = row.get(key, default)
    if value is None:
        raise ValueError(f"{key} is missing")
    confidence = float(value)
    if not np.isfinite(confidence) or confidence <= 0.0 or confidence > 1.0:
        raise ValueError(f"{key} must be finite in (0, 1]")
    return confidence


def _positive_sigma(value: Any, label: str) -> float:
    if value is None:
        raise ValueError(f"{label} is required")
    sigma = float(value)
    if not np.isfinite(sigma) or sigma <= 0.0:
        raise ValueError(f"{label} must be finite positive")
    return sigma


def _validate_imu_gap_bridge(row: dict[str, Any], label: str) -> None:
    if row.get("imu_coverage_verified") is not True:
        raise ValueError(f"{label} gap bridge requires verified IMU coverage")
    sample_count = row.get("imu_sample_count")
    if isinstance(sample_count, bool) or not isinstance(sample_count, (int, np.integer)):
        raise ValueError(f"{label} gap bridge requires imu_sample_count")
    if int(sample_count) < 2:
        raise ValueError(f"{label} gap bridge requires at least two IMU samples")
    max_gap = float(row.get("max_imu_sample_gap_s", np.inf))
    if not np.isfinite(max_gap) or max_gap > 0.010:
        raise ValueError(f"{label} gap bridge IMU sample gap is too large")
    digest = row.get("imu_source_sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(
        character not in "0123456789abcdefABCDEF" for character in digest
    ):
        raise ValueError(f"{label} gap bridge requires IMU source hash provenance")


def _sigma_stats(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "max": None, "mean": None}
    array = np.asarray(values, dtype=float)
    return {
        "count": int(array.size),
        "min": float(np.min(array)),
        "max": float(np.max(array)),
        "mean": float(np.mean(array)),
    }


def _validate_connected_pose_graph(
    node_count: int,
    stereo: list[_StereoFactor],
    gyro: list[_RotationFactor],
    vins: list[_DisplacementFactor],
    learned: list[_DisplacementFactor],
    optimize_rotations: bool,
) -> None:
    _require_connected_components(
        node_count,
        [(factor.first, factor.second) for factor in (*stereo, *vins, *learned)],
        "pose graph is disconnected/unobservable",
    )
    if optimize_rotations:
        _require_connected_components(
            node_count,
            [(factor.first, factor.second) for factor in (*stereo, *gyro)],
            "rotation pose graph is disconnected/unobservable",
        )


def _require_connected_components(
    node_count: int,
    edges: list[tuple[int, int]],
    message: str,
) -> None:
    parent = list(range(node_count))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(first: int, second: int) -> None:
        root_first = find(first)
        root_second = find(second)
        if root_first != root_second:
            parent[root_second] = root_first

    for first, second in edges:
        union(first, second)
    roots = {find(index) for index in range(node_count)}
    if len(roots) != 1:
        raise ValueError(message)


def _jacobian_sparsity(
    node_count: int,
    stereo: list[_StereoFactor],
    gyro: list[_RotationFactor],
    vins: list[_DisplacementFactor],
    learned: list[_DisplacementFactor],
    optimize_rotations: bool,
):
    variable_count = (6 if optimize_rotations else 3) * max(0, node_count - 1)
    row_count = 6 * len(stereo) + 3 * (len(gyro) + len(vins) + len(learned))
    sparsity = lil_matrix((row_count, variable_count), dtype=int)
    row = 0
    for factor in stereo:
        _mark_edge(
            sparsity,
            row,
            6,
            factor.first,
            factor.second,
            node_count,
            uses_rotation=optimize_rotations,
            uses_position=True,
            optimize_rotations=optimize_rotations,
        )
        row += 6
    for factor in gyro:
        _mark_edge(
            sparsity,
            row,
            3,
            factor.first,
            factor.second,
            node_count,
            uses_rotation=optimize_rotations,
            uses_position=False,
            optimize_rotations=optimize_rotations,
        )
        row += 3
    for factor in vins:
        _mark_edge(
            sparsity,
            row,
            3,
            factor.first,
            factor.second,
            node_count,
            uses_rotation=False,
            uses_position=True,
            optimize_rotations=optimize_rotations,
        )
        row += 3
    for factor in learned:
        _mark_edge(
            sparsity,
            row,
            3,
            factor.first,
            factor.second,
            node_count,
            uses_rotation=False,
            uses_position=True,
            optimize_rotations=optimize_rotations,
        )
        row += 3
    return sparsity.tocsr()


def _mark_edge(
    matrix,
    row: int,
    height: int,
    first: int,
    second: int,
    node_count: int,
    uses_rotation: bool,
    uses_position: bool,
    optimize_rotations: bool,
) -> None:
    rotation_offset = 0
    position_offset = 3 * max(0, node_count - 1) if optimize_rotations else 0
    for node in (first, second):
        if node == 0:
            continue
        if uses_rotation:
            start = rotation_offset + 3 * (node - 1)
            matrix[row : row + height, start : start + 3] = 1
        if uses_position:
            start = position_offset + 3 * (node - 1)
            matrix[row : row + height, start : start + 3] = 1
