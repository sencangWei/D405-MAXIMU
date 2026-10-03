"""Bridge timeline-gap native stereo probe rows into shared-stereo candidates.

Pure development helper.  It consumes the source-only
``umi_independent_ir_timeline_gap_probe_v1`` report and prepares deduplicated
eye candidates plus body-frame shared-stereo rows using the existing recovered
pair and physical lever helpers.  It does not read GT, run a backend, score, or
mutate source artifacts.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path
import sys
from typing import Any, Sequence

import numpy as np

from ego_vio.vio.dual_ir_factors import _as_body_t_camera, _as_rotations, _as_strict_times
from ego_vio.vio.physical_stereo_lever import transform_shared_stereo_body_lever
from ego_vio.vio.recovered_stereo_pairs import build_recovered_shared_rows


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from run_physical_stereo_lever_probe import reference_bound_eye_candidate  # noqa: E402
import fuse_mast3r_stereo_imu as fusion  # noqa: E402


SCHEMA = "umi_independent_ir_timeline_gap_probe_v1"
READY_STATUS = "TIMELINE_GAP_SOURCE_PROBE_READY"
COMPLETE_STATUS = "TIMELINE_GAP_SOURCE_PROBE_COMPLETE"
TIMESTAMP_TOLERANCE_S = 0.010


def build_timeline_gap_shared_rows(
    reference_times: Sequence[float],
    reference_body_rotations: Sequence[Any],
    full_d405_times: Sequence[float],
    original_shared_rows: Sequence[dict[str, Any]],
    original_eye_candidates: Sequence[dict[str, Any]],
    timeline_gap_report: dict[str, Any],
    *,
    reference_scale_by_eye: dict[str, float] | None = None,
    support_policy: str = "all",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Append valid timeline-gap candidates and transform all rows to body_i.

    ``full_d405_times`` is the full D405 timeline used by the source probe, not
    a truncated MASt3R trajectory.  Accepted native rows still pass the existing
    reference-nearest <=10ms binding before they can become candidates.
    """

    ref_times = _as_strict_times("reference_times", reference_times)
    body_rotations = _as_rotations("reference_body_rotations", reference_body_rotations, ref_times.size)
    d405_times = _as_strict_times("full_d405_times", full_d405_times)
    report = _validate_report(timeline_gap_report)
    body_t_by_eye = _body_transforms(report)
    pair_policy = report["pair_policy"]
    scale_by_eye = reference_scale_by_eye or {}

    recovered_candidates: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    closure_metadata: list[dict[str, Any]] = []
    for row in report.get("observations", []):
        _validate_gap_row(row, d405_times, pair_policy)
        closure_metadata.append(_closure_row(row))
        for eye in ("left", "right"):
            candidate, reason = _candidate_from_gap_row(
                row,
                eye=eye,
                reference_times=ref_times,
                full_d405_times=d405_times,
                body_t_camera=body_t_by_eye[eye],
                reference_scale=scale_by_eye.get(eye),
            )
            if candidate is None:
                skipped[f"{eye}:{reason}"] += 1
            else:
                recovered_candidates.append(candidate)

    retained_recovered_candidates, support_diag = _apply_support_policy(recovered_candidates, support_policy)
    templates, deduped_candidates, recovered_diag = build_recovered_shared_rows(
        ref_times,
        original_shared_rows,
        original_eye_candidates,
        retained_recovered_candidates,
    )
    transformed_rows, lever_diag = transform_shared_stereo_body_lever(
        ref_times,
        body_rotations,
        deduped_candidates,
        templates,
    )
    appended_pairs = {
        tuple(pair)
        for pair in recovered_diag.get("appended_pairs", [])
    }
    diagnostic = {
        "schema": "timeline_gap_stereo_candidates_diagnostic_v1",
        "status": "EXPERIMENTAL_NOT_ACCEPTED",
        "accepted": False,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "backend_launched": False,
        "scorer_launched": False,
        "tracker_reference_used": False,
        "production_promoted": False,
        "gpu_model_used": False,
        "source_report_id": report.get("id"),
        "input_gap_observation_count": len(report.get("observations", [])),
        "recovered_eye_candidate_count": len(recovered_candidates),
        "retained_recovered_eye_candidate_count": len(retained_recovered_candidates),
        "output_candidate_count": len(deduped_candidates),
        "output_row_count": len(transformed_rows),
        "appended_pair_count": len(appended_pairs),
        "skipped_eye_candidate_counts": dict(sorted(skipped.items())),
        "support_sampling": support_diag,
        "recovered_pairs": recovered_diag,
        "physical_lever": lever_diag,
        "timeline_gap_closure_metadata": closure_metadata,
        "confidence_policy": "fuse_mast3r_stereo_imu.stereo_observation_confidence_formula",
        "reference_scale_policy": (
            "explicit_reference_scale_by_eye"
            if reference_scale_by_eye is not None
            else "native_observation_scale_used_as_neutral_reference_for_source_probe"
        ),
    }
    return transformed_rows, deduped_candidates, diagnostic


