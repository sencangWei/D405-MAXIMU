"""Shared stereo SE(3) relative factors from validated physical eye candidates.

Experimental pure helper.  It does not read source reports, ground truth,
scores, or runner state; callers must supply already validated, unique
per-(reference pair, eye) candidates reconstructed from the baseline adapter.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Sequence

import numpy as np
from scipy.spatial.transform import Rotation

from ego_vio.vio.dual_ir_factors import (
    _as_strict_times,
    _interval_has_gap,
    _observation_index,
)


TIMESTAMP_BINDING_TOLERANCE_S = 0.010


def build_shared_stereo_se3_factors(
    reference_times: Sequence[float],
    eye_candidates: Sequence[dict[str, Any]],
    original_shared_rows: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build one SE(3) factor for each baseline shared stereo row.

    Selection is keyed by each original shared row's pair and exact
    ``pnp_inlier_ratio``.  Left/right exact ties are averaged once in the
    virtual body camera representation.  Inputs are copied; no rows are dropped.
    """

    times = _as_strict_times("reference_times", reference_times)
    candidates_by_pair = _candidate_map(times, eye_candidates)
    factors: list[dict[str, Any]] = []
    tie_rows = 0
    for row in original_shared_rows:
        pair = _validate_shared_row(row, times)
        row_confidence = _unit_interval(row, "pnp_inlier_ratio")
        candidates = candidates_by_pair.get(pair)
        if not candidates:
            raise ValueError("missing SE3 candidates for shared stereo row")
        selected = _select_candidates(candidates, row_confidence)
        tie_rows += int(len(selected) > 1)
        factor = _build_factor(row, pair, selected, row_confidence)
        factors.append(factor)

    diagnostic = {
        "schema": "shared_stereo_se3_factor_diagnostic_v1",
        "status": "EXPERIMENTAL_NOT_ACCEPTED",
        "accepted": False,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "input_candidate_count": len(eye_candidates),
        "input_row_count": len(original_shared_rows),
        "output_factor_count": len(factors),
        "tie_row_count": tie_rows,
        "selection_policy": "exact_original_shared_row_observation_confidence",
    }
    return factors, diagnostic


def _candidate_map(
    times: np.ndarray,
    eye_candidates: Sequence[dict[str, Any]],
) -> dict[tuple[int, int], list[dict[str, Any]]]:
    mapped: dict[tuple[int, int], list[dict[str, Any]]] = {}
    seen: set[tuple[int, int, str]] = set()
    for candidate in eye_candidates:
        candidate_copy = deepcopy(dict(candidate))
        if candidate_copy.get("external_ground_truth_used") is not False:
            raise ValueError("SE3 candidate must prove GT independence")
        if candidate_copy.get("slam_supervision") is not False:
            raise ValueError("SE3 candidate must disable SLAM supervision")
        eye = candidate_copy.get("eye")
        if eye not in ("left", "right"):
            raise ValueError("SE3 candidate eye must be 'left' or 'right'")
        first = _observation_index(candidate_copy, "first_index", times.size)
        second = _observation_index(candidate_copy, "second_index", times.size)
        _validate_reference_interval(times, first, second)
        _validate_timestamps_bind_pair(candidate_copy, times, (first, second), "SE3 candidate")
        key = (first, second, eye)
        if key in seen:
            raise ValueError("duplicate SE3 candidate for reference pair and eye")
        seen.add(key)
        confidence = _unit_interval(candidate_copy, "observation_confidence")
        rbc = _so3_matrix(candidate_copy.get("body_R_camera"), "body_R_camera")
        tbc = _vec3(candidate_copy.get("body_t_camera_m"), "body_t_camera_m")
        d_cam = _vec3(
            candidate_copy.get("metric_displacement_camera_i_m"),
            "metric_displacement_camera_i_m",
        )
        expected_frame = f"infrared_{eye}_camera_i"
        if candidate_copy.get("metric_displacement_frame") != expected_frame:
            raise ValueError("SE3 candidate metric displacement frame mismatch")
        if candidate_copy.get("pnp_rotation_mode") != "free":
            raise ValueError("SE3 candidate PnP rotation must be free")
        if candidate_copy.get("pnp_rotation_constrained") is not False:
            raise ValueError("SE3 candidate PnP rotation must be unconstrained")
        z_camera = _rotation_from_quat(
            candidate_copy.get("pnp_rotation_quaternion_xyzw"),
            "pnp_rotation_quaternion_xyzw",
        )
        mapped.setdefault((first, second), []).append(
            {
                "eye": eye,
                "confidence": confidence,
                "body_R_camera": rbc,
                "body_t_camera_m": tbc,
                "metric_displacement_body_i_m": rbc @ d_cam,
                "rotation_body_j_from_i": rbc @ z_camera @ rbc.T,
                "provenance": {
                    "eye": eye,
                    "first_t_sec": float(candidate_copy["first_t_sec"]),
                    "second_t_sec": float(candidate_copy["second_t_sec"]),
                    "observation_confidence": confidence,
                },
            }
        )
    return mapped


