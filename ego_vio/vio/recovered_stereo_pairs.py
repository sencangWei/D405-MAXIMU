"""Build shared-stereo templates for recovered IR stereo pairs.

Pure development helper.  It prepares template rows and deduplicated eye
candidate records only; the physical body-lever transform remains responsible
for choosing/averaging cross-eye geometry and replacing placeholder vectors.
"""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Sequence

import numpy as np

from ego_vio.vio.dual_ir_factors import (
    _as_body_t_camera,
    _as_strict_times,
    _interval_has_gap,
    _observation_index,
)


def build_recovered_shared_rows(
    reference_times: Sequence[float],
    original_shared_rows: Sequence[dict[str, Any]],
    original_eye_candidates: Sequence[dict[str, Any]],
    recovered_eye_candidates: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Append recovered pair templates without changing trajectory/timeline policy."""

    times = _as_strict_times("reference_times", reference_times)
    original_rows = deepcopy(list(original_shared_rows))
    original_candidates = deepcopy(list(original_eye_candidates))
    recovered_candidates = deepcopy(list(recovered_eye_candidates))

    original_pairs = _validate_original_rows(original_rows, times)
    original_map = _candidate_map(times, original_candidates, source="original")
    _validate_original_candidate_coverage(original_pairs, original_map)

    selected = dict(original_map)
    recovered_selected = 0
    replaced_original = 0
    deduplicated_recovered = 0
    for candidate in recovered_candidates:
        normalized = _normalize_candidate(times, candidate, source="recovered")
        key = _candidate_key(normalized)
        current = selected.get(key)
        winner = _choose_candidate(current, normalized)
        if current is None:
            recovered_selected += 1
        elif winner is normalized:
            replaced_original += int(current.get("_source") == "original")
            deduplicated_recovered += int(current.get("_source") == "recovered")
        else:
            deduplicated_recovered += 1
        selected[key] = winner

    by_pair = _by_pair(selected.values())
    templates, changed = _refresh_original_rows(original_rows, by_pair)
    appended_pairs = sorted(set(by_pair) - original_pairs)
    for pair in appended_pairs:
        templates.append(_appended_template(pair, by_pair[pair], times))

    deduped_candidates = [
        _public_candidate(candidate)
        for candidate in sorted(selected.values(), key=_candidate_sort_key)
    ]
    if not recovered_candidates:
        deduped_candidates = [_public_candidate(candidate) for candidate in original_map.values()]

    diagnostic = {
        "schema": "recovered_stereo_pairs_diagnostic_v1",
        "status": "EXPERIMENTAL_NOT_ACCEPTED",
        "accepted": False,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "original_row_count": len(original_rows),
        "original_candidate_count": len(original_candidates),
        "recovered_candidate_count": len(recovered_candidates),
        "output_row_count": len(templates),
        "output_candidate_count": len(deduped_candidates),
        "confidence_changed_original_row_count": changed,
        "appended_pair_count": len(appended_pairs),
        "appended_pairs": [[int(first), int(second)] for first, second in appended_pairs],
        "selected_recovered_candidate_count": recovered_selected,
        "replaced_original_same_eye_count": replaced_original,
        "deduplicated_recovered_same_eye_count": deduplicated_recovered,
    }
    return templates, deduped_candidates, diagnostic


def _validate_original_rows(
    rows: list[dict[str, Any]],
    times: np.ndarray,
) -> set[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    for row in rows:
        if row.get("accepted") is not True:
            raise ValueError("original shared rows must be accepted")
        if row.get("metric_displacement_frame") != "body_i":
            raise ValueError("original shared rows must be in body_i frame")
        first = _observation_index(row, "first_index", times.size)
        second = _observation_index(row, "second_index", times.size)
        _validate_pair_times(row, times, first, second)
        pair = (first, second)
        if pair in pairs:
            raise ValueError(f"duplicate original shared pair: {pair}")
        pairs.add(pair)
        _vec3(row.get("metric_displacement_camera_i_m"), "metric_displacement_camera_i_m")
        _confidence(row.get("pnp_inlier_ratio"), "pnp_inlier_ratio")
    return pairs


def _candidate_map(
    times: np.ndarray,
    candidates: list[dict[str, Any]],
    *,
    source: str,
) -> dict[tuple[tuple[int, int], str], dict[str, Any]]:
    mapped: dict[tuple[tuple[int, int], str], dict[str, Any]] = {}
    for candidate in candidates:
        normalized = _normalize_candidate(times, candidate, source=source)
        key = _candidate_key(normalized)
        if key in mapped:
            raise ValueError(f"duplicate same-eye candidate for pair {key[0]}: {key[1]}")
        mapped[key] = normalized
    return mapped


def _normalize_candidate(
    times: np.ndarray,
    candidate: dict[str, Any],
    *,
    source: str,
) -> dict[str, Any]:
    eye = candidate.get("eye")
    if eye not in ("left", "right"):
        raise ValueError("candidate eye must be 'left' or 'right'")
    first = _observation_index(candidate, "reference_first_index", times.size)
    second = _observation_index(candidate, "reference_second_index", times.size)
    _validate_pair_times(candidate, times, first, second)
    raw_first = _raw_index(candidate, "first_index")
    raw_second = _raw_index(candidate, "second_index")
    if raw_second <= raw_first:
        raise ValueError("candidate raw endpoints must be ordered")
    expected_frame = f"infrared_{eye}_camera_i"
    if candidate.get("metric_displacement_frame") != expected_frame:
        raise ValueError("candidate metric displacement frame mismatch")
    _vec3(candidate.get("metric_displacement_camera_i_m"), "metric_displacement_camera_i_m")
    _confidence(candidate.get("observation_confidence"), "observation_confidence")
    _as_body_t_camera(f"{eye}_body_t_camera", candidate.get("body_t_camera"))
    normalized = deepcopy(candidate)
    normalized["_source"] = source
    return normalized


def _validate_pair_times(
    item: dict[str, Any],
    times: np.ndarray,
    first: int,
    second: int,
) -> None:
    if second <= first:
        raise ValueError("observation endpoints must be ordered")
    if _interval_has_gap(times, first, second):
        raise ValueError("observation interval has a gap")
    first_t = _finite_float(item.get("first_t_sec"), "first_t_sec")
    second_t = _finite_float(item.get("second_t_sec"), "second_t_sec")
    if abs(first_t - float(times[first])) > 0.010 or abs(second_t - float(times[second])) > 0.010:
        raise ValueError("reference index/timestamp mismatch")


def _validate_original_candidate_coverage(
    original_pairs: set[tuple[int, int]],
    original_map: dict[tuple[tuple[int, int], str], dict[str, Any]],
) -> None:
    candidate_pairs = {pair for pair, _eye in original_map}
    missing = original_pairs - candidate_pairs
    extra = candidate_pairs - original_pairs
    if missing:
        raise ValueError(f"original shared pair missing original eye candidate: {sorted(missing)[:3]}")
    if extra:
        raise ValueError(f"original eye candidate absent from original shared rows: {sorted(extra)[:3]}")


def _refresh_original_rows(
    rows: list[dict[str, Any]],
    by_pair: dict[tuple[int, int], list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], int]:
    refreshed = []
    changed = 0
    for row in rows:
        pair = (int(row["first_index"]), int(row["second_index"]))
        candidates = by_pair.get(pair)
        if not candidates:
            raise ValueError(f"shared row pair missing from current candidates: {pair}")
        confidence = max(float(candidate["observation_confidence"]) for candidate in candidates)
        copied = deepcopy(row)
        if abs(float(copied["pnp_inlier_ratio"]) - confidence) > 1e-12:
            changed += 1
        copied["pnp_inlier_ratio"] = confidence
        copied["confidence_source"] = "current_candidate_winner"
        refreshed.append(copied)
    return refreshed, changed


def _appended_template(
    pair: tuple[int, int],
    candidates: list[dict[str, Any]],
    times: np.ndarray,
) -> dict[str, Any]:
    first, second = pair
    confidence = max(float(candidate["observation_confidence"]) for candidate in candidates)
    recovered_eyes = sorted(
        {str(candidate["eye"]) for candidate in candidates if candidate.get("_source") == "recovered"}
    )
    if not recovered_eyes:
        raise ValueError("new shared pair lacks recovered candidates")
    return {
        "accepted": True,
        "first_index": int(first),
        "second_index": int(second),
        "first_t_sec": float(times[first]),
        "second_t_sec": float(times[second]),
        "metric_displacement_camera_i_m": [0.0, 0.0, 0.0],
        "metric_displacement_frame": "body_i",
        "scale": 1.0,
        "pnp_inlier_ratio": confidence,
        "rotation_error_deg": 0.0,
        "recovery_appended": True,
        "recovered_eyes": recovered_eyes,
        "confidence_source": "recovered_candidate_winner",
    }


def _choose_candidate(
    current: dict[str, Any] | None,
    incoming: dict[str, Any],
) -> dict[str, Any]:
    if current is None:
        return incoming
    current_conf = float(current["observation_confidence"])
    incoming_conf = float(incoming["observation_confidence"])
    if incoming_conf > current_conf:
        return incoming
    if incoming_conf < current_conf:
        return current
    return min([current, incoming], key=_canonical_candidate_key)


def _by_pair(candidates: Sequence[dict[str, Any]]) -> dict[tuple[int, int], list[dict[str, Any]]]:
    by_pair: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for candidate in candidates:
        pair = (int(candidate["reference_first_index"]), int(candidate["reference_second_index"]))
        by_pair.setdefault(pair, []).append(candidate)
    return by_pair


def _candidate_key(candidate: dict[str, Any]) -> tuple[tuple[int, int], str]:
    return (
        (int(candidate["reference_first_index"]), int(candidate["reference_second_index"])),
        str(candidate["eye"]),
    )


def _candidate_sort_key(candidate: dict[str, Any]) -> tuple[int, int, str, str]:
    pair, eye = _candidate_key(candidate)
    return (pair[0], pair[1], eye, _canonical_candidate_key(candidate))


def _canonical_candidate_key(candidate: dict[str, Any]) -> str:
    return json.dumps(_public_candidate(candidate), sort_keys=True, separators=(",", ":"))


def _public_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {key: deepcopy(value) for key, value in candidate.items() if not key.startswith("_")}


def _raw_index(item: dict[str, Any], key: str) -> int:
    value = item.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{key} must be an integer index")
    index = int(value)
    if index < 0:
        raise ValueError(f"{key} must be a non-negative integer index")
    return index


def _finite_float(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite") from exc
    if not np.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _confidence(value: Any, label: str) -> float:
    number = _finite_float(value, label)
    if number < 0.0 or number > 1.0:
        raise ValueError(f"{label} must be finite in [0, 1]")
    return number


def _vec3(value: Any, label: str) -> np.ndarray:
    vector = np.asarray(value, dtype=float)
    if vector.shape != (3,) or not np.all(np.isfinite(vector)):
        raise ValueError(f"{label} must be a finite 3-vector")
    return vector