def _apply_support_policy(candidates: list[dict[str, Any]], policy: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if policy not in {"all", "nonoverlap"}:
        raise ValueError(f"unsupported timeline gap support_policy: {policy}")
    groups = _candidate_groups_by_pair(candidates)
    if policy == "all":
        return candidates, {
            "support_policy": "all",
            "support_policy_note": "development_support_sampling_only_not_statistical_independence",
            "support_input_pair_count": len(groups),
            "support_retained_pair_count": len(groups),
            "support_redundant_pair_count": 0,
            "support_retained_pairs": [_group_diag(group) for group in groups],
            "support_redundant_pairs": [],
        }

    retained: list[dict[str, Any]] = []
    redundant: list[dict[str, Any]] = []
    retained_intervals: list[tuple[int, int, tuple[int, int]]] = []
    ranked = sorted(
        groups,
        key=lambda group: (-group["score"], group["reference_pair"][0], group["reference_pair"][1]),
    )
    for group in ranked:
        first, second = group["support_interval"]
        overlap = next(
            (
                {"reference_pair": [pair[0], pair[1]], "support_interval": [kept_first, kept_second]}
                for kept_first, kept_second, pair in retained_intervals
                if not (second < kept_first or first > kept_second)
            ),
            None,
        )
        if overlap is not None:
            item = _group_diag(group)
            item["overlaps_retained_pair"] = overlap
            redundant.append(item)
            continue
        retained_intervals.append((first, second, group["reference_pair"]))
        retained.append(group)

    retained_pairs = {group["reference_pair"] for group in retained}
    retained_candidates = [
        candidate
        for group in groups
        if group["reference_pair"] in retained_pairs
        for candidate in group["candidates"]
    ]
    return retained_candidates, {
        "support_policy": "nonoverlap",
        "support_policy_note": "development_support_sampling_only_not_statistical_independence",
        "support_input_pair_count": len(groups),
        "support_retained_pair_count": len(retained),
        "support_redundant_pair_count": len(redundant),
        "support_retained_pairs": [_group_diag(group) for group in retained],
        "support_redundant_pairs": redundant,
    }


def _candidate_groups_by_pair(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for candidate in candidates:
        pair = (
            _candidate_index(candidate, "reference_first_index"),
            _candidate_index(candidate, "reference_second_index"),
        )
        grouped.setdefault(pair, []).append(candidate)
    groups: list[dict[str, Any]] = []
    for pair, items in grouped.items():
        raw_first = min(_candidate_index(candidate, "first_index") for candidate in items)
        raw_second = max(_candidate_index(candidate, "second_index") for candidate in items)
        if raw_second <= raw_first:
            raise ValueError("timeline gap support interval endpoints must be ordered")
        confidences = [float(candidate["observation_confidence"]) for candidate in items]
        if not all(np.isfinite(confidence) for confidence in confidences):
            raise ValueError("timeline gap support confidence must be finite")
        groups.append(
            {
                "reference_pair": pair,
                "support_interval": (raw_first, raw_second),
                "score": max(confidences),
                "candidate_count": len(items),
                "eyes": tuple(sorted(str(candidate.get("eye")) for candidate in items)),
                "candidates": items,
            }
        )
    return groups


def _group_diag(group: dict[str, Any]) -> dict[str, Any]:
    return {
        "reference_pair": [group["reference_pair"][0], group["reference_pair"][1]],
        "support_interval": [group["support_interval"][0], group["support_interval"][1]],
        "max_observation_confidence": group["score"],
        "candidate_count": group["candidate_count"],
        "eyes": list(group["eyes"]),
    }


def _candidate_index(candidate: dict[str, Any], key: str) -> int:
    value = candidate.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"timeline gap candidate {key} must be an integer")
    return int(value)


def _validate_report(report: dict[str, Any]) -> dict[str, Any]:
    if report.get("schema") != SCHEMA:
        raise ValueError("timeline gap report schema mismatch")
    if report.get("status") != READY_STATUS:
        raise ValueError("timeline gap record is not ready")
    for key in (
        "external_ground_truth_used",
        "slam_supervision",
        "backend_launched",
        "scorer_launched",
        "tracker_reference_used",
        "production_promoted",
        "gpu_model_used",
    ):
        if report.get(key) is not False:
            raise ValueError(f"timeline gap report {key} must be false")
    lineage = report.get("image_source_lineage")
    if not isinstance(lineage, dict) or lineage.get("selected_frames_loaded_from_db3_directly") is not True:
        raise ValueError("timeline gap report must load selected frames directly from DB3")
    if report.get("not_rejected_row_recovery") is not True:
        raise ValueError("timeline gap report must not masquerade as rejected-row recovery")
    policy = report.get("pair_policy")
    if not isinstance(policy, dict) or policy.get("selection") != "uniform_fixed_span_over_full_d405_timeline_tail":
        raise ValueError("timeline gap pair policy mismatch")
    if int(policy.get("span_frames", -1)) <= 0:
        raise ValueError("timeline gap span_frames must be positive")
    end_t = float(policy.get("both_raw_frontend_end_t_sec"))
    if not np.isfinite(end_t):
        raise ValueError("timeline gap raw frontend end is not finite")
    return report


def _body_transforms(report: dict[str, Any]) -> dict[str, list[list[float]]]:
    conversion = report.get("camera_pose_conversion")
    if not isinstance(conversion, dict):
        raise ValueError("timeline gap report missing camera_pose_conversion")
    return {
        "left": _body_t(conversion.get("body_T_left_ir"), "body_T_left_ir"),
        "right": _body_t(conversion.get("body_T_right_ir"), "body_T_right_ir"),
    }


def _body_t(value: Any, label: str) -> list[list[float]]:
    rotation, translation = _as_body_t_camera(label, value)
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-9) or np.linalg.det(rotation) <= 0.0:
        raise ValueError(f"{label} rotation must be proper")
    if not np.all(np.isfinite(translation)):
        raise ValueError(f"{label} translation must be finite")
    return np.asarray(value, dtype=float).tolist()


