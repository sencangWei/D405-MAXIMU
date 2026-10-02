"""Physical camera-to-body lever transform for shared stereo observations.

Pure experimental helper.  It rewrites only ``metric_displacement_camera_i_m``
in existing shared body-frame stereo rows using raw physical IR_i observations
and VINS/reference body rotations.  No ground truth, scoring, thresholds, or
runner state are used.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any, Sequence

import numpy as np

from ego_vio.vio.dual_ir_factors import (
    _as_body_t_camera,
    _as_rotations,
    _as_strict_times,
    _interval_has_gap,
    _observation_index,
)


TIMESTAMP_BINDING_TOLERANCE_S = 0.010


def transform_shared_stereo_body_lever(
    reference_times: Sequence[float],
    reference_body_rotations: Sequence[Any],
    eye_candidates: Sequence[dict[str, Any]],
    original_shared_rows: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Recompute selected shared body_i stereo deltas with VINS-relative lever.

    Candidate dictionaries are already deduplicated physical IR observations with:
    eye, first/second indices and times, metric_displacement_camera_i_m,
    metric_displacement_frame, observation_confidence, and body_t_camera.
    The caller must reproduce the baseline same-eye own-confidence deduplication;
    this helper rejects duplicate ``(reference_pair, eye)`` candidates rather
    than silently choosing or averaging them.
    """

    times = _as_strict_times("reference_times", reference_times)
    rotations = _as_rotations(
        "reference_body_rotations",
        reference_body_rotations,
        times.size,
    )
    candidates_by_pair = _candidate_map(times, eye_candidates)
    copied_rows = deepcopy(list(original_shared_rows))
    tie_rows = 0
    for row in copied_rows:
        pair = _validate_shared_row(row, times)
        candidates = candidates_by_pair.get(pair)
        if not candidates:
            raise ValueError("unmatched shared stereo row")
        selected_delta, selection = _select_delta(candidates, rotations, pair)
        row_confidence = _unit_interval(row, "pnp_inlier_ratio")
        if abs(row_confidence - selection["confidence"]) > 1e-12:
            raise ValueError("shared stereo row confidence does not match selected candidate")
        tie_rows += int(selection["tie_count"] > 1)
        row["metric_displacement_camera_i_m"] = [
            float(value) for value in selected_delta
        ]

    diagnostic = {
        "schema": "physical_stereo_lever_diagnostic_v1",
        "status": "EXPERIMENTAL_NOT_ACCEPTED",
        "accepted": False,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "formula": "Rbc*d_cam - Rv_i.T*Rv_j*t_body_camera + t_body_camera",
        "input_candidate_count": len(eye_candidates),
        "input_row_count": len(original_shared_rows),
        "output_row_count": len(copied_rows),
        "tie_row_count": tie_rows,
    }
    return copied_rows, diagnostic


def _candidate_map(
    times: np.ndarray,
    eye_candidates: Sequence[dict[str, Any]],
) -> dict[tuple[int, int], list[dict[str, Any]]]:
    mapped: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for candidate in eye_candidates:
        eye = candidate.get("eye")
        if eye not in ("left", "right"):
            raise ValueError("candidate eye must be 'left' or 'right'")
        expected_frame = f"infrared_{eye}_camera_i"
        if candidate.get("metric_displacement_frame") != expected_frame:
            raise ValueError("candidate metric displacement frame mismatch")
        first_raw = _raw_observation_index(candidate, "first_index")
        second_raw = _raw_observation_index(candidate, "second_index")
        if second_raw <= first_raw:
            raise ValueError("observation endpoints must be ordered")
        first, second = _reference_pair_from_timestamps(candidate, times)
        _validate_reference_interval(times, first, second)
        confidence = _unit_interval(candidate, "observation_confidence")
        d_cam = _vec3(candidate.get("metric_displacement_camera_i_m"), "metric_displacement_camera_i_m")
        body_r_camera, body_t_camera = _as_body_t_camera(
            f"{eye}_body_t_camera",
            candidate.get("body_t_camera"),
        )
        pair = (first, second)
        if any(existing["eye"] == eye for existing in mapped[pair]):
            raise ValueError("duplicate same-eye stereo candidate for reference pair")
        mapped[pair].append({
            "eye": eye,
            "confidence": confidence,
            "d_cam": d_cam,
            "body_r_camera": body_r_camera,
            "body_t_camera": body_t_camera,
        })
    return mapped


