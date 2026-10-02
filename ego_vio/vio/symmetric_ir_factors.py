"""Pure symmetric left/right IR relative-motion factor builder.

This module consumes already-metric MASt3R camera tracks plus their own stereo
observations.  It deliberately does not use ground truth and does not anchor
right-eye evidence to a primary-left position track.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np

from ego_vio.vio.dual_ir_factors import (
    _as_body_t_camera,
    _as_positions,
    _as_rotations,
    _as_strict_times,
    _confidence_summary,
    _interval_has_gap,
    _nearest_index,
    _observation_index,
    _observation_time,
    _observation_vector,
)


EYE_FRAMES = {
    "left": "infrared_left_camera_i",
    "right": "infrared_right_camera_i",
}
HUBER_SCALE_M = 0.008


def _validate_confidences(count: int, values: Sequence[float]) -> np.ndarray:
    confidences = np.asarray(values, dtype=float)
    if confidences.shape != (count,) or not np.all(np.isfinite(confidences)):
        raise ValueError("observation_confidences must be finite")
    if np.any((confidences < 0.0) | (confidences > 1.0)):
        raise ValueError("observation_confidences must be in [0, 1]")
    return confidences


def _camera_pose_arrays(
    track: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    eye = track.get("eye")
    if eye not in EYE_FRAMES:
        raise ValueError("track eye must be 'left' or 'right'")
    times = _as_strict_times(f"{eye}_times", track.get("times"))
    positions = _as_positions(
        f"{eye}_metric_camera_positions",
        track.get("metric_camera_positions"),
        times.size,
    )
    camera_rotations = _as_rotations(
        f"{eye}_camera_rotations", track.get("camera_rotations"), times.size
    )
    body_r_camera, body_t_camera = _as_body_t_camera(
        f"{eye}_body_t_camera", track.get("body_t_camera")
    )
    world_r_body = camera_rotations @ body_r_camera.T
    body_positions = positions - np.einsum("nij,j->ni", world_r_body, body_t_camera)
    return times, positions, camera_rotations, world_r_body, body_positions


def _empty_counters(track_count: int, observation_count: int) -> dict[str, int]:
    return {
        "track_count": track_count,
        "input_observation_count": observation_count,
        "rejected_observation_count": 0,
        "missing_observation_count": 0,
        "zero_confidence_count": 0,
        "missing_endpoint_count": 0,
        "gap_rejected_count": 0,
        "duplicate_motion_pair_count": 0,
        "stereo_duplicate_count": 0,
    }


def _motion_factor(candidate: dict[str, Any], confidence: float) -> dict[str, Any]:
    return {
        "first_index": candidate["first_index"],
        "second_index": candidate["second_index"],
        "eye": candidate["eye"],
        "metric_displacement_world_m": [
            float(value) for value in candidate["motion_world_target"]
        ],
        "confidence": float(confidence),
        "own_confidence": float(candidate["own_confidence"]),
        "own_observation_confidence": float(candidate["observation_confidence"]),
        "own_stereo_residual_m": float(candidate["own_stereo_residual_m"]),
    }


def _stereo_row(
    candidate: dict[str, Any], local_delta: np.ndarray, confidence: float
) -> dict[str, Any]:
    return {
        "accepted": True,
        "first_index": candidate["first_index"],
        "second_index": candidate["second_index"],
        "first_t_sec": float(candidate["first_t_sec"]),
        "second_t_sec": float(candidate["second_t_sec"]),
        "metric_displacement_camera_i_m": [float(value) for value in local_delta],
        "metric_displacement_frame": "body_i",
        "scale": 1.0,
        "pnp_inlier_ratio": float(confidence),
        "rotation_error_deg": 0.0,
    }


def _choose_stereo_row(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    best_confidence = max(candidate["observation_confidence"] for candidate in candidates)
    tied = [
        candidate
        for candidate in candidates
        if np.isclose(
            candidate["observation_confidence"], best_confidence, rtol=0.0, atol=0.0
        )
    ]
    if len(tied) == 1:
        candidate = tied[0]
        local_delta = candidate["stereo_body_i_delta"]
    else:
        candidate = sorted(tied, key=lambda item: item["eye"])[0]
        local_delta = np.mean(
            np.asarray([item["stereo_body_i_delta"] for item in tied], dtype=float),
            axis=0,
        )
    return _stereo_row(candidate, local_delta, best_confidence)


def _store_eye_candidate(
    by_pair: dict[tuple[int, int], dict[str, dict[str, Any]]],
    pair: tuple[int, int],
    candidate: dict[str, Any],
) -> bool:
    by_eye = by_pair.setdefault(pair, {})
    existing = by_eye.get(candidate["eye"])
    if existing is None or candidate["own_confidence"] > existing["own_confidence"]:
        candidate["_tie_count"] = 1
        by_eye[candidate["eye"]] = candidate
        return existing is not None
    if candidate["own_confidence"] == existing["own_confidence"]:
        tie_count = int(existing["_tie_count"])
        next_count = tie_count + 1
        existing["motion_world_target"] = (
            existing["motion_world_target"] * tie_count
            + candidate["motion_world_target"]
        ) / next_count
        existing["stereo_body_i_delta"] = (
            existing["stereo_body_i_delta"] * tie_count
            + candidate["stereo_body_i_delta"]
        ) / next_count
        existing["observation_confidence"] = max(
            existing["observation_confidence"], candidate["observation_confidence"]
        )
        existing["own_stereo_residual_m"] = (
            existing["own_stereo_residual_m"] * tie_count
            + candidate["own_stereo_residual_m"]
        ) / next_count
        existing["_tie_count"] = next_count
    return True


def build_symmetric_ir_factors(
    reference_times: Sequence[float],
    reference_body_rotations: Sequence[Any],
    tracks: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Build eye-symmetric local MASt3R relative-motion proposals.

    ``reference_body_rotations`` are neutral reference world-from-body rotations
    on ``reference_times``.  Each accepted track observation must already be in
    that track's physical IR frame: ``infrared_left_camera_i`` for left tracks
    and ``infrared_right_camera_i`` for right tracks.
    """

    reference_times_array = _as_strict_times("reference_times", reference_times)
    reference_rotations = _as_rotations(
        "reference_body_rotations",
        reference_body_rotations,
        reference_times_array.size,
    )
    total_observations = sum(len(track.get("observations", [])) for track in tracks)
    counters = _empty_counters(len(tracks), total_observations)
    by_pair: dict[tuple[int, int], dict[str, dict[str, Any]]] = {}

    for track in tracks:
        eye = track.get("eye")
        if eye not in EYE_FRAMES:
            raise ValueError("track eye must be 'left' or 'right'")
        expected_frame = EYE_FRAMES[eye]
        times, positions, camera_rotations, world_r_body, body_positions = _camera_pose_arrays(
            track
        )
        _, body_t_camera = _as_body_t_camera(
            f"{eye}_body_t_camera", track.get("body_t_camera")
        )
        observations = track.get("observations")
        confidences_input = track.get("observation_confidences")
        if observations is None or confidences_input is None:
            raise ValueError("track must include observations and observation_confidences")
        if len(observations) != len(confidences_input):
            raise ValueError(
                "observations and observation_confidences must have the same length"
            )
        confidences = _validate_confidences(len(observations), confidences_input)

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
            if observation.get("metric_displacement_frame") != expected_frame:
                raise ValueError(f"accepted {eye} observation is not in {expected_frame}")

            first_track = _observation_index(observation, "first_index", times.size)
            second_track = _observation_index(observation, "second_index", times.size)
            if second_track <= first_track:
                raise ValueError("accepted observation endpoints are not ordered")
            first_time = _observation_time(observation, "first_t_sec")
            second_time = _observation_time(observation, "second_t_sec")
            if (
                abs(float(times[first_track]) - first_time) > 0.010
                or abs(float(times[second_track]) - second_time) > 0.010
            ):
                raise ValueError("accepted observation indices do not bind timestamps")

            first_reference = _nearest_index(reference_times_array, first_time)
            second_reference = _nearest_index(reference_times_array, second_time)
            if (
                first_reference is None
                or second_reference is None
                or second_reference <= first_reference
            ):
                counters["missing_endpoint_count"] += 1
                continue
            if _interval_has_gap(times, first_track, second_track) or _interval_has_gap(
                reference_times_array, first_reference, second_reference
            ):
                counters["gap_rejected_count"] += 1
                continue

            observed_camera_delta = _observation_vector(observation)
            raw_camera_delta_world = positions[second_track] - positions[first_track]
            stereo_camera_delta_world = camera_rotations[first_track] @ observed_camera_delta
            residual = float(np.linalg.norm(raw_camera_delta_world - stereo_camera_delta_world))
            own_confidence = float(provided_confidence) * min(
                1.0, HUBER_SCALE_M / max(residual, 1e-12)
            )
            if own_confidence <= 0.0:
                counters["zero_confidence_count"] += 1
                continue

            stereo_body_delta_world = (
                stereo_camera_delta_world
                - world_r_body[second_track] @ body_t_camera
                + world_r_body[first_track] @ body_t_camera
            )
            stereo_body_i_delta = world_r_body[first_track].T @ stereo_body_delta_world
            track_body_delta_world = body_positions[second_track] - body_positions[first_track]
            motion_body_i_delta = world_r_body[first_track].T @ track_body_delta_world
            motion_world_target = reference_rotations[first_reference] @ motion_body_i_delta

            pair = (int(first_reference), int(second_reference))
            candidate = {
                "first_index": pair[0],
                "second_index": pair[1],
                "first_t_sec": first_time,
                "second_t_sec": second_time,
                "eye": str(eye),
                "motion_world_target": motion_world_target,
                "stereo_body_i_delta": stereo_body_i_delta,
                "observation_confidence": float(provided_confidence),
                "own_confidence": own_confidence,
                "own_stereo_residual_m": residual,
            }
            if _store_eye_candidate(by_pair, pair, candidate):
                counters["duplicate_motion_pair_count"] += 1
                counters["stereo_duplicate_count"] += 1

    motion_factors: list[dict[str, Any]] = []
    stereo_observations: list[dict[str, Any]] = []
    for pair in sorted(by_pair):
        candidates = sorted(by_pair[pair].values(), key=lambda item: item["eye"])
        own_sum = sum(candidate["own_confidence"] for candidate in candidates)
        own_max = max(candidate["own_confidence"] for candidate in candidates)
        confidence_scale = min(1.0, own_max / own_sum) if own_sum > 0.0 else 0.0
        for candidate in candidates:
            motion_factors.append(
                _motion_factor(candidate, candidate["own_confidence"] * confidence_scale)
            )
        stereo_observations.append(_choose_stereo_row(candidates))

    accepted_confidences = [float(factor["confidence"]) for factor in motion_factors]
    summary: dict[str, Any] = {
        **counters,
        "factor_count": len(motion_factors),
        "stereo_observation_count": len(stereo_observations),
    }
    summary.update(_confidence_summary(accepted_confidences))
    return motion_factors, stereo_observations, summary