def _validate_gap_row(row: dict[str, Any], full_d405_times: np.ndarray, pair_policy: dict[str, Any]) -> None:
    first = _index(row, "first_index", full_d405_times.size)
    second = _index(row, "second_index", full_d405_times.size)
    if second <= first:
        raise ValueError("timeline gap row endpoints must be ordered")
    span = int(pair_policy["span_frames"])
    if second - first != span:
        raise ValueError("timeline gap row does not match fixed span_frames")
    first_t = _finite(row.get("first_t_sec"), "first_t_sec")
    second_t = _finite(row.get("second_t_sec"), "second_t_sec")
    if abs(first_t - float(full_d405_times[first])) > TIMESTAMP_TOLERANCE_S:
        raise ValueError("timeline gap first index/time mismatch")
    if abs(second_t - float(full_d405_times[second])) > TIMESTAMP_TOLERANCE_S:
        raise ValueError("timeline gap second index/time mismatch")
    if first_t <= float(pair_policy["both_raw_frontend_end_t_sec"]) or second_t <= float(pair_policy["both_raw_frontend_end_t_sec"]):
        raise ValueError("timeline gap row does not extend beyond both raw frontend ends")


def _candidate_from_gap_row(
    row: dict[str, Any],
    *,
    eye: str,
    reference_times: np.ndarray,
    full_d405_times: np.ndarray,
    body_t_camera: list[list[float]],
    reference_scale: float | None,
) -> tuple[dict[str, Any] | None, str | None]:
    combined = row.get(f"native_combined_{eye}")
    forward = row.get(f"raw_forward_{eye}")
    reverse = row.get(f"raw_reverse_{eye}")
    if not isinstance(combined, dict) or not isinstance(forward, dict) or not isinstance(reverse, dict):
        raise ValueError(f"timeline gap {eye} native result missing")
    if forward.get("accepted") is not True:
        return None, f"forward_{forward.get('reason', 'not_accepted')}"
    if reverse.get("accepted") is not True:
        return None, f"reverse_{reverse.get('reason', 'not_accepted')}"
    if combined.get("accepted") is not True:
        return None, f"combined_{combined.get('reason', 'not_accepted')}"
    observation = deepcopy(combined)
    observation.update(
        {
            "first_index": row["first_index"],
            "second_index": row["second_index"],
            "first_t_sec": row["first_t_sec"],
            "second_t_sec": row["second_t_sec"],
        }
    )
    expected_frame = f"infrared_{eye}_camera_i"
    if observation.get("metric_displacement_frame") != expected_frame:
        raise ValueError(f"{eye} timeline gap metric frame mismatch")
    confidence = fusion.stereo_observation_confidence(
        observation,
        float(reference_scale) if reference_scale is not None else float(observation["scale"]),
    )
    candidate, reason = reference_bound_eye_candidate(
        reference_times,
        full_d405_times,
        eye,
        observation,
        confidence,
        body_t_camera,
    )
    if candidate is not None:
        candidate["timeline_gap_source"] = True
        candidate["timeline_gap_native_bidirectional"] = True
        candidate["source_observation_index"] = row.get("source_observation_index")
        candidate["source_pair_closure"] = deepcopy(row.get("factory_frame_closure"))
    return candidate, reason


def _closure_row(row: dict[str, Any]) -> dict[str, Any]:
    closure = row.get("factory_frame_closure")
    return {
        "first_index": int(row["first_index"]),
        "second_index": int(row["second_index"]),
        "native_bidirectional_cross_class": row.get("native_bidirectional_cross_class"),
        "factory_frame_closure": deepcopy(closure),
    }


def _index(item: dict[str, Any], key: str, size: int) -> int:
    value = item.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{key} must be an integer index")
    index = int(value)
    if index < 0 or index >= size:
        raise ValueError(f"{key} out of full D405 timeline range")
    return index


def _finite(value: Any, label: str) -> float:
    number = float(value)
    if not np.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number
