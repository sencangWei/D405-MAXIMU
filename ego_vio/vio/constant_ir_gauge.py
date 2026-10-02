"""Constant per-eye SO(3) gauge experiment for dual-IR learned factors.

This module is deliberately isolated from production runners.  It fits one
orientation-only gauge per eye from synced onboard orientations, then rewrites
existing learned factor displacement vectors while preserving all other factor
metadata.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Sequence

import numpy as np

from ego_vio.vio.dual_ir_factors import (
    _as_body_t_camera,
    _as_positions,
    _as_rotations,
    _as_strict_times,
    _interval_has_gap,
    _observation_index,
)


MAX_TIME_BINDING_ERROR_S = 0.010


def transform_existing_motion_factors(
    reference_times: Sequence[float],
    reference_body_rotations: Sequence[Any],
    tracks: Sequence[dict[str, Any]],
    original_motion_factors: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Replace learned displacement vectors using a constant per-eye SO(3) gauge.

    The fit uses only synced orientations:
    ``reference_world_from_body ~= Q_eye @ track_world_from_body``.
    Translation is not used for gauge fitting.  For each existing factor, only
    ``metric_displacement_world_m`` is replaced:

    ``Q_eye @ (pc_j - pc_i) - Rv_j @ t_body_camera + Rv_i @ t_body_camera``.
    """

    ref_times = _as_strict_times("reference_times", reference_times)
    ref_rotations = _as_rotations(
        "reference_body_rotations",
        reference_body_rotations,
        ref_times.size,
    )
    track_by_eye = _validate_tracks(tracks)
    fitted = {
        eye: _fit_eye_gauge(eye, track, ref_times, ref_rotations)
        for eye, track in track_by_eye.items()
    }
    copied = _validated_factor_copies(original_motion_factors, ref_times.size)
    for factor in copied:
        eye = factor.get("eye")
        if eye not in fitted:
            raise ValueError(f"missing track for factor eye {eye!r}")
        data = fitted[eye]
        first = _observation_index(factor, "first_index", ref_times.size)
        second = _observation_index(factor, "second_index", ref_times.size)
        if second <= first:
            raise ValueError("factor endpoints must be ordered")
        if first >= data["track_indices"].size or second >= data["track_indices"].size:
            raise ValueError("factor index out of range for bound track")
        first_track = int(data["track_indices"][first])
        second_track = int(data["track_indices"][second])
        if first_track < 0 or second_track < 0:
            raise ValueError("factor endpoint does not bind to eye track")
        if second_track <= first_track:
            raise ValueError("factor endpoints bind to the same physical track sample")
        _reject_gap(ref_times, first, second, "reference")
        _reject_gap(data["track_times"], first_track, second_track, "track")
        camera_delta = data["positions"][second_track] - data["positions"][first_track]
        lever = data["body_t_camera"]
        replacement = (
            data["constant_world_from_track"] @ camera_delta
            - ref_rotations[second] @ lever
            + ref_rotations[first] @ lever
        )
        factor["metric_displacement_world_m"] = [float(value) for value in replacement]

    diagnostic = {
        "schema": "constant_ir_gauge_diagnostic_v1",
        "status": "EXPERIMENTAL_NOT_ACCEPTED",
        "accepted": False,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "fit": "orientation_only_so3_procrustes_no_translation_no_gt",
        "max_time_binding_error_s": MAX_TIME_BINDING_ERROR_S,
        "input_factor_count": len(original_motion_factors),
        "output_factor_count": len(copied),
        "eye_order": [track.get("eye") for track in tracks],
        "eye_diagnostics": {
            eye: data["diagnostic"] for eye, data in fitted.items()
        },
    }
    return copied, diagnostic


