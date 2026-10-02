#!/usr/bin/env python3
"""Evaluate native bidirectional SE3 geometry against its scalar baseline.

Development-only adapter.  It reuses the current best dual/constant-gauge
backend runner, but swaps only the physical stereo source rows built from a
separately proven bidirectional native-source appendix.  The two arms share the
same pair layout and frozen learned factors; candidate rows whose native SE3
geometry is invalid are retained only by the already-frozen fallback geometry.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import time
from copy import deepcopy
from pathlib import Path
import sys
from typing import Any
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import align_mast3r_scale_with_stereo as stereo_scale  # noqa: E402
import evaluate_sift_lm_dual_combined_probe as paired  # noqa: E402
import prepare_bidirectional_se3_source_probe as bidirectional_source  # noqa: E402


SOURCE_SCHEMA = "umi_bidirectional_se3_source_preflight_v1"
APPENDIX_SCHEMA = "umi_bidirectional_native_geometry_appendix_v1"
SCHEMA = "umi_bidirectional_se3_paired_probe_v1"
SCALAR_VARIANT = "currentbest_bidirectional_scalar_baseline"
SE3_VARIANT = "currentbest_bidirectional_se3_geometry"
RECOVERY_REFERENCE_VARIANT = "currentbest_independent_ir_recovered_pairs"
SOURCE_RECORD_STATUS = "INDEPENDENT_IR_RECOVERY_APPENDIX_READY"
ORIGINAL_PAIRED_FROZEN_CODE_PATHS = paired.frozen_code_paths


def _path_hash(path: Path) -> str:
    return paired.file_hash(Path(path))


def load_source_stage(path: Path) -> dict[str, Any]:
    report_path = path / "preflight_report.json"
    report = paired.read_json(report_path)
    if report.get("schema") != SOURCE_SCHEMA:
        raise ValueError("bidirectional SE3 source stage schema mismatch")
    if report.get("status") != "PREFLIGHT_COMPLETE":
        raise ValueError("bidirectional SE3 source stage is not complete")
    if report.get("external_ground_truth_used") is not False or report.get("slam_supervision") is not False:
        raise ValueError("bidirectional SE3 source stage used external supervision")
    rows = report.get("refined_sources", report.get("records"))
    if not isinstance(rows, list):
        raise ValueError("bidirectional SE3 source stage rows missing")
    if report.get("ready_record_count") != len(rows):
        raise ValueError("bidirectional SE3 source stage ready count mismatch")
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        record_id = row.get("id")
        if not record_id or record_id in by_id:
            raise ValueError("bidirectional SE3 source stage has missing/duplicate id")
        if row.get("status") != SOURCE_RECORD_STATUS:
            raise ValueError(f"bidirectional SE3 source row is not ready: {record_id}")
        _validate_stage_overrides(row)
        appendix_path = Path(row.get("recovery_appendix_path", ""))
        if not appendix_path.is_file() or _path_hash(appendix_path) != row.get("recovery_appendix_sha256"):
            raise ValueError(f"bidirectional SE3 appendix hash mismatch: {record_id}")
        by_id[str(record_id)] = row
    return {"path": str(report_path.resolve()), "sha256": _path_hash(report_path), "raw": report, "refined_by_id": by_id}


def validate_source_stage_record(record_id: str, source_stage: dict[str, Any]) -> dict[str, Any]:
    row = source_stage["refined_by_id"].get(record_id)
    if row is None:
        raise ValueError(f"bidirectional SE3 source stage missing record: {record_id}")
    return row


def _validate_stage_overrides(row: dict[str, Any]) -> None:
    overrides = row.get("source_override_sha256")
    if not isinstance(overrides, dict) or not overrides:
        raise ValueError("bidirectional SE3 source row missing source_override_sha256")
    for key in ("refined_left_sources", "refined_right_sources"):
        paths = row.get(key)
        if not isinstance(paths, list) or not paths:
            raise ValueError(f"bidirectional SE3 source row missing {key}")
        for source in paths:
            resolved = str(Path(source).resolve())
            if overrides.get(resolved) != _path_hash(Path(resolved)):
                raise ValueError(f"bidirectional SE3 override hash changed: {resolved}")


def _validate_onboard(value: dict[str, Any], label: str) -> None:
    paired.base.require_onboard_report(value, label)
    if value.get("external_ground_truth_used") is not False or value.get("slam_supervision") is not False:
        raise ValueError(f"{label} used external supervision")


def _validate_vector_quaternion(row: dict[str, Any], eye: str) -> None:
    vector = np.asarray(row.get("metric_displacement_camera_i_m"), dtype=float)
    quat = np.asarray(row.get("pnp_rotation_quaternion_xyzw"), dtype=float)
    if (
        row.get("metric_displacement_frame") != f"infrared_{eye}_camera_i"
        or vector.shape != (3,)
        or quat.shape != (4,)
        or not np.all(np.isfinite(vector))
        or not np.all(np.isfinite(quat))
        or not np.isclose(np.linalg.norm(quat), 1.0, atol=1e-6)
    ):
        raise ValueError("bidirectional native measurement geometry invalid")


def _bind_native_row(native: dict[str, Any], original: dict[str, Any]) -> None:
    for key in ("first_index", "second_index", "first_t_sec", "second_t_sec"):
        if native.get(key) != original.get(key):
            raise ValueError(f"bidirectional native row changed source binding: {key}")


def _candidate_from_native(
    *,
    reference_times: np.ndarray,
    metric_times: np.ndarray,
    eye: str,
    native: dict[str, Any],
    reference_scale: float,
    body_t_camera: Any,
    source_path: str,
    source_index: int,
    source_kind: str,
) -> dict[str, Any]:
    candidate, reason = paired.physical.reference_bound_eye_candidate(
        reference_times,
        metric_times,
        eye,
        native,
        paired.fusion.stereo_observation_confidence(native, reference_scale),
        body_t_camera,
    )
    if reason is not None:
        return {"_skip_reason": reason}
    candidate["bidirectional_source_report_path"] = source_path
    candidate["bidirectional_source_observation_index"] = int(source_index)
    candidate["bidirectional_geometry_source"] = source_kind
    return candidate


def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, float) or isinstance(right, float):
        try:
            return bool(np.isclose(float(left), float(right), rtol=0.0, atol=1e-12))
        except (TypeError, ValueError):
            return False
    if isinstance(left, list) or isinstance(right, list):
        try:
            left_array = np.asarray(left, dtype=float)
            right_array = np.asarray(right, dtype=float)
        except (TypeError, ValueError):
            return left == right
        return left_array.shape == right_array.shape and bool(np.allclose(left_array, right_array, rtol=0.0, atol=1e-12))
    return left == right


def validate_native_geometry_provenance(native: dict[str, Any], forward: dict[str, Any], reverse: dict[str, Any]) -> None:
    recomputed_native = bidirectional_source.combine_native_geometry(
        forward,
        reverse,
        stereo_scale.combine_bidirectional_scale,
    )
    endpoint_keys = {"first_index", "second_index", "first_t_sec", "second_t_sec"}
    for key in sorted((set(native) | set(recomputed_native)) - endpoint_keys):
        if not _json_equal(native.get(key), recomputed_native.get(key)):
            raise ValueError(f"saved bidirectional native geometry mismatch: {key}")
    if native.get("accepted") is not True:
        return
    saved = native.get("scalar_bidirectional_baseline")
    if not isinstance(saved, dict) or saved.get("accepted") is not True:
        raise ValueError("bidirectional SE3 accepted row lacks scalar accepted baseline")
    recomputed = stereo_scale.combine_bidirectional_scale(forward, reverse)
    if recomputed.get("accepted") is not True:
        raise ValueError("recomputed scalar bidirectional baseline is not accepted")
    for key in sorted((set(saved) | set(recomputed)) - endpoint_keys):
        if not _json_equal(saved.get(key), recomputed.get(key)):
            raise ValueError(f"saved scalar bidirectional baseline mismatch: {key}")


def validate_bidirectional_appendix(
    record: dict[str, Any],
    stage_record: dict[str, Any],
    baseline_candidate: dict[str, Any],
    reference_times: np.ndarray,
    eye_reports: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], list[Path]]:
    """Return matched scalar-baseline and SE3 candidates from one appendix."""
    path = Path(stage_record["recovery_appendix_path"])
    if _path_hash(path) != stage_record["recovery_appendix_sha256"]:
        raise ValueError("bidirectional SE3 appendix hash mismatch")
    appendix = paired.read_json(path)
    _validate_onboard(appendix, "bidirectional SE3 appendix")
    if appendix.get("schema") != APPENDIX_SCHEMA or appendix.get("id") != record["id"]:
        raise ValueError("bidirectional SE3 appendix schema/record mismatch")
    if Path(appendix.get("session", "")).resolve() != Path(record["session"]).resolve():
        raise ValueError("bidirectional SE3 appendix session mismatch")

    guard = appendix.get("consumed_source_guard", {})
    guarded = guard.get("guarded_before_sha256")
    if guard.get("guarded_after_verified") is not True or not isinstance(guarded, dict) or not guarded:
        raise ValueError("bidirectional SE3 consumed source guard missing")
    consumed = [path]
    for binding in guarded.values():
        source_path = Path(binding["path"])
        if _path_hash(source_path) != binding["sha256"]:
            raise ValueError("bidirectional SE3 consumed source guard hash changed")
        consumed.append(source_path)

    contexts = appendix.get("eye_contexts")
    if set(contexts or {}) != {"left", "right"}:
        raise ValueError("bidirectional SE3 appendix must bind both eyes")
    source_reports: dict[tuple[str, str], dict[str, Any]] = {}
    trajectory_times: dict[str, np.ndarray] = {}
    metric_times: dict[str, np.ndarray] = {}
    metadata: dict[str, dict[str, Any]] = {}
    for eye in ("left", "right"):
        context = contexts[eye]
        merged = eye_reports[eye]
        scale = float(context["reference_scale"])
        if not np.isfinite(scale) or scale <= 0 or scale != float(merged["scale_m_per_mast3r_unit"]):
            raise ValueError("bidirectional SE3 reference scale mismatch")
        admitted = {str(Path(p).resolve()) for p in merged["merged_report_paths"]}
        declared = {str(Path(p).resolve()) for p in context["merged_report_paths"]}
        if declared != admitted:
            raise ValueError("bidirectional SE3 report admission mismatch")
        trajectory = Path(context["reference_trajectory_path"]).resolve()
        if _path_hash(trajectory) != context["reference_trajectory_sha256"]:
            raise ValueError("bidirectional SE3 raw trajectory hash mismatch")
        trajectory_times[eye] = paired.fusion.load_trajectory(trajectory)[0]
        metric_trajectory = paired.physical.eye_trajectory_path_from_baseline(baseline_candidate, eye)
        paired.source_eval.validate_candidate_hashes(baseline_candidate, [metric_trajectory])
        metric_times[eye] = paired.fusion.load_trajectory(metric_trajectory)[0]
        if not np.array_equal(trajectory_times[eye], metric_times[eye]):
            raise ValueError("bidirectional SE3 raw/metric timeline mismatch")
        metadata[eye] = paired.physical.validate_eye_metadata(baseline_candidate, eye)
        consumed.extend([trajectory, metric_trajectory])
        for source, digest in context["report_sha256"].items():
            source_path = Path(source).resolve()
            if _path_hash(source_path) != digest:
                raise ValueError("bidirectional SE3 source report hash mismatch")
            report = paired.read_json(source_path)
            if Path(report.get("trajectory", "")).resolve() != trajectory:
                raise ValueError("bidirectional SE3 source report trajectory mismatch")
            source_reports[(eye, str(source_path))] = report
            consumed.append(source_path)
        for source_path in admitted:
            if source_reports[(eye, source_path)].get("result") != "PASS":
                raise ValueError("bidirectional SE3 admitted source report must remain PASS")

    scalar_candidates: list[dict[str, Any]] = []
    se3_candidates: list[dict[str, Any]] = []
    native_rejected = 0
    skipped: dict[str, int] = {}
    seen: set[tuple[str, str, int]] = set()
    for entry in appendix.get("observations", []):
        eye = entry.get("eye")
        if eye not in ("left", "right"):
            raise ValueError("bidirectional SE3 entry eye invalid")
        source_path = str(Path(entry["source_report_path"]).resolve())
        context = contexts[eye]
        if source_path not in {str(Path(p).resolve()) for p in context["merged_report_paths"]}:
            raise ValueError("bidirectional SE3 entry from non-admitted source report")
        if entry.get("source_report_sha256") != context["report_sha256"].get(source_path):
            raise ValueError("bidirectional SE3 entry source hash mismatch")
        index = entry["source_observation_index"]
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError("bidirectional SE3 source row index invalid")
        identity = (eye, source_path, index)
        if identity in seen:
            raise ValueError("duplicate bidirectional SE3 source row")
        seen.add(identity)
        source = source_reports[(eye, source_path)]
        if not 0 <= index < len(source.get("observations", [])):
            raise ValueError("bidirectional SE3 source row index out of range")
        original = source["observations"][index]
        if original != entry.get("original_observation"):
            raise ValueError("bidirectional SE3 original row was not byte-identical")
        native = entry["native_observation"]
        _bind_native_row(native, original)
        paired.physical.validate_raw_observation_source(native, trajectory_times[eye])
        forward = entry.get("raw_forward_summary", {})
        reverse = entry.get("raw_reverse_summary", {})
        validate_native_geometry_provenance(native, forward, reverse)
        if native.get("accepted") is not True:
            native_rejected += 1
            continue
        if forward.get("accepted") is not True or reverse.get("accepted") is not True:
            raise ValueError("bidirectional SE3 accepted row lacks forward/reverse native support")
        scalar = native["scalar_bidirectional_baseline"]
        if native.get("bidirectional_se3_midpoint") is not True:
            raise ValueError("bidirectional SE3 accepted row lacks midpoint geometry")
        scalar_native = deepcopy(scalar)
        for key in ("first_index", "second_index", "first_t_sec", "second_t_sec"):
            scalar_native[key] = native[key]
        scalar_native.setdefault("metric_displacement_frame", f"infrared_{eye}_camera_i")
        _validate_vector_quaternion(scalar_native, eye)
        _validate_vector_quaternion(native, eye)
        kwargs = dict(
            reference_times=reference_times,
            metric_times=metric_times[eye],
            eye=eye,
            reference_scale=float(context["reference_scale"]),
            body_t_camera=metadata[eye]["effective_body_T_camera"],
            source_path=source_path,
            source_index=index,
        )
        scalar_candidate = _candidate_from_native(native=scalar_native, source_kind="scalar_bidirectional_baseline", **kwargs)
        se3_candidate = _candidate_from_native(native=native, source_kind="bidirectional_se3_midpoint", **kwargs)
        if scalar_candidate.get("_skip_reason") or se3_candidate.get("_skip_reason"):
            reason = str(scalar_candidate.get("_skip_reason") or se3_candidate.get("_skip_reason"))
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        scalar_candidates.append(scalar_candidate)
        se3_candidates.append(se3_candidate)

    return scalar_candidates, se3_candidates, {
        "schema": "bidirectional_se3_appendix_validation_v1",
        "appendix_schema": APPENDIX_SCHEMA,
        "appendix_path": str(path.resolve()),
        "appendix_sha256": _path_hash(path),
        "scalar_candidate_count": len(scalar_candidates),
        "se3_candidate_count": len(se3_candidates),
        "native_rejected_rows_retained_as_frozen_fallback": native_rejected,
        "skipped_reference_binding_candidates": skipped,
        "original_source_reports_unchanged": True,
        "pair_layout_policy": "matched scalar/SE3 candidates; invalid native rows fall back to frozen measurement",
        "external_ground_truth_used": False,
        "slam_supervision": False,
    }, list(dict.fromkeys(consumed))


def shared_row_keys(rows: list[dict[str, Any]]) -> list[tuple[int, int, float, float]]:
    return [
        (int(row["first_index"]), int(row["second_index"]), float(row["first_t_sec"]), float(row["second_t_sec"]))
        for row in rows
    ]


def assert_same_layout_and_confidence(
    scalar_rows: list[dict[str, Any]],
    se3_rows: list[dict[str, Any]],
) -> None:
    if shared_row_keys(scalar_rows) != shared_row_keys(se3_rows):
        raise ValueError("bidirectional scalar/SE3 arms changed shared pair layout")
    for scalar, se3 in zip(scalar_rows, se3_rows):
        scalar_confidence = float(scalar["pnp_inlier_ratio"])
        se3_confidence = float(se3["pnp_inlier_ratio"])
        if not np.isfinite(scalar_confidence) or not np.isfinite(se3_confidence):
            raise ValueError("bidirectional scalar/SE3 confidence is non-finite")
        if abs(scalar_confidence - se3_confidence) > 1e-12:
            raise ValueError("bidirectional scalar/SE3 arms changed shared confidence")


def _candidate_key(candidate: dict[str, Any]) -> tuple[tuple[int, int], str]:
    return (
        (int(candidate["reference_first_index"]), int(candidate["reference_second_index"])),
        str(candidate["eye"]),
    )


def _candidate_map(candidates: list[dict[str, Any]], *, label: str) -> dict[tuple[tuple[int, int], str], dict[str, Any]]:
    mapped = {}
    for candidate in candidates:
        key = _candidate_key(candidate)
        if key in mapped:
            raise ValueError(f"duplicate {label} same-eye candidate for pair {key}")
        mapped[key] = candidate
    return mapped


def _pair_set(rows: list[dict[str, Any]]) -> set[tuple[int, int]]:
    return {(int(row["first_index"]), int(row["second_index"])) for row in rows}


def refresh_rows_for_candidate_confidence(
    rows: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    fallback_pairs: set[tuple[int, int]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_pair: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for candidate in candidates:
        pair = (int(candidate["reference_first_index"]), int(candidate["reference_second_index"]))
        by_pair.setdefault(pair, []).append(candidate)
    refreshed = []
    changed = 0
    for row in rows:
        pair = (int(row["first_index"]), int(row["second_index"]))
        copied = deepcopy(row)
        if pair in fallback_pairs:
            refreshed.append(copied)
            continue
        pair_candidates = by_pair.get(pair)
        if not pair_candidates:
            raise ValueError(f"fixed shared row lacks selected candidate and fallback marker: {pair}")
        confidence = max(float(candidate["observation_confidence"]) for candidate in pair_candidates)
        if not np.isfinite(confidence) or confidence < 0.0 or confidence > 1.0:
            raise ValueError("selected candidate confidence must be finite in [0, 1]")
        if abs(float(copied["pnp_inlier_ratio"]) - confidence) > 1e-12:
            changed += 1
        copied["pnp_inlier_ratio"] = confidence
        copied["confidence_source"] = "bidirectional_native_candidate_winner"
        refreshed.append(copied)
    return refreshed, {
        "schema": "bidirectional_fixed_layout_confidence_refresh_v1",
        "row_count": len(rows),
        "confidence_changed_row_count": changed,
        "fallback_pair_count": len(fallback_pairs),
    }


def select_candidates_for_fixed_layout(
    reference_rows: list[dict[str, Any]],
    existing_candidates: list[dict[str, Any]],
    native_candidates: list[dict[str, Any]],
    *,
    label: str,
) -> tuple[list[dict[str, Any]], set[tuple[int, int]], dict[str, Any]]:
    fixed_pairs = _pair_set(reference_rows)
    existing = _candidate_map(existing_candidates, label="existing")
    native = _candidate_map(native_candidates, label=label)
    selected = dict(existing)
    replaced = 0
    skipped_new_pairs = 0
    for key, candidate in native.items():
        pair, _eye = key
        if pair not in fixed_pairs:
            skipped_new_pairs += 1
            continue
        selected[key] = candidate
        replaced += 1
    selected_for_layout = [
        deepcopy(candidate)
        for key, candidate in sorted(selected.items(), key=lambda item: (item[0][0][0], item[0][0][1], item[0][1]))
        if key[0] in fixed_pairs
    ]
    covered_pairs = {pair for pair, _eye in selected if pair in fixed_pairs}
    frozen_fallback_pairs = fixed_pairs - covered_pairs
    return selected_for_layout, frozen_fallback_pairs, {
        "schema": "bidirectional_fixed_layout_candidate_selection_v1",
        "arm": label,
        "fixed_row_count": len(reference_rows),
        "fixed_pair_count": len(fixed_pairs),
        "native_same_eye_replacement_count": replaced,
        "native_pairs_outside_fixed_layout_skipped": skipped_new_pairs,
        "frozen_fallback_pair_count": len(frozen_fallback_pairs),
        "frozen_fallback_pairs": [[first, second] for first, second in sorted(frozen_fallback_pairs)],
    }


def transform_with_frozen_fallback(
    state: Any,
    candidates: list[dict[str, Any]],
    reference_rows: list[dict[str, Any]],
    fallback_pairs: set[tuple[int, int]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not fallback_pairs:
        return paired.physical.transform_shared_rows(state, candidates, reference_rows)
    active_rows = [row for row in reference_rows if (int(row["first_index"]), int(row["second_index"])) not in fallback_pairs]
    active_pairs = _pair_set(active_rows)
    active_candidates = [
        candidate
        for candidate in candidates
        if (int(candidate["reference_first_index"]), int(candidate["reference_second_index"])) in active_pairs
    ]
    transformed_active, report = paired.physical.transform_shared_rows(state, active_candidates, active_rows)
    by_pair = {
        (int(row["first_index"]), int(row["second_index"])): row
        for row in transformed_active
    }
    output = []
    for row in reference_rows:
        pair = (int(row["first_index"]), int(row["second_index"]))
        output.append(deepcopy(row) if pair in fallback_pairs else deepcopy(by_pair[pair]))
    report["frozen_body_i_fallback_pairs"] = [[first, second] for first, second in sorted(fallback_pairs)]
    report["frozen_body_i_fallback_pair_count"] = len(fallback_pairs)
    return output, report


def build_matched_shared_rows(
    reference_times: np.ndarray,
    original_rows: list[dict[str, Any]],
    existing_candidates: list[dict[str, Any]],
    scalar_candidates: list[dict[str, Any]],
    se3_candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], set[tuple[int, int]], set[tuple[int, int]]]:
    scalar_all, scalar_fallback, scalar_diag = select_candidates_for_fixed_layout(
        original_rows, existing_candidates, scalar_candidates, label="scalar_bidirectional_baseline",
    )
    se3_all, se3_fallback, se3_diag = select_candidates_for_fixed_layout(
        original_rows, existing_candidates, se3_candidates, label="bidirectional_se3_midpoint",
    )
    if scalar_fallback != se3_fallback:
        raise ValueError("bidirectional scalar/SE3 arms changed fallback pair layout")
    scalar_rows, scalar_confidence = refresh_rows_for_candidate_confidence(
        original_rows, scalar_all, scalar_fallback,
    )
    se3_rows, se3_confidence = refresh_rows_for_candidate_confidence(
        original_rows, se3_all, se3_fallback,
    )
    assert_same_layout_and_confidence(scalar_rows, se3_rows)
    if len(scalar_all) != len(se3_all):
        raise ValueError("bidirectional scalar/SE3 arms changed candidate count")
    if {_candidate_key(candidate) for candidate in scalar_all} != {_candidate_key(candidate) for candidate in se3_all}:
        raise ValueError("bidirectional scalar/SE3 arms changed candidate source keys")
    scalar_diag = {**scalar_diag, "confidence_refresh": scalar_confidence, "matched_pair_layout_keys": shared_row_keys(scalar_rows)}
    se3_diag = {**se3_diag, "confidence_refresh": se3_confidence, "matched_pair_layout_keys": shared_row_keys(se3_rows)}
    return scalar_rows, scalar_all, scalar_diag, se3_rows, se3_all, se3_diag, scalar_fallback, se3_fallback


def bidirectional_source_upgrade_scope(source_override_sha256: dict[str, str], arm_role: str) -> dict[str, Any]:
    return {
        "bidirectional_native_geometry_probe": True,
        "arm_role": arm_role,
        "source_override_sha256_count": len(source_override_sha256),
        "learned_factor_targets_identical_between_arms": True,
        "pair_layout_identical_between_scalar_and_se3": True,
        "native_rejected_rows_use_frozen_fallback": True,
        "external_ground_truth_used": False,
        "slam_supervision": False,
    }


def frozen_code_paths(source_stage: Path) -> list[Path]:
    return [
        *ORIGINAL_PAIRED_FROZEN_CODE_PATHS(source_stage),
        Path(__file__),
        ROOT / "scripts/prepare_bidirectional_se3_source_probe.py",
        ROOT / "scripts/align_mast3r_scale_with_stereo.py",
        ROOT / "ego_vio/vio/bidirectional_se3_motion.py",
    ]


def recovery_reference_artifact_dir(root: Path, record_id: str) -> Path:
    return root / record_id / RECOVERY_REFERENCE_VARIANT


def validate_recovery_reference(
    record: dict[str, Any],
    recovery_reference: Path,
    stage_record: dict[str, Any],
) -> tuple[Path, list[dict[str, Any]], dict[str, Any], list[Path]]:
    summary_path = recovery_reference / "summary.json"
    summary = paired.read_json(summary_path)
    if summary.get("schema") != "umi_independent_ir_recovery_paired_probe_v1":
        raise ValueError("recovery reference summary schema mismatch")
    if summary.get("status") != "COMPLETED":
        raise ValueError("recovery reference summary is not complete")
    _validate_onboard(summary, "recovery reference summary")
    artifact = recovery_reference_artifact_dir(recovery_reference, record["id"])
    paths = [
        artifact / "candidate_manifest.json",
        artifact / "graph_report.json",
        artifact / "shared_stereo_observations.json",
        artifact / "body_trajectory_fused.csv",
    ]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise ValueError(f"missing recovery reference artifact: {missing[:3]}")
    candidate = paired.read_json(paths[0])
    graph = paired.read_json(paths[1])
    _validate_onboard(candidate, "recovery reference candidate")
    _validate_onboard(graph, "recovery reference graph")
    if Path(candidate.get("session", "")).resolve() != Path(record["session"]).resolve():
        raise ValueError("recovery reference session mismatch")
    if graph.get("output_frame") != "body_imu_origin":
        raise ValueError("recovery reference graph output frame mismatch")
    if paired.file_hash(paths[3]) != candidate.get("output_estimate_sha256"):
        raise ValueError("recovery reference output estimate hash mismatch")
    source_stage = Path(summary.get("source_stage", ""))
    if not source_stage:
        raise ValueError("recovery reference source stage missing")
    source_stage_preflight = source_stage / "preflight_report.json"
    if paired.file_hash(source_stage_preflight) != summary.get("source_stage_preflight_sha256"):
        raise ValueError("recovery reference source preflight hash mismatch")
    if stage_record.get("id") != record["id"]:
        raise ValueError("recovery reference record mismatch")
    rows = paired.read_json(paths[2])
    return artifact, rows, {
        "schema": "bidirectional_recovery_reference_layout_v1",
        "artifact_dir": str(artifact.resolve()),
        "summary_sha256": paired.file_hash(summary_path),
        "candidate_manifest_sha256": paired.file_hash(paths[0]),
        "graph_report_sha256": paired.file_hash(paths[1]),
        "shared_stereo_observations_sha256": paired.file_hash(paths[2]),
        "body_trajectory_fused_sha256": paired.file_hash(paths[3]),
        "source_stage": str(source_stage.resolve()),
        "source_stage_preflight_sha256": summary.get("source_stage_preflight_sha256"),
        "fixed_shared_row_count": len(rows),
    }, [summary_path, source_stage_preflight, *paths]


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    output = {}
    for variant in (SCALAR_VARIANT, SE3_VARIANT):
        scores = [
            result.get("variants", {}).get(variant, {}).get("score")
            for result in results
            if result.get("variants", {}).get(variant, {}).get("score") is not None
        ]
        output[variant] = {
            "scored_count": len(scores),
            "precision_pass_count": sum(score.get("result") == "PASS" for score in scores),
            "max_within_10mm_count": sum(
                score.get("ate_translation_max_m", float("inf")) <= 0.010
                for score in scores
            ),
            "worst_max_m": max(
                (score.get("ate_translation_max_m") for score in scores),
                default=None,
            ),
        }
    return output


def run_record(
    record: dict[str, Any],
    baseline: Path,
    constant_root: Path,
    combined_reference: Path,
    recovery_reference: Path,
    output: Path,
    source_stage: dict[str, Any],
    frozen_hashes: dict[str, str],
) -> dict[str, Any]:
    record_id = record["id"]
    baseline_artifact = paired.baseline_artifact_dir(baseline, record_id)
    result = {"id": record_id, "status": "IN_PROGRESS", "variants": {}}
    try:
        stage_record = validate_source_stage_record(record_id, source_stage)
        paired.base.validate_record_sources(record)
        baseline_candidate, baseline_graph = paired.base.validate_baseline_artifact(record, baseline_artifact)
        state = paired.base.load_bound_reference(record)
        paired.base.validate_baseline_trajectory_identity(baseline_artifact, state, baseline_graph)
        recovery_artifact, fixed_stereo, recovery_proof, recovery_paths = validate_recovery_reference(
            record, recovery_reference, stage_record,
        )
        existing_candidates, eye_reports, existing_paths, overrides = paired.load_refined_all_eye_candidates(
            record, baseline_candidate, state.times, stage_record,
        )
        scalar_candidates, se3_candidates, appendix_proof, appendix_paths = validate_bidirectional_appendix(
            record, stage_record, baseline_candidate, state.times, eye_reports,
        )
        scalar_templates, scalar_all, scalar_diag, se3_templates, se3_all, se3_diag, scalar_fallback, se3_fallback = build_matched_shared_rows(
            state.times, fixed_stereo, existing_candidates, scalar_candidates, se3_candidates,
        )
        scalar_rows, scalar_report = transform_with_frozen_fallback(state, scalar_all, scalar_templates, scalar_fallback)
        se3_rows, se3_report = transform_with_frozen_fallback(state, se3_all, se3_templates, se3_fallback)
        for report, diag, role in (
            (scalar_report, scalar_diag, "scalar_bidirectional_baseline"),
            (se3_report, se3_diag, "bidirectional_se3_geometry"),
        ):
            report["eye_candidate_reports"] = eye_reports
            report["bidirectional_appendix_validation"] = appendix_proof
            report["bidirectional_layout_match"] = diag
            report["recovery_reference_layout"] = recovery_proof
            report["source_geometry_arm_role"] = role
        constant_artifact, _constant_candidate, _constant_graph, constant_paths = paired.physical.validate_constant_artifact(
            record, constant_root,
        )
        combined_artifact, combined_candidate, combined_paths = paired.validate_combined_reference(
            record, baseline_artifact, combined_reference,
        )
        consumed_before = paired.snapshot_paths([
            baseline_artifact / "candidate_manifest.json",
            baseline_artifact / "graph_report.json",
            baseline_artifact / "local_motion_factors.json",
            baseline_artifact / "body_trajectory_fused.csv",
            constant_artifact / "candidate_manifest.json",
            constant_artifact / "graph_report.json",
            constant_artifact / "local_motion_factors.json",
            constant_artifact / "body_trajectory_fused.csv",
            combined_artifact / "candidate_manifest.json",
            combined_artifact / "graph_report.json",
            combined_artifact / "shared_stereo_observations.json",
            combined_artifact / "local_motion_factors.json",
            combined_artifact / "body_trajectory_fused.csv",
            Path(source_stage["path"]),
            Path(record["session"]) / "d405_frames.csv",
            Path(record["session"]) / "external_imu/imu.bin",
            Path(record["vins_dir"]) / "vio_corrected_stream.csv",
            Path(record["vins_dir"]) / "run_acceptance.json",
            *existing_paths,
            *appendix_paths,
            *recovery_paths,
            *constant_paths,
            *combined_paths,
        ])
        result["reference_current_best"] = {
            "artifact_dir": str(combined_artifact.resolve()),
            "candidate_manifest_sha256": paired.file_hash(combined_artifact / "candidate_manifest.json"),
            "graph_report_sha256": paired.file_hash(combined_artifact / "graph_report.json"),
            "body_trajectory_fused_sha256": paired.file_hash(combined_artifact / "body_trajectory_fused.csv"),
            "role": "best_context_comparator_not_source_selector",
            "schema": combined_candidate.get("schema"),
            "recovery_reference_artifact_dir": str(recovery_artifact.resolve()),
        }
    except paired.base.StopCodeChanged:
        raise
    except Exception as error:
        for variant in (SCALAR_VARIANT, SE3_VARIANT):
            variant_dir = output / record_id / variant
            variant_dir.mkdir(parents=True, exist_ok=True)
            result["variants"][variant] = paired.physical.run_variant_failure(variant_dir, error)
        result["status"] = "INCOMPLETE_VARIANTS"
        return result

    arms = {
        SCALAR_VARIANT: (scalar_rows, scalar_report, "native_scalar_bidirectional_baseline"),
        SE3_VARIANT: (se3_rows, se3_report, "native_bidirectional_se3_geometry"),
    }
    for variant, (rows, report, role) in arms.items():
        variant_dir = output / record_id / variant
        variant_dir.mkdir(parents=True, exist_ok=True)
        try:
            result["variants"][variant] = paired.run_arm(
                record=record,
                baseline_artifact=baseline_artifact,
                variant=variant,
                variant_dir=variant_dir,
                state=state,
                baseline_candidate=baseline_candidate,
                stereo_rows=rows,
                stereo_report=report,
                raw_paths=[*existing_paths, *appendix_paths, *recovery_paths],
                constant_artifact=constant_artifact,
                constant_paths=[*constant_paths, *combined_paths],
                frozen_hashes=frozen_hashes,
                combined_reference_artifact=combined_artifact,
                source_override_sha256=overrides,
                arm_role=role,
                control_replay=None,
            )
        except paired.base.StopCodeChanged:
            raise
        except Exception as error:
            result["variants"][variant] = paired.physical.run_variant_failure(variant_dir, error)
    paired.assert_hashes_unchanged(consumed_before)
    result["status"] = (
        "COMPLETED"
        if all("score" in variant for variant in result["variants"].values())
        else "INCOMPLETE_VARIANTS"
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--constant-gauge", type=Path, required=True)
    parser.add_argument("--combined-reference", type=Path, required=True)
    parser.add_argument("--recovery-reference", type=Path, required=True)
    parser.add_argument("--source-stage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", action="append", default=[])
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink():
        parser.error("output must be new; previous results are never overwritten")

    with ExitStack() as stack:
        for owner, name, value in (
            (paired, "source_upgrade_scope", bidirectional_source_upgrade_scope),
            (paired, "frozen_code_paths", frozen_code_paths),
            (paired, "SCHEMA", SCHEMA),
        ):
            stack.enter_context(patch.object(owner, name, value))
        manifest = paired.read_json(args.manifest)
        records = paired.base.validate_records(manifest, args.dataset)
        baseline_summary = paired.read_json(args.baseline / "summary.json")
        baseline_by_id = paired.base.baseline_results(baseline_summary)
        missing = {record["id"] for record in records} - set(baseline_by_id)
        if missing:
            raise ValueError(f"baseline summary is missing records: {sorted(missing)}")
        source_stage = load_source_stage(args.source_stage)
        frozen_hashes = paired.base.snapshot_hashes(frozen_code_paths(args.source_stage))
        args.output.mkdir(parents=True)
        summary = {
            "schema": SCHEMA,
            "status": "RUNNING",
            "development_only": True,
            "blind_test": False,
            "production_promoted": False,
            "external_ground_truth_used": False,
            "slam_supervision": False,
            "dataset_count": len(records),
            "completed_count": 0,
            "manifest_sha256": paired.file_hash(args.manifest),
            "baseline_summary_sha256": paired.file_hash(args.baseline / "summary.json"),
            "source_stage_preflight_sha256": source_stage["sha256"],
            "baseline": str(args.baseline.resolve()),
            "constant_gauge": str(args.constant_gauge.resolve()),
            "combined_reference": str(args.combined_reference.resolve()),
            "recovery_reference": str(args.recovery_reference.resolve()),
            "source_stage": str(args.source_stage.resolve()),
            "variants": [SCALAR_VARIANT, SE3_VARIANT],
            "recovery_reference_variant": RECOVERY_REFERENCE_VARIANT,
            "code_sha256": frozen_hashes,
            "results": [],
        }
        paired.write_json(args.output / "summary.json", summary)
        for index, record in enumerate(records, 1):
            if paired.base.code_changed(frozen_hashes):
                summary["status"] = "STOP_CODE_CHANGED"
                paired.write_json(args.output / "summary.json", summary)
                return 2
            started = time.monotonic()
            baseline_row = baseline_by_id[record["id"]]
            if baseline_row.get("status") != "COMPLETED":
                result = {
                    "id": record["id"],
                    "status": baseline_row.get("status", "INCOMPLETE_VARIANTS"),
                    "source_baseline_status": baseline_row.get("status"),
                    "variants": {},
                }
            else:
                try:
                    result = run_record(
                        record,
                        args.baseline,
                        args.constant_gauge,
                        args.combined_reference,
                        args.recovery_reference,
                        args.output,
                        source_stage,
                        frozen_hashes,
                    )
                except paired.base.StopCodeChanged:
                    summary["status"] = "STOP_CODE_CHANGED"
                    paired.write_json(args.output / "summary.json", summary)
                    return 2
            result["elapsed_s"] = time.monotonic() - started
            summary["results"].append(result)
            summary["completed_count"] = index
            summary["aggregates"] = aggregate(summary["results"])
            paired.write_json(args.output / "summary.json", summary)
        summary["status"] = (
            "COMPLETED"
            if all(result.get("status") == "COMPLETED" for result in summary["results"])
            else "COMPLETED_WITH_FAILURES"
        )
        paired.write_json(args.output / "summary.json", summary)
        after_hashes = paired.base.snapshot_hashes(frozen_code_paths(args.source_stage))
        if after_hashes != frozen_hashes:
            summary["status"] = "STOP_CODE_CHANGED"
            summary["code_sha256_after"] = after_hashes
            paired.write_json(args.output / "summary.json", summary)
            return 2
        return 0 if summary["status"] == "COMPLETED" else 3


if __name__ == "__main__":
    raise SystemExit(main())