def _validate_shared_row(row: dict[str, Any], times: np.ndarray) -> tuple[int, int]:
    if row.get("accepted") is not True:
        raise ValueError("shared stereo row must be accepted")
    if row.get("metric_displacement_frame") != "body_i":
        raise ValueError("shared stereo row must be in body_i frame")
    first = _observation_index(row, "first_index", times.size)
    second = _observation_index(row, "second_index", times.size)
    mapped_first, mapped_second = _reference_pair_from_timestamps(row, times)
    if (first, second) != (mapped_first, mapped_second):
        raise ValueError("shared stereo row indices do not bind timestamps")
    _validate_reference_interval(times, first, second)
    _vec3(row.get("metric_displacement_camera_i_m"), "metric_displacement_camera_i_m")
    return first, second


def _reference_pair_from_timestamps(
    item: dict[str, Any],
    times: np.ndarray,
) -> tuple[int, int]:
    first_t = float(item.get("first_t_sec"))
    second_t = float(item.get("second_t_sec"))
    if not np.isfinite(first_t) or not np.isfinite(second_t):
        raise ValueError("observation timeline does not bind reference times")
    first = _nearest_reference_index(times, first_t)
    second = _nearest_reference_index(times, second_t)
    if first is None or second is None:
        raise ValueError("observation timeline does not bind reference times")
    return first, second


def _validate_reference_interval(times: np.ndarray, first: int, second: int) -> None:
    if second <= first:
        raise ValueError("observation endpoints must be ordered")
    if _interval_has_gap(times, first, second):
        raise ValueError("observation interval has a gap")


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


def _raw_observation_index(item: dict[str, Any], key: str) -> int:
    value = item.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{key} must be an integer index")
    index = int(value)
    if index < 0:
        raise ValueError(f"{key} must be a non-negative integer index")
    return index


def _select_delta(
    candidates: list[dict[str, Any]],
    rotations: np.ndarray,
    pair: tuple[int, int],
) -> tuple[np.ndarray, dict[str, Any]]:
    first, second = pair
    best = max(candidate["confidence"] for candidate in candidates)
    tied = [
        candidate
        for candidate in candidates
        if np.isclose(candidate["confidence"], best, rtol=0.0, atol=0.0)
    ]
    tied = sorted(tied, key=lambda candidate: candidate["eye"])
    deltas = [_physical_body_delta(candidate, rotations, first, second) for candidate in tied]
    if len(deltas) == 1:
        return deltas[0], {
            "confidence": float(best),
            "tie_count": 1,
            "eyes": [tied[0]["eye"]],
        }
    return np.mean(deltas, axis=0), {
        "confidence": float(best),
        "tie_count": len(deltas),
        "eyes": [candidate["eye"] for candidate in tied],
    }


def _physical_body_delta(
    candidate: dict[str, Any],
    rotations: np.ndarray,
    first: int,
    second: int,
) -> np.ndarray:
    return (
        candidate["body_r_camera"] @ candidate["d_cam"]
        - rotations[first].T @ rotations[second] @ candidate["body_t_camera"]
        + candidate["body_t_camera"]
    )


def _vec3(value: Any, label: str) -> np.ndarray:
    vector = np.asarray(value, dtype=float)
    if vector.shape != (3,) or not np.all(np.isfinite(vector)):
        raise ValueError(f"{label} must be a finite 3-vector")
    return vector


def _unit_interval(item: dict[str, Any], key: str) -> float:
    value = float(item.get(key))
    if not np.isfinite(value) or value < 0.0 or value > 1.0:
        raise ValueError(f"{key} must be finite in [0, 1]")
    return value
