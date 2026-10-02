#!/usr/bin/env python3
"""Evaluate an honest 25-record independent-IR native source corpus.

This is a thin, process-local adapter over the current-best paired evaluator.
It consumes the typed corpus source registry produced by
``extract_independent_ir_corpus_sources.py`` and does not serialize fake
``refined_*``/SIFT-LM provenance.  Ready rows are adapted in memory only.
"""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import sys
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import evaluate_independent_ir_recovery_probe as recovery  # noqa: E402
import evaluate_sift_lm_dual_combined_probe as paired  # noqa: E402
from ego_vio.vio.recovered_stereo_pairs import build_recovered_shared_rows  # noqa: E402


SCHEMA = "umi_independent_ir_corpus_paired_probe_v1"
SOURCE_SCHEMA = "umi_independent_ir_corpus_native_sources_v1"
RIGHT_REFRESH_PARENT_SCHEMA = "umi_independent_ir_corpus_right_refresh_record_v1"
SOURCE_PREFLIGHT_SCHEMA = "umi_independent_ir_corpus_source_preflight_v1"
READY_STATUS = "INDEPENDENT_IR_CORPUS_NATIVE_SOURCES_READY"
RETAINED_STATUSES = {"RETAINED_UNOBSERVABLE_UNSCORED"}
TECHNICAL_FAILED_STATUSES = {"INDEPENDENT_IR_CORPUS_NATIVE_SOURCE_FAILED"}
REFINED_VARIANT = "currentbest_independent_ir_corpus_native_recovery"


def read_json(path: Path) -> Any:
    return paired.read_json(path)


def file_hash(path: Path) -> str:
    return paired.file_hash(path)


def _require_source_only(report: dict[str, Any], label: str) -> None:
    if report.get("external_ground_truth_used") is not False:
        raise ValueError(f"{label} used ground truth")
    if report.get("slam_supervision") is not False:
        raise ValueError(f"{label} used slam supervision")


def _hash_bound_paths(paths: list[Any], overrides: dict[str, str], label: str) -> list[Path]:
    if len(paths) != 4:
        raise ValueError(f"{label} must contain the four stereo report paths")
    out = [Path(path).resolve() for path in paths]
    if len({str(path) for path in out}) != len(out):
        raise ValueError(f"{label} contains duplicate report paths")
    for path in out:
        resolved = str(path)
        if resolved not in overrides:
            raise ValueError(f"{label} path missing source_override_sha256: {resolved}")
        if file_hash(path) != overrides[resolved]:
            raise ValueError(f"{label} source hash changed: {resolved}")
    return out


