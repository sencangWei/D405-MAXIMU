"""Synchronize learned-factor source metadata without changing targets.

Pure development helper.  It copies only source confidence/residual fields from
rebuilt factors onto an existing factor list keyed by exact eye/reference pair.
It does not compare, rebuild, or alter learned targets.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Sequence

import numpy as np


SYNC_SOURCE_FIELDS = (
    "own_stereo_residual_m",
    "own_observation_confidence",
    "own_confidence",
    "confidence",
)


def sync_existing_factor_source_metadata(
    original_factors: Sequence[dict[str, Any]],
    rebuilt_source_factors: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Copy source metadata from rebuilt factors onto existing factors.

    The learned targets and all non-source fields remain exactly as they are in
    ``original_factors``.  Both inputs must contain the same unique
    ``(eye, first_index, second_index)`` keyset.
    """

    originals = deepcopy(list(original_factors))
    rebuilt = deepcopy(list(rebuilt_source_factors))
    original_by_key = _factor_map(originals, "original")
    rebuilt_by_key = _factor_map(rebuilt, "rebuilt")
    if set(original_by_key) != set(rebuilt_by_key):
        missing = sorted(set(original_by_key) - set(rebuilt_by_key))
        extra = sorted(set(rebuilt_by_key) - set(original_by_key))
        raise ValueError(f"factor keyset mismatch: missing={missing[:3]} extra={extra[:3]}")

    synced = []
    changed_factors = 0
    updated_fields = 0
    for original in originals:
        key = _factor_key(original)
        rebuilt_factor = rebuilt_by_key[key]
        copied = deepcopy(original)
        factor_changed = False
        for field in SYNC_SOURCE_FIELDS:
            new_value = float(rebuilt_factor[field])
            if float(copied[field]) != new_value:
                factor_changed = True
                updated_fields += 1
            copied[field] = new_value
        changed_factors += int(factor_changed)
        synced.append(copied)

    diagnostic = {
        "schema": "learned_source_consistency_sync_v1",
        "status": "EXPERIMENTAL_NOT_ACCEPTED",
        "accepted": False,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "factor_count": len(synced),
        "changed_factor_count": changed_factors,
        "updated_field_count": updated_fields,
        "synced_fields": list(SYNC_SOURCE_FIELDS),
    }
    return synced, diagnostic


def _factor_map(
    factors: list[dict[str, Any]],
    label: str,
) -> dict[tuple[str, int, int], dict[str, Any]]:
    mapped: dict[tuple[str, int, int], dict[str, Any]] = {}
    for factor in factors:
        key = _factor_key(factor)
        if key in mapped:
            raise ValueError(f"duplicate {label} learned factor key: {key}")
        _validate_source_fields(factor)
        mapped[key] = factor
    return mapped


def _factor_key(factor: dict[str, Any]) -> tuple[str, int, int]:
    eye = factor.get("eye")
    if eye not in ("left", "right"):
        raise ValueError("factor eye must be 'left' or 'right'")
    first = _index(factor, "first_index")
    second = _index(factor, "second_index")
    if second <= first:
        raise ValueError("factor indices must be ordered")
    return str(eye), first, second


def _index(factor: dict[str, Any], field: str) -> int:
    value = factor.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{field} must be an integer index")
    index = int(value)
    if index < 0:
        raise ValueError(f"{field} must be non-negative")
    return index


def _validate_source_fields(factor: dict[str, Any]) -> None:
    residual = _finite_float(factor, "own_stereo_residual_m")
    if residual < 0.0:
        raise ValueError("own_stereo_residual_m must be finite and non-negative")
    for field in ("own_observation_confidence", "own_confidence", "confidence"):
        value = _finite_float(factor, field)
        if value < 0.0 or value > 1.0:
            raise ValueError(f"{field} must be finite in [0, 1]")


def _finite_float(factor: dict[str, Any], field: str) -> float:
    try:
        value = float(factor[field])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite") from exc
    if not np.isfinite(value):
        raise ValueError(f"{field} must be finite")
    return value
