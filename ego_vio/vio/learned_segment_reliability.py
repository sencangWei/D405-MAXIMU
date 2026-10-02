"""Onboard-only reliability candidate for learned dual-IR motion factors.

This module is intentionally not wired into the production runner.  It exposes a
pure function for bounded regression experiments against existing artifacts.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import math
from typing import Any, Sequence

import numpy as np

from ego_vio.vio.dual_ir_factors import (
    _as_rotations,
    _as_strict_times,
    _observation_index,
)


DURATION_MIN_SEC = 1.0
WINDOW_HALF_WIDTH_SEC = 1.0
MIN_PAIRED_EDGES = 8
OWN_RESIDUAL_BAD_M = 0.010
JOINT_BAD_FRACTION_THRESHOLD = 0.25
WEIGHTED_RESIDUAL_P95_M = 0.015


def apply_learned_segment_reliability(
    reference_times: Sequence[float],
    reference_body_rotations: Sequence[Sequence[Sequence[float]]],
    motion_factors: Sequence[dict[str, Any]],
    shared_stereo_observations: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Zero final confidence for long paired learned edges in suspicious windows.

    The rule is fixed and onboard-only:
    duration >= 1s, pair midtime within center ±1s, at least 8 paired long edges,
    both eye own stereo residuals >10mm fraction >0.25, and final-confidence
    weighted learned-vs-stereo residual P95 >15mm.

    Inputs are never mutated.  All factors are preserved; affected paired long
    factors only have ``confidence`` changed to 0.0 in the returned copy.
    """

    times = _validate_reference_times(reference_times)
    rotations = _validate_reference_rotations(reference_body_rotations, len(times))
    stereo_by_pair = _validate_stereo_observations(shared_stereo_observations, times)
    factors_by_pair, copied_factors = _validate_motion_factors(motion_factors, len(times))

    paired_edges, unpaired_factor_count = _build_paired_edges(
        factors_by_pair,
        stereo_by_pair,
        times,
        rotations,
    )
    long_edges = [edge for edge in paired_edges if edge["duration_sec"] >= DURATION_MIN_SEC]
    windows = [_window_stats(long_edges, center_time) for center_time in times]
    hit_windows = [window for window in windows if window["hit"]]
    affected_pairs = {
        edge["pair"]
        for edge in long_edges
        if any(abs(edge["mid_t_sec"] - window["center_t_sec"]) <= WINDOW_HALF_WIDTH_SEC for window in hit_windows)
    }

    zeroed_factor_count = 0
    for factor in copied_factors:
        pair = (int(factor["first_index"]), int(factor["second_index"]))
        if pair in affected_pairs and factor.get("eye") in ("left", "right"):
            if float(factor["confidence"]) != 0.0:
                zeroed_factor_count += 1
            factor["confidence"] = 0.0

    diagnostic = {
        "schema": "learned_segment_reliability_diagnostic_v1",
        "status": "EXPERIMENTAL_NOT_ACCEPTED",
        "accepted": False,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "rule": {
            "duration_min_sec": DURATION_MIN_SEC,
            "window_half_width_sec": WINDOW_HALF_WIDTH_SEC,
            "min_paired_edges": MIN_PAIRED_EDGES,
            "joint_bad_own_residual_m": OWN_RESIDUAL_BAD_M,
            "joint_bad_fraction_threshold_exclusive": JOINT_BAD_FRACTION_THRESHOLD,
            "weighted_residual_p95_m_threshold_exclusive": WEIGHTED_RESIDUAL_P95_M,
            "confidence_field": "confidence",
            "membership": "abs(pair_midtime - reference_time) <= window_half_width_sec",
            "supervision": "onboard_only_no_gt_no_precision",
        },
        "input_factor_count": len(motion_factors),
        "paired_edge_count": len(paired_edges),
        "long_paired_edge_count": len(long_edges),
        "unpaired_factor_count": unpaired_factor_count,
        "eligible_window_count": sum(1 for window in windows if window["edge_count"] >= MIN_PAIRED_EDGES),
        "hit_window_count": len(hit_windows),
        "hit_intervals": _hit_intervals(hit_windows, times[0] if len(times) else 0.0),
        "affected_pair_count": len(affected_pairs),
        "zeroed_factor_count": zeroed_factor_count,
        "affected_pairs": [
            {"first_index": first, "second_index": second}
            for first, second in sorted(affected_pairs)
        ],
        "top_hit_windows": [
            _compact_window(window, times[0])
            for window in sorted(
                hit_windows,
                key=lambda window: (
                    window["joint_bad_fraction"],
                    window["weighted_residual_p95_m"],
                ),
                reverse=True,
            )[:5]
        ],
        "max_joint_bad_window": _compact_window(
            max(
                (window for window in windows if window["edge_count"] >= MIN_PAIRED_EDGES),
                key=lambda window: window["joint_bad_fraction"],
                default=None,
            ),
            times[0],
        ),
        "max_weighted_residual_p95_window": _compact_window(
            max(
                (window for window in windows if window["edge_count"] >= MIN_PAIRED_EDGES),
                key=lambda window: window["weighted_residual_p95_m"],
                default=None,
            ),
            times[0],
        ),
    }
    return copied_factors, diagnostic