def _validate_shared_row(row: dict[str, Any], times: np.ndarray) -> tuple[int, int]:
    if row.get("accepted") is not True:
        raise ValueError("shared stereo row must be accepted")
    first = _observation_index(row, "first_index", times.size)
    second = _observation_index(row, "second_index", times.size)
    _validate_reference_interval(times, first, second)
    _validate_timestamps_bind_pair(row, times, (first, second), "shared stereo row")
    if row.get("metric_displacement_frame") != "body_i":
        raise ValueError("shared stereo row must be in body_i frame")
    _vec3(row.get("metric_displacement_camera_i_m"), "metric_displacement_camera_i_m")
    return first, second


def _validate_reference_interval(times: np.ndarray, first: int, second: int) -> None:
    if second <= first:
        raise ValueError("observation endpoints must be ordered")
    if _interval_has_gap(times, first, second):
        raise ValueError("observation interval has a gap")


def _validate_timestamps_bind_pair(
    item: dict[str, Any],
    times: np.ndarray,
    pair: tuple[int, int],
    label: str,
) -> None:
    first_t = _finite_float(item.get("first_t_sec"), f"{label} first_t_sec")
    second_t = _finite_float(item.get("second_t_sec"), f"{label} second_t_sec")
    mapped = (_nearest_reference_index(times, first_t), _nearest_reference_index(times, second_t))
    if mapped != pair:
        raise ValueError(f"{label} timestamps do not bind reference pair")


def _nearest_reference_index(times: np.ndarray, value: float) -> int | None:
    insertion = int(np.searchsorted(times, value))
    candidates = []
    if insertion < times.size:
        candidates.append(insertion)
    if insertion > 0:
        candidates.append(insertion - 1)
    if not candidates:
        return None
    best = min(candidates, key=lambda index: abs(float(times[index]) - value))
    if abs(float(times[best]) - value) > TIMESTAMP_BINDING_TOLERANCE_S:
        return None
    return best


def _select_candidates(
    candidates: list[dict[str, Any]],
    row_confidence: float,
) -> list[dict[str, Any]]:
    best = max(candidate["confidence"] for candidate in candidates)
    if abs(best - row_confidence) > 1e-12:
        raise ValueError("shared row confidence does not match SE3 candidates")
    selected = [
        candidate
        for candidate in candidates
        if np.isclose(candidate["confidence"], row_confidence, rtol=0.0, atol=0.0)
    ]
    if not selected:
        raise ValueError("no SE3 candidate matches shared row confidence")
    return sorted(selected, key=lambda candidate: candidate["eye"])