def _validate_ready_record(row: dict[str, Any], stage_path: Path) -> dict[str, Any]:
    if any(key in row for key in ("refined_left_sources", "refined_right_sources")):
        raise ValueError("native source stage must not serialize fake refined_* fields")
    if not row.get("id"):
        raise ValueError("source record missing id")
    if not row.get("session"):
        raise ValueError(f"source record missing session: {row.get('id')}")
    if row.get("external_ground_truth_used") is not False or row.get("slam_supervision") is not False:
        raise ValueError(f"ready source record is not source-only: {row['id']}")
    if row.get("ready_for_consumer") is not True:
        raise ValueError(f"ready source record missing ready_for_consumer: {row['id']}")
    overrides = row.get("source_override_sha256")
    if not isinstance(overrides, dict) or not overrides:
        raise ValueError(f"source override hashes missing: {row['id']}")
    left_paths = _hash_bound_paths(row.get("left_source_paths", []), overrides, "left_source_paths")
    right_paths = _hash_bound_paths(row.get("right_source_paths", []), overrides, "right_source_paths")
    appendix = Path(row.get("recovery_appendix_path", "")).resolve()
    if not appendix.is_file() or file_hash(appendix) != row.get("recovery_appendix_sha256"):
        raise ValueError(f"recovery appendix hash mismatch: {row['id']}")
    proof = row.get("right_refresh_proof")
    if not isinstance(proof, dict) or proof.get("schema") != "umi_independent_right_geometry_refresh_v1":
        raise ValueError(f"right_refresh_proof schema mismatch: {row['id']}")
    _require_source_only(proof, f"right_refresh_proof {row['id']}")
    reports = proof.get("reports")
    if not isinstance(reports, dict) or set(reports) != {str(path) for path in right_paths}:
        raise ValueError(f"right_refresh_proof reports do not match right_source_paths: {row['id']}")
    source_preflight = row.get("source_preflight")
    if not isinstance(source_preflight, dict):
        raise ValueError(f"ready row source_preflight binding missing: {row['id']}")
    source_preflight_path = Path(source_preflight.get("path", "")).resolve()
    if not source_preflight_path.is_file():
        raise ValueError(f"ready row source_preflight missing: {row['id']}")
    if source_preflight.get("sha256") != file_hash(source_preflight_path):
        raise ValueError(f"ready row source_preflight hash mismatch: {row['id']}")
    return {
        **row,
        "status": row["status"],
        "left_source_paths": [str(path) for path in left_paths],
        "right_source_paths": [str(path) for path in right_paths],
        "recovery_appendix_path": str(appendix),
        "source_stage_preflight": str(stage_path.resolve()),
        "source_stage_preflight_sha256": file_hash(stage_path),
    }


def load_source_stage(path: Path) -> dict[str, Any]:
    report_path = path / "preflight_report.json"
    report = read_json(report_path)
    if report.get("schema") != SOURCE_SCHEMA:
        raise ValueError("source stage schema mismatch")
    if report.get("status") not in {"NATIVE_SOURCES_COMPLETE", "NATIVE_SOURCES_WITH_FAILURES"}:
        raise ValueError("source stage status is not a completed preflight")
    _require_source_only(report, "source stage")
    records = report.get("records")
    if not isinstance(records, list):
        raise ValueError("source stage records missing")
    ids = [row.get("id") for row in records]
    if any(not item for item in ids):
        raise ValueError("source stage record missing id")
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate source stage record id")
    if report.get("record_count") is not None and int(report["record_count"]) != len(records):
        raise ValueError("source stage record_count mismatch")
    by_id: dict[str, dict[str, Any]] = {}
    ready_count = 0
    retained_count = 0
    technical_failed_count = 0
    for row in records:
        status = row.get("status")
        if status == READY_STATUS:
            by_id[row["id"]] = _validate_ready_record(row, report_path)
            ready_count += 1
        elif status in RETAINED_STATUSES:
            by_id[row["id"]] = {**row, "ready_for_probe": False}
            retained_count += 1
        elif status in TECHNICAL_FAILED_STATUSES:
            by_id[row["id"]] = {**row, "ready_for_probe": False}
            technical_failed_count += 1
        else:
            raise ValueError(f"unsupported source record status for {row['id']}: {status}")
    return {
        "path": report_path.resolve(),
        "sha256": file_hash(report_path),
        "raw": report,
        "by_id": by_id,
        "ready_record_count": ready_count,
        "retained_record_count": retained_count,
        "technical_failed_record_count": technical_failed_count,
    }


def validate_source_stage_record(record_id: str, source_stage: dict[str, Any]) -> dict[str, Any]:
    try:
        row = source_stage["by_id"][record_id]
    except KeyError as exc:
        raise ValueError(f"source stage missing record: {record_id}") from exc
    if row.get("status") != READY_STATUS:
        raise ValueError(f"source record is not ready for probe: {record_id} {row.get('status')}")
    return row


def report_source_override_sha256(row: dict[str, Any]) -> dict[str, str]:
    report_paths = [*row["left_source_paths"], *row["right_source_paths"]]
    overrides = row["source_override_sha256"]
    return {str(Path(path).resolve()): overrides[str(Path(path).resolve())] for path in report_paths}