def _validate_tracks(tracks: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    track_by_eye: dict[str, dict[str, Any]] = {}
    for track in tracks:
        eye = track.get("eye")
        if eye not in ("left", "right"):
            raise ValueError("track eye must be 'left' or 'right'")
        if eye in track_by_eye:
            raise ValueError("tracks must have unique eyes")
        track_by_eye[eye] = track
    if not track_by_eye:
        raise ValueError("at least one track is required")
    return track_by_eye


def _fit_eye_gauge(
    eye: str,
    track: dict[str, Any],
    reference_times: np.ndarray,
    reference_rotations: np.ndarray,
) -> dict[str, Any]:
    track_times = _as_strict_times(f"{eye}_times", track.get("times"))
    positions = _as_positions(
        f"{eye}_metric_camera_positions",
        track.get("metric_camera_positions"),
        track_times.size,
    )
    camera_rotations = _as_rotations(
        f"{eye}_camera_rotations",
        track.get("camera_rotations"),
        track_times.size,
    )
    body_r_camera, body_t_camera = _as_body_t_camera(
        f"{eye}_body_t_camera",
        track.get("body_t_camera"),
    )
    track_indices, binding_errors = _bind_reference_to_track(reference_times, track_times)
    valid = track_indices >= 0
    if int(np.count_nonzero(valid)) < 2:
        raise ValueError(f"{eye} track has insufficient orientation coverage")

    track_world_from_body = camera_rotations[track_indices[valid]] @ body_r_camera.T
    reference_world_from_body = reference_rotations[valid]
    constant_world_from_track = _fit_so3(reference_world_from_body, track_world_from_body)
    residuals = _rotation_residuals(
        reference_world_from_body,
        constant_world_from_track @ track_world_from_body,
    )
    diagnostic = {
        "matched_orientation_count": int(np.count_nonzero(valid)),
        "unbound_reference_count": int(np.count_nonzero(~valid)),
        "max_binding_error_s": (
            float(np.max(binding_errors[valid])) if np.any(valid) else None
        ),
        "constant_world_from_track": constant_world_from_track.tolist(),
        "proper_rotation": _is_so3(constant_world_from_track),
        "orientation_degenerate": _orientation_degenerate(track_world_from_body),
        "orientation_residual_deg_p50": float(np.percentile(residuals, 50)),
        "orientation_residual_deg_p95": float(np.percentile(residuals, 95)),
        "orientation_residual_deg_max": float(np.max(residuals)),
    }
    return {
        "track_indices": track_indices,
        "track_times": track_times,
        "positions": positions,
        "body_t_camera": body_t_camera,
        "constant_world_from_track": constant_world_from_track,
        "diagnostic": diagnostic,
    }


def _bind_reference_to_track(
    reference_times: np.ndarray,
    track_times: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    indices = np.full(reference_times.shape, -1, dtype=int)
    errors = np.full(reference_times.shape, np.inf, dtype=float)
    for ref_index, timestamp in enumerate(reference_times):
        insertion = int(np.searchsorted(track_times, timestamp))
        candidates = [
            index
            for index in (insertion - 1, insertion)
            if 0 <= index < track_times.size
        ]
        if not candidates:
            continue
        nearest = min(candidates, key=lambda index: abs(float(track_times[index]) - timestamp))
        error = abs(float(track_times[nearest]) - timestamp)
        if error <= MAX_TIME_BINDING_ERROR_S:
            indices[ref_index] = nearest
            errors[ref_index] = error
    return indices, errors


def _validated_factor_copies(
    original_motion_factors: Sequence[dict[str, Any]],
    reference_count: int,
) -> list[dict[str, Any]]:
    copied = deepcopy(list(original_motion_factors))
    seen: set[tuple[str, int, int]] = set()
    for factor in copied:
        eye = factor.get("eye")
        if eye not in ("left", "right"):
            raise ValueError("factor eye must be 'left' or 'right'")
        first = _observation_index(factor, "first_index", reference_count)
        second = _observation_index(factor, "second_index", reference_count)
        key = (eye, first, second)
        if key in seen:
            raise ValueError("duplicate factor eye/endpoints")
        seen.add(key)
        if second <= first:
            raise ValueError("factor endpoints must be ordered")
        confidence = float(factor.get("confidence"))
        if not np.isfinite(confidence) or confidence < 0.0 or confidence > 1.0:
            raise ValueError("factor confidence must be finite in [0, 1]")
        _validate_optional_unit_interval(factor, "own_confidence")
        _validate_optional_unit_interval(factor, "own_observation_confidence")
        if "own_stereo_residual_m" in factor:
            residual = float(factor["own_stereo_residual_m"])
            if not np.isfinite(residual) or residual < 0.0:
                raise ValueError("own_stereo_residual_m must be finite and non-negative")
        vector = np.asarray(factor.get("metric_displacement_world_m"), dtype=float)
        if vector.shape != (3,) or not np.all(np.isfinite(vector)):
            raise ValueError("metric_displacement_world_m must be a finite 3-vector")
    return copied


def _validate_optional_unit_interval(factor: dict[str, Any], key: str) -> None:
    if key not in factor:
        return
    value = float(factor[key])
    if not np.isfinite(value) or value < 0.0 or value > 1.0:
        raise ValueError(f"{key} must be finite in [0, 1]")


def _reject_gap(times: np.ndarray, first: int, second: int, label: str) -> None:
    lo = min(first, second)
    hi = max(first, second)
    if _interval_has_gap(times, lo, hi):
        raise ValueError(f"{label} interval has a gap")


def _fit_so3(
    reference_world_from_body: np.ndarray,
    track_world_from_body: np.ndarray,
) -> np.ndarray:
    # Maximize sum trace(R_ref.T @ Q @ R_track).  This is Procrustes on
    # matrices, equivalent to fitting all orientation basis vectors.
    cross_covariance = np.zeros((3, 3), dtype=float)
    for reference_rotation, track_rotation in zip(reference_world_from_body, track_world_from_body):
        cross_covariance += reference_rotation @ track_rotation.T
    u, _, vt = np.linalg.svd(cross_covariance)
    correction = np.eye(3)
    correction[2, 2] = np.linalg.det(u @ vt)
    result = u @ correction @ vt
    if not _is_so3(result):
        raise ValueError("constant gauge fit did not produce a proper rotation")
    return result


def _rotation_residuals(
    reference_rotations: np.ndarray,
    fitted_rotations: np.ndarray,
) -> np.ndarray:
    residuals = []
    for reference_rotation, fitted_rotation in zip(reference_rotations, fitted_rotations):
        delta = reference_rotation.T @ fitted_rotation
        cos_angle = np.clip((np.trace(delta) - 1.0) / 2.0, -1.0, 1.0)
        residuals.append(np.rad2deg(np.arccos(cos_angle)))
    return np.asarray(residuals, dtype=float)


def _orientation_degenerate(rotations: np.ndarray) -> bool:
    if rotations.shape[0] < 2:
        return True
    residuals = _rotation_residuals(
        np.repeat(rotations[:1], rotations.shape[0], axis=0),
        rotations,
    )
    return bool(np.max(residuals) < 1e-6)


def _is_so3(matrix: np.ndarray) -> bool:
    return bool(
        matrix.shape == (3, 3)
        and np.all(np.isfinite(matrix))
        and np.allclose(matrix.T @ matrix, np.eye(3), atol=1e-6)
        and np.isclose(np.linalg.det(matrix), 1.0, atol=1e-6)
    )
