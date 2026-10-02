"""Helpers for building right-IR visual factors for VIO graph fusion.

The helper is deliberately pure: it consumes already-metric camera poses,
onboard-refined rotations, stereo observations, and confidences.  It does not
read ground truth or any repository-local reports.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np


MAX_TIME_BINDING_ERROR_S = 0.010
MAX_INTERVAL_SAMPLE_GAP_S = 0.050
RIGHT_IR_FRAME = "infrared_right_camera_i"


def _as_strict_times(name: str, values: Any) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    if array.ndim != 1 or array.size == 0 or not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must be a non-empty finite 1D array")
    if np.any(np.diff(array) <= 0.0):
        raise ValueError(f"{name} must be strictly monotonic")
    return array


def _as_positions(name: str, values: Any, expected_rows: int) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    if array.shape != (expected_rows, 3) or not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must have shape ({expected_rows}, 3)")
    return array


def _as_rotations(name: str, values: Any, expected_rows: int) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    if array.shape != (expected_rows, 3, 3) or not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must have shape ({expected_rows}, 3, 3)")
    _require_rotation_matrices(name, array)
    return array


def _require_rotation_matrices(name: str, matrices: np.ndarray) -> None:
    identity = np.eye(3)
    for index, matrix in enumerate(matrices.reshape((-1, 3, 3))):
        if not np.allclose(matrix.T @ matrix, identity, atol=1e-6):
            raise ValueError(f"{name}[{index}] is not orthonormal")
        if not np.isclose(np.linalg.det(matrix), 1.0, atol=1e-6):
            raise ValueError(f"{name}[{index}] is not a proper rotation")


def _as_body_t_camera(name: str, value: Any) -> tuple[np.ndarray, np.ndarray]:
    matrix = np.asarray(value, dtype=float)
    if matrix.shape != (4, 4) or not np.all(np.isfinite(matrix)):
        raise ValueError(f"{name} must have shape (4, 4)")
    if not np.allclose(matrix[3], [0.0, 0.0, 0.0, 1.0], atol=1e-9):
        raise ValueError(f"{name} must be a rigid transform")
    rotation = matrix[:3, :3]
    _require_rotation_matrices(f"{name} rotation", rotation[None, :, :])
    return rotation, matrix[:3, 3].copy()


def _nearest_index(times: np.ndarray, timestamp: float) -> int | None:
    insertion = int(np.searchsorted(times, timestamp))
    candidates = [index for index in (insertion - 1, insertion) if 0 <= index < times.size]
    if not candidates:
        return None
    nearest = min(candidates, key=lambda index: abs(times[index] - timestamp))
    if abs(float(times[nearest]) - timestamp) > MAX_TIME_BINDING_ERROR_S:
        return None
    return nearest


def _interval_has_gap(times: np.ndarray, first: int, second: int) -> bool:
    if second <= first:
        raise ValueError("second endpoint must be after first endpoint")
    return bool(np.any(np.diff(times[first : second + 1]) > MAX_INTERVAL_SAMPLE_GAP_S))


def _observation_index(observation: dict[str, Any], key: str, limit: int) -> int:
    value = observation.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"accepted observation has malformed {key}")
    index = int(value)
    if index < 0 or index >= limit:
        raise ValueError(f"accepted observation {key} is out of range")
    return index


def _observation_time(observation: dict[str, Any], key: str) -> float:
    try:
        value = float(observation[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"accepted observation has malformed {key}") from exc
    if not np.isfinite(value):
        raise ValueError(f"accepted observation has non-finite {key}")
    return value


def _observation_vector(observation: dict[str, Any]) -> np.ndarray:
    vector = np.asarray(observation.get("metric_displacement_camera_i_m"), dtype=float)
    if vector.shape != (3,) or not np.all(np.isfinite(vector)):
        raise ValueError("accepted observation has malformed metric displacement")
    return vector


def _confidence_summary(confidences: list[float]) -> dict[str, float | int | None]:
    if not confidences:
        return {
            "confidence_count": 0,
            "confidence_min": None,
            "confidence_max": None,
            "confidence_mean": None,
        }
    array = np.asarray(confidences, dtype=float)
    return {
        "confidence_count": int(array.size),
        "confidence_min": float(np.min(array)),
        "confidence_max": float(np.max(array)),
        "confidence_mean": float(np.mean(array)),
    }


def build_secondary_visual_factors(
    primary_times: Sequence[float],
    primary_body_rotations: Sequence[Any],
    secondary_times: Sequence[float],
    secondary_metric_camera_positions: Sequence[Any],
    secondary_camera_rotations: Sequence[Any],
    secondary_body_t_camera: Any,
    observations: Sequence[dict[str, Any] | None],
    observation_confidences: Sequence[float],
    *,
    primary_metric_camera_positions: Sequence[Any] | None = None,
    primary_body_t_camera: Any | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build local visual translation factors from an independent right-IR track.

    All poses are in meters and epoch seconds.  Rotations are 3x3 world-from-body
    or world-from-camera matrices.  Observations are right-IR stereo observations
    in ``infrared_right_camera_i`` coordinates.  The returned displacement is in
    the primary world frame, after re-expressing the secondary right-body motion
    through the first endpoint's secondary body frame and the primary body's
    first-endpoint orientation.  No ground truth is consumed.
    """

    primary_times_array = _as_strict_times("primary_times", primary_times)
    secondary_times_array = _as_strict_times("secondary_times", secondary_times)
    primary_rotations = _as_rotations(
        "primary_body_rotations", primary_body_rotations, primary_times_array.size
    )
    secondary_positions = _as_positions(
        "secondary_metric_camera_positions",
        secondary_metric_camera_positions,
        secondary_times_array.size,
    )
    secondary_camera_rotations_array = _as_rotations(
        "secondary_camera_rotations",
        secondary_camera_rotations,
        secondary_times_array.size,
    )
    body_r_camera, body_t_camera = _as_body_t_camera(
        "secondary_body_t_camera", secondary_body_t_camera
    )
    primary_pair_supplied = (
        primary_metric_camera_positions is not None or primary_body_t_camera is not None
    )
    if primary_pair_supplied and (
        primary_metric_camera_positions is None or primary_body_t_camera is None
    ):
        raise ValueError(
            "primary_metric_camera_positions and primary_body_t_camera must be supplied together"
        )
    primary_body_positions = None
    if primary_pair_supplied:
        primary_positions = _as_positions(
            "primary_metric_camera_positions",
            primary_metric_camera_positions,
            primary_times_array.size,
        )
        _, primary_body_t_camera_translation = _as_body_t_camera(
            "primary_body_t_camera", primary_body_t_camera
        )
        primary_body_positions = primary_positions - np.einsum(
            "nij,j->ni", primary_rotations, primary_body_t_camera_translation
        )

    if len(observations) != len(observation_confidences):
        raise ValueError("observations and observation_confidences must have the same length")
    confidences = np.asarray(observation_confidences, dtype=float)
    if confidences.shape != (len(observations),) or not np.all(np.isfinite(confidences)):
        raise ValueError("observation_confidences must be finite")
    if np.any((confidences < 0.0) | (confidences > 1.0)):
        raise ValueError("observation_confidences must be in [0, 1]")

    world_r_body = secondary_camera_rotations_array @ body_r_camera.T
    world_body_positions = secondary_positions - np.einsum(
        "nij,j->ni", world_r_body, body_t_camera
    )

    by_primary_pair: dict[tuple[int, int], dict[str, Any]] = {}
    counters = {
        "input_observation_count": len(observations),
        "rejected_observation_count": 0,
        "missing_observation_count": 0,
        "zero_confidence_count": 0,
        "missing_endpoint_count": 0,
        "gap_rejected_count": 0,
        "duplicate_count": 0,
        "preferred_count": 0,
        "right_disabled_count": 0,
    }

    for observation, provided_confidence in zip(observations, confidences):
        if observation is None:
            counters["missing_observation_count"] += 1
            continue
        if not observation.get("accepted", False):
            counters["rejected_observation_count"] += 1
            continue
        if provided_confidence == 0.0:
            counters["zero_confidence_count"] += 1
            continue
        if observation.get("metric_displacement_frame") != RIGHT_IR_FRAME:
            raise ValueError("accepted observation is not in infrared_right_camera_i")

        first_secondary = _observation_index(
            observation, "first_index", secondary_times_array.size
        )
        second_secondary = _observation_index(
            observation, "second_index", secondary_times_array.size
        )
        if second_secondary <= first_secondary:
            raise ValueError("accepted observation endpoints are not ordered")
        first_time = _observation_time(observation, "first_t_sec")
        second_time = _observation_time(observation, "second_t_sec")
        if (
            abs(float(secondary_times_array[first_secondary]) - first_time)
            > MAX_TIME_BINDING_ERROR_S
            or abs(float(secondary_times_array[second_secondary]) - second_time)
            > MAX_TIME_BINDING_ERROR_S
        ):
            raise ValueError("accepted observation indices do not bind timestamps")

        first_primary = _nearest_index(primary_times_array, first_time)
        second_primary = _nearest_index(primary_times_array, second_time)
        if first_primary is None or second_primary is None or second_primary <= first_primary:
            counters["missing_endpoint_count"] += 1
            continue
        if _interval_has_gap(secondary_times_array, first_secondary, second_secondary) or _interval_has_gap(
            primary_times_array, first_primary, second_primary
        ):
            counters["gap_rejected_count"] += 1
            continue

        observed_camera_delta = _observation_vector(observation)
        raw_world_delta = (
            secondary_positions[second_secondary] - secondary_positions[first_secondary]
        )
        stereo_world_target = (
            secondary_camera_rotations_array[first_secondary] @ observed_camera_delta
        )
        residual = float(np.linalg.norm(raw_world_delta - stereo_world_target))
        reliability = float(provided_confidence) * min(1.0, 0.008 / max(residual, 1e-12))
        if reliability <= 0.0:
            counters["zero_confidence_count"] += 1
            continue

        stereo_right_body_delta_world = (
            stereo_world_target
            - world_r_body[second_secondary] @ body_t_camera
            + world_r_body[first_secondary] @ body_t_camera
        )
        primary_residual = None
        local_advantage = 1.0
        primary_from_secondary_world = (
            primary_rotations[first_primary] @ world_r_body[first_secondary].T
        )
        if primary_body_positions is not None:
            primary_body_delta = (
                primary_body_positions[second_primary]
                - primary_body_positions[first_primary]
            )
            transformed_stereo_delta = (
                primary_from_secondary_world @ stereo_right_body_delta_world
            )
            primary_residual = float(
                np.linalg.norm(primary_body_delta - transformed_stereo_delta)
            )
            local_advantage = max(
                0.0,
                (primary_residual**2 - residual**2)
                / (primary_residual**2 + residual**2 + 0.008**2),
            )
            if local_advantage == 0.0:
                counters["right_disabled_count"] += 1
                continue
            counters["preferred_count"] += 1
            reliability *= local_advantage

        secondary_body_delta = (
            world_body_positions[second_secondary] - world_body_positions[first_secondary]
        )
        factor_world_target = primary_from_secondary_world @ secondary_body_delta
        factor = {
            "first_index": int(first_primary),
            "second_index": int(second_primary),
            "metric_displacement_world_m": [
                float(value) for value in factor_world_target
            ],
            "confidence": reliability,
            "secondary_stereo_residual_m": residual,
            "primary_stereo_residual_m": primary_residual,
            "local_advantage": local_advantage,
        }
        pair = (int(first_primary), int(second_primary))
        existing = by_primary_pair.get(pair)
        if existing is not None:
            counters["duplicate_count"] += 1
            if reliability <= existing["confidence"]:
                continue
        by_primary_pair[pair] = factor

    factors = [by_primary_pair[key] for key in sorted(by_primary_pair)]
    accepted_confidences = [float(factor["confidence"]) for factor in factors]
    summary: dict[str, Any] = {
        **counters,
        "factor_count": len(factors),
    }
    summary.update(_confidence_summary(accepted_confidences))
    return factors, summary