def _build_factor(
    row: dict[str, Any],
    pair: tuple[int, int],
    selected: list[dict[str, Any]],
    confidence: float,
) -> dict[str, Any]:
    tbar = np.mean(
        np.asarray([candidate["body_t_camera_m"] for candidate in selected], dtype=float),
        axis=0,
    )
    d_body = np.mean(
        np.asarray(
            [candidate["metric_displacement_body_i_m"] for candidate in selected],
            dtype=float,
        ),
        axis=0,
    )
    rotation_body = _mean_so3(
        [candidate["rotation_body_j_from_i"] for candidate in selected]
    )
    eyes = [candidate["eye"] for candidate in selected]
    return {
        "accepted": True,
        "first_index": pair[0],
        "second_index": pair[1],
        "first_t_sec": float(row["first_t_sec"]),
        "second_t_sec": float(row["second_t_sec"]),
        "confidence": float(confidence),
        "metric_displacement_body_i_m": [float(value) for value in d_body],
        "rotation_body_j_from_i_matrix": rotation_body.tolist(),
        "rotation_body_j_from_i_quaternion_xyzw": Rotation.from_matrix(rotation_body).as_quat().tolist(),
        "virtual_body_R_camera": np.eye(3).tolist(),
        "virtual_body_t_camera_m": [float(value) for value in tbar],
        "residual_model": (
            "Ri.T*(p_j-p_i) + Ri.T*Rj*virtual_body_t_camera_m "
            "- virtual_body_t_camera_m - metric_displacement_body_i_m"
        ),
        "source_shared_row": {
            "pnp_inlier_ratio": float(row["pnp_inlier_ratio"]),
            "metric_displacement_frame": row.get("metric_displacement_frame"),
        },
        "provenance": {
            "eyes": eyes,
            "selected_candidate_count": len(selected),
            "eye_candidates": [candidate["provenance"] for candidate in selected],
        },
    }


def _mean_so3(rotations: Sequence[np.ndarray]) -> np.ndarray:
    if len(rotations) == 1:
        return np.array(rotations[0], dtype=float, copy=True)
    if len(rotations) != 2:
        raise ValueError("SE3 factor supports at most left/right tied rotations")
    first = Rotation.from_matrix(rotations[0])
    second = Rotation.from_matrix(rotations[1])
    midpoint = first * Rotation.from_rotvec(0.5 * (first.inv() * second).as_rotvec())
    return midpoint.as_matrix()


def _so3_matrix(value: Any, label: str) -> np.ndarray:
    matrix = np.asarray(value, dtype=float)
    if (
        matrix.shape != (3, 3)
        or not np.all(np.isfinite(matrix))
        or not np.allclose(matrix.T @ matrix, np.eye(3), atol=1e-6)
        or not np.isclose(np.linalg.det(matrix), 1.0, atol=1e-6)
    ):
        raise ValueError(f"{label} must be a finite proper SO3 matrix")
    return matrix


def _rotation_from_quat(value: Any, label: str) -> np.ndarray:
    quat = np.asarray(value, dtype=float)
    if quat.shape != (4,) or not np.all(np.isfinite(quat)):
        raise ValueError(f"{label} must be a finite xyzw quaternion")
    norm = float(np.linalg.norm(quat))
    if not np.isfinite(norm) or norm == 0.0:
        raise ValueError(f"{label} must be a finite xyzw quaternion")
    return Rotation.from_quat(quat / norm).as_matrix()


def _vec3(value: Any, label: str) -> np.ndarray:
    vector = np.asarray(value, dtype=float)
    if vector.shape != (3,) or not np.all(np.isfinite(vector)):
        raise ValueError(f"{label} must be a finite 3-vector")
    return vector


def _unit_interval(item: dict[str, Any], key: str) -> float:
    value = _finite_float(item.get(key), key)
    if value < 0.0 or value > 1.0:
        raise ValueError(f"{key} must be finite in [0, 1]")
    return value


def _finite_float(value: Any, label: str) -> float:
    number = float(value)
    if not np.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number