def _validate_reference_times(reference_times: Sequence[float]) -> np.ndarray:
    return _as_strict_times("reference_times", reference_times)


def _validate_reference_rotations(
    reference_body_rotations: Sequence[Sequence[Sequence[float]]],
    expected_count: int,
) -> np.ndarray:
    return _as_rotations("reference_body_rotations", reference_body_rotations, expected_count)


def _check_index(first: int, second: int, reference_count: int) -> None:
    if first < 0 or second < 0 or first >= reference_count or second >= reference_count:
        raise ValueError("factor/stereo index out of reference range")
    if second <= first:
        raise ValueError("second_index must be greater than first_index")


def _factor_index(factor: dict[str, Any], key: str, limit: int) -> int:
    value = factor.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"motion factor has malformed {key}")
    index = int(value)
    if index < 0 or index >= limit:
        raise ValueError(f"motion factor {key} is out of range")
    return index


def _finite_vec3(value: Any, label: str) -> np.ndarray:
    vector = np.asarray(value, dtype=float)
    if vector.shape != (3,) or not np.all(np.isfinite(vector)):
        raise ValueError(f"{label} must be a finite 3-vector")
    return vector


def _finite_float(value: Any, label: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _require_reference_time(
    times: np.ndarray,
    index: int,
    timestamp: float,
    label: str,
) -> None:
    if not np.isclose(float(times[index]), timestamp, rtol=0.0, atol=1e-9):
        raise ValueError(f"{label} must match reference endpoint time")


def _validate_stereo_observations(
    shared_stereo_observations: Sequence[dict[str, Any]],
    times: np.ndarray,
) -> dict[tuple[int, int], dict[str, Any]]:
    stereo_by_pair: dict[tuple[int, int], dict[str, Any]] = {}
    for row in shared_stereo_observations:
        if row.get("accepted") is not True:
            continue
        if row.get("metric_displacement_frame") != "body_i":
            raise ValueError("shared stereo observations must use metric_displacement_frame='body_i'")
        first = _observation_index(row, "first_index", len(times))
        second = _observation_index(row, "second_index", len(times))
        _check_index(first, second, len(times))
        first_t = _finite_float(row["first_t_sec"], "first_t_sec")
        second_t = _finite_float(row["second_t_sec"], "second_t_sec")
        if second_t <= first_t:
            raise ValueError("stereo observation times must be increasing")
        _require_reference_time(times, first, first_t, "first_t_sec reference binding")
        _require_reference_time(times, second, second_t, "second_t_sec reference binding")
        _finite_vec3(row["metric_displacement_camera_i_m"], "metric_displacement_camera_i_m")
        pair = (first, second)
        if pair in stereo_by_pair:
            raise ValueError("duplicate shared stereo pair")
        stereo_by_pair[pair] = row
    return stereo_by_pair


def _validate_motion_factors(
    motion_factors: Sequence[dict[str, Any]],
    reference_count: int,
) -> tuple[dict[tuple[int, int], dict[str, dict[str, Any]]], list[dict[str, Any]]]:
    copied_factors = deepcopy(list(motion_factors))
    factors_by_pair: dict[tuple[int, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    for factor in copied_factors:
        eye = factor.get("eye")
        if eye not in ("left", "right"):
            raise ValueError("motion factor eye must be left or right")
        first = _factor_index(factor, "first_index", reference_count)
        second = _factor_index(factor, "second_index", reference_count)
        _check_index(first, second, reference_count)
        pair = (first, second)
        if eye in factors_by_pair[pair]:
            raise ValueError("duplicate motion factor pair-eye")
        _finite_vec3(factor["metric_displacement_world_m"], "metric_displacement_world_m")
        confidence = _finite_float(factor["confidence"], "confidence")
        if confidence < 0.0 or confidence > 1.0:
            raise ValueError("confidence must be in [0, 1]")
        own = _finite_float(factor["own_stereo_residual_m"], "own_stereo_residual_m")
        if own < 0.0:
            raise ValueError("own_stereo_residual_m must be non-negative")
        factors_by_pair[pair][eye] = factor
    return factors_by_pair, copied_factors


def _build_paired_edges(
    factors_by_pair: dict[tuple[int, int], dict[str, dict[str, Any]]],
    stereo_by_pair: dict[tuple[int, int], dict[str, Any]],
    times: np.ndarray,
    rotations: np.ndarray,
) -> tuple[list[dict[str, Any]], int]:
    paired_edges = []
    unpaired_factor_count = 0
    for pair, by_eye in factors_by_pair.items():
        if pair not in stereo_by_pair:
            raise ValueError("missing stereo observation for motion factor pair")
        if "left" not in by_eye or "right" not in by_eye:
            unpaired_factor_count += len(by_eye)
            continue
        row = stereo_by_pair[pair]
        first, second = pair
        duration = float(times[second] - times[first])
        raw_body = _finite_vec3(row["metric_displacement_camera_i_m"], "metric_displacement_camera_i_m")
        raw_world = rotations[first] @ raw_body
        left = by_eye["left"]
        right = by_eye["right"]
        left_delta = _finite_vec3(left["metric_displacement_world_m"], "metric_displacement_world_m")
        right_delta = _finite_vec3(right["metric_displacement_world_m"], "metric_displacement_world_m")
        left_conf = float(left["confidence"])
        right_conf = float(right["confidence"])
        if left_conf + right_conf > 0.0:
            learned = (left_delta * left_conf + right_delta * right_conf) / (left_conf + right_conf)
        else:
            learned = (left_delta + right_delta) / 2.0
        paired_edges.append(
            {
                "pair": pair,
                "mid_t_sec": float((times[first] + times[second]) / 2.0),
                "duration_sec": duration,
                "joint_bad": (
                    float(left["own_stereo_residual_m"]) > OWN_RESIDUAL_BAD_M
                    and float(right["own_stereo_residual_m"]) > OWN_RESIDUAL_BAD_M
                ),
                "weighted_residual_m": float(np.linalg.norm(learned - raw_world)),
                "own_max_residual_m": max(
                    float(left["own_stereo_residual_m"]),
                    float(right["own_stereo_residual_m"]),
                ),
            }
        )
    return paired_edges, unpaired_factor_count


def _percentile(values: list[float], percent: float) -> float | None:
    finite = np.asarray([value for value in values if math.isfinite(value)], dtype=float)
    if finite.size == 0:
        return None
    return float(np.percentile(finite, percent))


def _window_stats(edges: list[dict[str, Any]], center_time: float) -> dict[str, Any]:
    members = [
        edge
        for edge in edges
        if abs(float(edge["mid_t_sec"]) - center_time) <= WINDOW_HALF_WIDTH_SEC
    ]
    joint_bad_count = sum(1 for edge in members if edge["joint_bad"])
    joint_bad_fraction = joint_bad_count / len(members) if members else 0.0
    weighted_residual_p95 = _percentile(
        [float(edge["weighted_residual_m"]) for edge in members],
        95,
    )
    hit = (
        len(members) >= MIN_PAIRED_EDGES
        and joint_bad_fraction > JOINT_BAD_FRACTION_THRESHOLD
        and weighted_residual_p95 is not None
        and weighted_residual_p95 > WEIGHTED_RESIDUAL_P95_M
    )
    return {
        "center_t_sec": float(center_time),
        "edge_count": len(members),
        "joint_bad_count": joint_bad_count,
        "joint_bad_fraction": joint_bad_fraction,
        "weighted_residual_p95_m": weighted_residual_p95,
        "weighted_residual_p50_m": _percentile(
            [float(edge["weighted_residual_m"]) for edge in members],
            50,
        ),
        "own_max_residual_p95_m": _percentile(
            [float(edge["own_max_residual_m"]) for edge in members],
            95,
        ),
        "hit": hit,
    }


def _compact_window(window: dict[str, Any] | None, first_time: float) -> dict[str, Any] | None:
    if window is None:
        return None
    return {
        "center_rel_sec": float(window["center_t_sec"] - first_time),
        "edge_count": int(window["edge_count"]),
        "joint_bad_count": int(window["joint_bad_count"]),
        "joint_bad_fraction": float(window["joint_bad_fraction"]),
        "weighted_residual_p50_m": window["weighted_residual_p50_m"],
        "weighted_residual_p95_m": window["weighted_residual_p95_m"],
        "own_max_residual_p95_m": window["own_max_residual_p95_m"],
        "hit": bool(window["hit"]),
    }


def _hit_intervals(hit_windows: list[dict[str, Any]], first_time: float) -> list[dict[str, float]]:
    if not hit_windows:
        return []
    centers = sorted(float(window["center_t_sec"]) for window in hit_windows)
    intervals = []
    start = previous = centers[0]
    for center in centers[1:]:
        if center - previous > 0.08:
            intervals.append({"start_rel_sec": start - first_time, "end_rel_sec": previous - first_time})
            start = center
        previous = center
    intervals.append({"start_rel_sec": start - first_time, "end_rel_sec": previous - first_time})
    return intervals