def source_preflight_binding(value: Any, sha_value: Any = None) -> tuple[Path, str]:
    if isinstance(value, dict):
        path = value.get("path")
        sha = value.get("sha256")
    else:
        path = value
        sha = sha_value
    if not path or not sha:
        raise ValueError("source_preflight path/hash missing")
    return Path(path).resolve(), str(sha)


def load_bound_source_preflight(path: Path, expected_sha256: str, record_id: str, session: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not path.is_file() or file_hash(path) != expected_sha256:
        raise ValueError("source_preflight hash mismatch")
    report = read_json(path)
    if report.get("schema") != SOURCE_PREFLIGHT_SCHEMA:
        raise ValueError("source_preflight schema mismatch")
    _require_source_only(report, "source_preflight")
    records = report.get("records")
    if not isinstance(records, list):
        raise ValueError("source_preflight records missing")
    matches = [row for row in records if row.get("id") == record_id]
    if len(matches) != 1:
        raise ValueError("source_preflight record id mismatch")
    row = matches[0]
    if Path(row.get("session", "")).resolve() != Path(session).resolve():
        raise ValueError("source_preflight session mismatch")
    status = row.get("status")
    ready = status == "INDEPENDENT_IR_CORPUS_SOURCE_PREFLIGHT_READY" and row.get("ready_for_image_extract") is True
    if not ready:
        raise ValueError("source_preflight record is not ready")
    return report, row


def _metadata_report_items(source_row: dict[str, Any], eye: str) -> dict[str, dict[str, Any]]:
    eye_row = source_row.get(eye)
    if not isinstance(eye_row, dict):
        raise ValueError(f"source_preflight {eye} metadata missing")
    reports = eye_row.get("source_reports")
    if not isinstance(reports, list):
        raise ValueError(f"source_preflight {eye} source_reports missing")
    out: dict[str, dict[str, Any]] = {}
    for item in reports:
        path = str(Path(item.get("path", "")).resolve())
        if not path or path in out:
            raise ValueError(f"source_preflight duplicate/missing {eye} report path")
        out[path] = item
    return out


def validate_source_metadata_bindings(
    *,
    source_path: Path,
    source_sha256: str,
    record_id: str,
    session: str,
    left_paths: list[Path],
    right_proof: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    _report, source_row = load_bound_source_preflight(source_path, source_sha256, record_id, session)
    left_items = _metadata_report_items(source_row, "left")
    right_items = _metadata_report_items(source_row, "right")
    if [str(path.resolve()) for path in left_paths] != list(left_items):
        raise ValueError("source_preflight LEFT report paths mismatch")
    left_lineage = source_row.get("left", {}).get("lineage", {})
    if left_lineage != {
        "actual_source": "original_baseline_left_reports",
        "not_sift_lm_refined_source": True,
        "not_refined_lineage_alias": True,
    }:
        raise ValueError("source_preflight LEFT lineage mismatch")
    right_lineage = source_row.get("right", {}).get("lineage", {})
    if right_lineage != {
        "actual_source": "original_baseline_derived_right_reports",
        "not_sift_lm_refined_source": True,
        "not_refined_lineage_alias": True,
    }:
        raise ValueError("source_preflight RIGHT lineage mismatch")
    right_raw = source_row.get("right", {}).get("raw_geometry_trajectory", {})
    right_raw_path = Path(right_raw.get("path", "")).resolve()
    right_raw_sha = right_raw.get("sha256")
    for output_path, binding in right_proof.get("reports", {}).items():
        fallback = str(Path(binding.get("fallback_right_source_path", "")).resolve())
        if fallback not in right_items:
            raise ValueError(f"RIGHT fallback report is outside source_preflight metadata: {output_path}")
        item = right_items[fallback]
        if item.get("sha256") != binding.get("fallback_right_source_sha256"):
            raise ValueError(f"RIGHT fallback report sha mismatch: {fallback}")
        if file_hash(Path(fallback)) != item.get("sha256"):
            raise ValueError(f"RIGHT fallback report current hash mismatch: {fallback}")
        derived = item.get("derived_from_left_stereo_report")
        derived_path = str(Path(derived or "").resolve())
        if not derived or derived_path not in left_items:
            raise ValueError(f"RIGHT fallback derived LEFT path mismatch: {fallback}")
        if file_hash(Path(derived_path)) != left_items[derived_path].get("sha256"):
            raise ValueError(f"RIGHT fallback derived LEFT hash mismatch: {fallback}")
        raw_path = Path(binding.get("raw_right_geometry_trajectory", "")).resolve()
        if raw_path != right_raw_path or binding.get("raw_right_geometry_trajectory_sha256") != right_raw_sha:
            raise ValueError(f"RIGHT raw trajectory binding mismatch: {fallback}")
        if file_hash(raw_path) != right_raw_sha:
            raise ValueError(f"RIGHT raw trajectory current hash mismatch: {raw_path}")
    return source_row, left_items, right_items


def admitted_report_paths(report_paths: list[Path]) -> set[str]:
    reports = []
    for path in report_paths:
        report = read_json(path)
        reports.append({**report, "report_path": str(path.resolve())})
    merged = paired.fusion.merge_stereo_reports(reports[0], reports[1:], optional_policy="reject_window")
    return {str(Path(path).resolve()) for path in merged.get("merged_report_paths", [])}


def adapt_ready_record_for_paired_runner(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "refined_left_sources": list(row["left_source_paths"]),
        "refined_right_sources": list(row["right_source_paths"]),
        "source_override_sha256": report_source_override_sha256(row),
        "independent_right_geometry_refresh": row["right_refresh_proof"],
        "recovery_appendix_path": row["recovery_appendix_path"],
        "recovery_appendix_sha256": row["recovery_appendix_sha256"],
    }


def source_upgrade_scope(source_override_sha256: dict[str, str], arm_role: str) -> dict[str, Any]:
    if not source_override_sha256:
        return {
            "source": "currentbest_original_control",
            "right_accepted_refresh": False,
            "symmetric_rejected_nonlow_recovery": False,
            "left_source_paths_original_baseline": True,
            "right_eye_used_as_independent_evidence": False,
        }
    return {
        "source": "independent_ir_corpus_native_sources",
        "arm_role": arm_role,
        "right_accepted_refresh": True,
        "symmetric_rejected_nonlow_recovery": True,
        "left_source_paths_original_baseline": True,
        "learned_factor_context_unchanged": True,
        "constant_gauge_context_unchanged": True,
        "time_quaternion_weight_gate_context_unchanged": True,
        "shared_factor_policy": "build_recovered_shared_rows; one physical factor per pair",
        "right_eye_used_as_independent_evidence": False,
    }


def validate_recovery_appendix(
    record: dict[str, Any],
    row: dict[str, Any],
    baseline_candidate: dict[str, Any],
    reference_times,
    eye_reports: dict[str, Any],
    source_stage: dict[str, Any],
):
    appendix_path = Path(row["recovery_appendix_path"])
    appendix = read_json(appendix_path)
    parent = Path(appendix.get("source_stage_preflight", "")).resolve()
    if not parent.is_file() or file_hash(parent) != appendix.get("source_stage_preflight_sha256"):
        raise ValueError("recovery appendix source_stage_preflight hash mismatch")
    parent_row = validate_right_refresh_parent(record, row, parent)
    if appendix.get("native_source_schema") != SOURCE_SCHEMA:
        raise ValueError("recovery appendix native source schema mismatch")
    adapter_row = adapt_ready_record_for_paired_runner(parent_row)
    parent_stage = {"refined_by_id": {record["id"]: adapter_row}}
    adapted_appendix = dict(appendix)
    original_read = recovery.paired.read_json

    def read_json_adapter(path):
        if Path(path).resolve() == appendix_path.resolve():
            return adapted_appendix
        return original_read(path)

    with patch.object(recovery.paired, "load_source_stage", lambda _path: parent_stage), patch.object(
        recovery.paired.source_eval,
        "validate_source_stage_record",
        lambda record_id, stage: stage["refined_by_id"][record_id],
    ), patch.object(
        recovery.paired,
        "read_json",
        read_json_adapter,
    ):
        return recovery.validate_recovery_appendix(
            record,
            adapter_row,
            baseline_candidate,
            reference_times,
            eye_reports,
        )


def validate_right_refresh_parent(record: dict[str, Any], row: dict[str, Any], path: Path) -> dict[str, Any]:
    parent = read_json(path)
    if parent.get("schema") != RIGHT_REFRESH_PARENT_SCHEMA:
        raise ValueError("right refresh parent schema mismatch")
    _require_source_only(parent, "right refresh parent")
    if parent.get("id") != record["id"] or parent.get("session") != record["session"]:
        raise ValueError("right refresh parent record/session mismatch")
    for key in ("left_source_paths", "right_source_paths"):
        parent_paths = [str(Path(item).resolve()) for item in parent.get(key, [])]
        row_paths = [str(Path(item).resolve()) for item in row.get(key, [])]
        if parent_paths != row_paths:
            raise ValueError(f"right refresh parent {key} mismatch")
    if parent.get("source_override_sha256") != report_source_override_sha256(row):
        raise ValueError("right refresh parent source_override_sha256 mismatch")
    if parent.get("right_refresh_proof") != row.get("right_refresh_proof"):
        raise ValueError("right refresh parent proof mismatch")
    source_path, source_sha = source_preflight_binding(parent.get("source_preflight"), parent.get("source_preflight_sha256"))
    row_source_path, row_source_sha = source_preflight_binding(row.get("source_preflight"), row.get("source_preflight_sha256"))
    if source_path != row_source_path:
        raise ValueError("right refresh parent source_preflight path mismatch")
    if source_sha != row_source_sha:
        raise ValueError("right refresh parent source_preflight sha mismatch")
    validate_source_metadata_bindings(
        source_path=source_path,
        source_sha256=source_sha,
        record_id=record["id"],
        session=record["session"],
        left_paths=[Path(path) for path in row["left_source_paths"]],
        right_proof=row["right_refresh_proof"],
    )
    return {**row, **parent}


def validate_right_geometry_refresh_evidence(
    stage_record: dict[str, Any],
    right_paths: list[Path],
) -> tuple[dict[str, Any], list[Path]]:
    proof = stage_record["right_refresh_proof"]
    if proof.get("schema") != "umi_independent_right_geometry_refresh_v1":
        raise ValueError("RIGHT geometry refresh schema mismatch")
    _require_source_only(proof, "RIGHT geometry refresh evidence")
    source_preflight = Path(proof.get("source_preflight", "")).resolve()
    if file_hash(source_preflight) != proof.get("source_preflight_sha256"):
        raise ValueError("RIGHT geometry refresh source preflight hash mismatch")
    reports = proof.get("reports")
    if not isinstance(reports, dict) or set(reports) != {str(path.resolve()) for path in right_paths}:
        raise ValueError("RIGHT geometry refresh report paths mismatch")
    fallback_paths = [Path(reports[str(path.resolve())]["fallback_right_source_path"]).resolve() for path in right_paths]
    admitted_fallbacks = admitted_report_paths(fallback_paths)
    refreshed_reports = {}
    refreshed_paths: list[Path] = []
    consumed: list[Path] = [source_preflight]
    optional_copied = 0
    for path in right_paths:
        resolved = str(path.resolve())
        binding = reports[resolved]
        fallback = Path(binding["fallback_right_source_path"]).resolve()
        raw = Path(binding["raw_right_geometry_trajectory"]).resolve()
        if file_hash(fallback) != binding["fallback_right_source_sha256"]:
            raise ValueError(f"RIGHT geometry refresh fallback hash mismatch: {fallback}")
        if file_hash(raw) != binding["raw_right_geometry_trajectory_sha256"]:
            raise ValueError(f"RIGHT geometry refresh raw trajectory hash mismatch: {raw}")
        consumed.extend([path.resolve(), fallback, raw])
        if binding.get("byte_copied_unadmitted_optional") is True:
            if str(fallback) in admitted_fallbacks:
                raise ValueError("byte-copied RIGHT report is admitted by normal merge policy")
            if file_hash(path) != file_hash(fallback):
                raise ValueError("byte-copied RIGHT optional report hash mismatch")
            if read_json(path) != read_json(fallback):
                raise ValueError("byte-copied RIGHT optional report content changed")
            optional_copied += 1
            continue
        refreshed_reports[resolved] = binding
        refreshed_paths.append(path.resolve())
    validated = dict(proof)
    if refreshed_paths:
        adapted = {
            **proof,
            "source_stage_preflight": proof["source_preflight"],
            "source_stage_preflight_sha256": proof["source_preflight_sha256"],
            "reports": refreshed_reports,
        }
        refreshed_validated, refreshed_consumed = paired.validate_right_geometry_refresh_evidence(
            {"independent_right_geometry_refresh": adapted},
            refreshed_paths,
        )
        validated.update(refreshed_validated)
        consumed.extend(refreshed_consumed)
    validated["optional_unadmitted_copied_count"] = optional_copied
    validated["refreshed_report_count"] = len(refreshed_paths)
    return validated, list(dict.fromkeys(consumed))


def frozen_code_paths(source_stage: Path) -> list[Path]:
    base_paths = getattr(paired, "_independent_ir_corpus_original_frozen_code_paths", paired.frozen_code_paths)
    return [
        *base_paths(source_stage),
        Path(__file__),
        ROOT / "scripts/evaluate_independent_ir_recovery_probe.py",
        ROOT / "ego_vio/vio/recovered_stereo_pairs.py",
    ]


def main(argv: list[str] | None = None) -> int:
    original = {
        "load_source_stage": paired.load_source_stage,
        "validate_source_stage_record": paired.source_eval.validate_source_stage_record,
        "load_refined_all_eye_candidates": paired.load_refined_all_eye_candidates,
        "refresh_shared_row_confidences": paired.refresh_shared_row_confidences,
        "run_record": paired.run_record,
        "source_upgrade_scope": paired.source_upgrade_scope,
        "frozen_code_paths": paired.frozen_code_paths,
        "SCHEMA": paired.SCHEMA,
        "REFINED_VARIANT": paired.REFINED_VARIANT,
    }
    context: dict[str, Any] = {}

    def load_candidates(record, baseline_candidate, reference_times, stage_record):
        if Path(stage_record["session"]).resolve() != Path(record["session"]).resolve():
            raise ValueError("source stage session mismatch")
        adapter = adapt_ready_record_for_paired_runner(stage_record)
        left_paths = [Path(path) for path in stage_record["left_source_paths"]]
        right_paths = [Path(path) for path in stage_record["right_source_paths"]]
        overrides = report_source_override_sha256(stage_record)
        left_candidates, left_report, left_consumed = paired.load_override_eye_candidates_from_reports(
            record,
            "left",
            baseline_candidate,
            reference_times,
            left_paths,
            refined_override_sha256=overrides,
        )
        right_refresh, right_refresh_paths = validate_right_geometry_refresh_evidence(stage_record, right_paths)
        right_candidates, right_report, right_consumed = paired.load_override_eye_candidates_from_reports(
            record,
            "right",
            baseline_candidate,
            reference_times,
            right_paths,
            refined_override_sha256=overrides,
        )
        eye_reports = {
            "schema": "independent_ir_corpus_eye_candidates_v1",
            "left": {**left_report, "schema": "independent_ir_corpus_left_original_eye_candidates_v1"},
            "right": {
                **right_report,
                "schema": "independent_ir_corpus_right_native_eye_candidates_v1",
                "independent_right_geometry_refresh": right_refresh,
            },
            "right_geometry_source": "independent_right_pixels_with_explicit_derived_fallback",
            "left_geometry_source": "original_baseline_left_reports",
            "right_eye_used_as_independent_evidence": False,
        }
        recovered, proof, recovery_paths = validate_recovery_appendix(
            record,
            stage_record,
            baseline_candidate,
            reference_times,
            eye_reports,
            context["source_stage"],
        )
        context.update(times=reference_times, existing=[*left_candidates, *right_candidates], recovered=recovered)
        templates, all_candidates, diagnostic = build_recovered_shared_rows(
            reference_times,
            context["original_rows"],
            context["existing"],
            recovered,
        )
        eye_reports["recovery_appendix"] = proof
        eye_reports["recovered_shared_rows_diagnostic"] = diagnostic
        eye_reports["recovered_shared_row_template_count"] = len(templates)
        return (
            all_candidates,
            eye_reports,
            [*left_consumed, *right_consumed, *right_refresh_paths, *recovery_paths],
            overrides,
        )

    def refresh(rows, candidates):
        templates, all_candidates, diagnostic = build_recovered_shared_rows(
            context["times"],
            rows,
            context["existing"],
            context["recovered"],
        )
        if all_candidates != candidates:
            raise ValueError("corpus recovery candidate construction changed across adapters")
        return templates, diagnostic

    def run_record(record, baseline, *args, **kwargs):
        source_stage = args[3]
        context.clear()
        context["source_stage"] = source_stage
        source_row = source_stage["by_id"].get(record["id"])
        if source_row is not None and source_row.get("status") != READY_STATUS:
            return {
                "id": record["id"],
                "status": source_row.get("status"),
                "source_stage_status": source_row.get("status"),
                "denominator_retained": True,
                "variants": {},
            }
        context["original_rows"] = paired.read_json(
            paired.baseline_artifact_dir(baseline, record["id"]) / "shared_stereo_observations.json"
        )
        return original["run_record"](record, baseline, *args, **kwargs)

    def arm_role(stage_record):
        return "independent_ir_corpus_right_refresh_symmetric_recovery"

    with ExitStack() as stack:
        paired._independent_ir_corpus_original_frozen_code_paths = original["frozen_code_paths"]
        patches = {
            "load_source_stage": load_source_stage,
            "load_refined_all_eye_candidates": load_candidates,
            "refresh_shared_row_confidences": refresh,
            "run_record": run_record,
            "source_upgrade_scope": source_upgrade_scope,
            "frozen_code_paths": frozen_code_paths,
            "SCHEMA": SCHEMA,
            "REFINED_VARIANT": REFINED_VARIANT,
            "refined_arm_role": arm_role,
        }
        for name, value in patches.items():
            stack.enter_context(patch.object(paired, name, value))
        stack.enter_context(patch.object(paired.source_eval, "validate_source_stage_record", validate_source_stage_record))
        try:
            return paired.main(argv)
        finally:
            if hasattr(paired, "_independent_ir_corpus_original_frozen_code_paths"):
                delattr(paired, "_independent_ir_corpus_original_frozen_code_paths")
            for name, value in original.items():
                target = paired.source_eval if name == "validate_source_stage_record" else paired
                attr = "validate_source_stage_record" if name == "validate_source_stage_record" else name
                if getattr(target, attr) is not value:
                    setattr(target, attr, value)


if __name__ == "__main__":
    raise SystemExit(main())
