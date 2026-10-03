#!/usr/bin/env python3
"""Run a bounded timeline-gap stereo fusion trial for one record.

Development-only runner.  It reuses the current native corpus source stage and
the approved timeline-gap source report to build two solver arms with unchanged
backend parameters:

* native cached recovery-only control
* native cached recovery + timeline-gap candidates

It does not run frontends, use GT for solver input, change weights/caps, or
overwrite existing outputs.  Root decides when to launch real solver/scorer
runs; tests in this file use mocked solver paths only.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
import time
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import evaluate_independent_ir_corpus_probe as corpus_eval  # noqa: E402
import evaluate_sift_lm_dual_combined_probe as paired  # noqa: E402
import probe_independent_ir_timeline_gap as gap_probe  # noqa: E402
import run_learned_segment_probe as base  # noqa: E402
import run_physical_stereo_lever_probe as physical  # noqa: E402
from ego_vio.vio.recovered_stereo_pairs import build_recovered_shared_rows  # noqa: E402
from ego_vio.vio.timeline_gap_stereo_candidates import build_timeline_gap_shared_rows  # noqa: E402


SCHEMA = "umi_timeline_gap_fusion_trial_v1"
CONTROL_VARIANT = "native_cached_recovery_control"
GAP_VARIANT = "native_cached_recovery_timeline_gap"


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def file_hash(path: Path) -> str:
    return base.file_hash(Path(path))


def snapshot_paths(paths: list[Path]) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in paths:
        resolved = str(Path(path).resolve())
        if resolved in out:
            continue
        if not Path(path).is_file():
            raise ValueError(f"consumed path missing: {resolved}")
        out[resolved] = file_hash(Path(path))
    return out


def assert_hashes_unchanged(before: dict[str, str]) -> None:
    for source, expected in before.items():
        path = Path(source)
        if not path.is_file() or file_hash(path) != expected:
            raise ValueError(f"consumed source changed during timeline-gap trial: {source}")


def gap_guard_paths(report: dict[str, Any]) -> list[Path]:
    guard = report.get("consumed_source_guard")
    if not isinstance(guard, dict):
        raise ValueError("timeline gap consumed source guard missing")
    before = guard.get("guarded_before_sha256")
    if not isinstance(before, dict) or not before:
        raise ValueError("timeline gap consumed source hashes missing")
    return [Path(item["path"]).resolve() for item in before.values()]


def frozen_code_paths(source_stage: Path, gap_report: Path) -> list[Path]:
    return [
        *physical.frozen_code_paths(),
        ROOT / "ego_vio/vio/recovered_stereo_pairs.py",
        ROOT / "ego_vio/vio/timeline_gap_stereo_candidates.py",
        ROOT / "scripts/evaluate_independent_ir_corpus_probe.py",
        ROOT / "scripts/evaluate_sift_lm_dual_combined_probe.py",
        ROOT / "scripts/probe_independent_ir_timeline_gap.py",
        Path(__file__),
        source_stage / "preflight_report.json",
        gap_report,
    ]


def _require_false(report: dict[str, Any], key: str, label: str) -> None:
    if report.get(key) is not False:
        raise ValueError(f"{label} {key} must be false")


def load_gap_report(path: Path, record: dict[str, Any], baseline_candidate: dict[str, Any]) -> dict[str, Any]:
    report = read_json(path)
    if report.get("schema") != gap_probe.SCHEMA:
        raise ValueError("timeline gap report schema mismatch")
    if report.get("status") != gap_probe.READY_STATUS:
        raise ValueError("timeline gap report is not ready")
    if report.get("id") != record["id"]:
        raise ValueError("timeline gap report id mismatch")
    if Path(report.get("session", "")).resolve() != Path(record["session"]).resolve():
        raise ValueError("timeline gap report session mismatch")
    for key in (
        "external_ground_truth_used",
        "slam_supervision",
        "tracker_reference_used",
        "backend_launched",
        "scorer_launched",
        "gpu_model_used",
        "production_promoted",
    ):
        _require_false(report, key, "timeline gap report")
    lineage = report.get("image_source_lineage")
    if not isinstance(lineage, dict) or lineage.get("selected_frames_loaded_from_db3_directly") is not True:
        raise ValueError("timeline gap report must use DB3-direct selected frames")
    guard = report.get("consumed_source_guard")
    if not isinstance(guard, dict) or guard.get("guarded_after_verified") is not True:
        raise ValueError("timeline gap consumed source guard missing")
    before = guard.get("guarded_before_sha256")
    after = guard.get("guarded_after_sha256")
    if not isinstance(before, dict) or not before:
        raise ValueError("timeline gap consumed source hashes missing")
    if not isinstance(after, dict) or set(after) != set(before):
        raise ValueError("timeline gap before/after guard labels mismatch")
    db3 = str(Path(lineage.get("db3", "")).resolve())
    if db3 not in {str(Path(item["path"]).resolve()) for item in before.values()}:
        raise ValueError("timeline gap DB3 is not source-guarded")
    for label, item in before.items():
        path = Path(item["path"]).resolve()
        expected = item["sha256"]
        if file_hash(path) != expected:
            raise ValueError(f"timeline gap consumed source hash changed: {label}")
        after_item = after[label]
        if Path(after_item.get("path", "")).resolve() != path:
            raise ValueError(f"timeline gap before/after guard path mismatch: {label}")
        if after_item.get("sha256") != expected:
            raise ValueError(f"timeline gap before/after guard mismatch: {label}")
    _validate_gap_camera_transforms(report, baseline_candidate)
    return report


def _validate_gap_camera_transforms(report: dict[str, Any], baseline_candidate: dict[str, Any]) -> None:
    conversion = report.get("camera_pose_conversion", {})
    for eye, key in (("left", "body_T_left_ir"), ("right", "body_T_right_ir")):
        metadata = physical.validate_eye_metadata(baseline_candidate, eye)
        expected = np.asarray(metadata["effective_body_T_camera"], dtype=float)
        observed = np.asarray(conversion.get(key), dtype=float)
        if observed.shape != (4, 4) or not np.all(np.isfinite(observed)):
            raise ValueError(f"timeline gap {key} is invalid")
        rotation = observed[:3, :3]
        if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-9) or np.linalg.det(rotation) <= 0.0:
            raise ValueError(f"timeline gap {key} rotation is not proper")
        if not np.allclose(observed, expected, atol=1e-9):
            raise ValueError(f"timeline gap {key} does not match baseline eye metadata")


def load_full_d405_times(frame_csv: Path) -> np.ndarray:
    with Path(frame_csv).open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if not rows or "infrared_left_device_ms" not in rows[0]:
        raise ValueError(f"invalid D405 frame timeline: {frame_csv}")
    times = np.asarray([float(row["infrared_left_device_ms"]) / 1000.0 for row in rows], dtype=float)
    if times.ndim != 1 or times.size == 0 or not np.all(np.isfinite(times)) or np.any(np.diff(times) <= 0):
        raise ValueError(f"D405 frame timeline is not strictly increasing: {frame_csv}")
    return times


def build_native_recovery_rows(record: dict[str, Any], baseline_candidate: dict[str, Any], state: Any, stage_record: dict[str, Any], source_stage: dict[str, Any], original_stereo: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], list[Path]]:
    _original_candidates, original_eye_reports, original_paths = physical.load_all_eye_candidates(record, baseline_candidate, state.times)
    left_paths = [Path(path) for path in stage_record["left_source_paths"]]
    right_paths = [Path(path) for path in stage_record["right_source_paths"]]
    overrides = corpus_eval.report_source_override_sha256(stage_record)
    left_candidates, left_report, left_consumed = paired.load_override_eye_candidates_from_reports(
        record, "left", baseline_candidate, state.times, left_paths, refined_override_sha256=overrides
    )
    right_refresh, right_refresh_paths = corpus_eval.validate_right_geometry_refresh_evidence(stage_record, right_paths)
    right_candidates, right_report, right_consumed = paired.load_override_eye_candidates_from_reports(
        record, "right", baseline_candidate, state.times, right_paths, refined_override_sha256=overrides
    )
    eye_reports = {
        "schema": "timeline_gap_trial_native_eye_candidates_v1",
        "original": original_eye_reports,
        "left": left_report,
        "right": {**right_report, "independent_right_geometry_refresh": right_refresh},
    }
    recovered, recovery_proof, recovery_paths = corpus_eval.validate_recovery_appendix(
        record,
        stage_record,
        baseline_candidate,
        state.times,
        eye_reports,
        source_stage,
    )
    existing_candidates = [*left_candidates, *right_candidates]
    templates, all_candidates, recovered_diag = build_recovered_shared_rows(
        state.times,
        original_stereo,
        existing_candidates,
        recovered,
    )
    native_rows, native_report = physical.transform_shared_rows(state, all_candidates, templates)
    native_report.update(
        {
            "schema": "timeline_gap_trial_native_recovery_control_v1",
            "eye_candidate_reports": eye_reports,
            "recovery_appendix": recovery_proof,
            "recovered_shared_rows_diagnostic": recovered_diag,
        }
    )
    consumed = [*original_paths, *left_consumed, *right_consumed, *right_refresh_paths, *recovery_paths]
    return native_rows, all_candidates, native_report, consumed


def run_record(
    record: dict[str, Any],
    *,
    baseline: Path,
    constant_gauge: Path,
    combined_reference: Path,
    source_stage: dict[str, Any],
    gap_report_path: Path,
    output: Path,
    frozen_hashes: dict[str, str],
    gap_support_policy: str = "all",
) -> dict[str, Any]:
    record_id = record["id"]
    record_dir = output / record_id
    if record_dir.exists() or record_dir.is_symlink():
        raise ValueError(f"record output must be new: {record_dir}")
    baseline_artifact = physical.baseline_artifact_dir(baseline, record_id)
    result = {"id": record_id, "status": "IN_PROGRESS", "variants": {}}
    stage_record = corpus_eval.validate_source_stage_record(record_id, source_stage)
    base.validate_record_sources(record)
    baseline_candidate, baseline_graph = base.validate_baseline_artifact(record, baseline_artifact)
    state = base.load_bound_reference(record)
    base.validate_baseline_trajectory_identity(baseline_artifact, state, baseline_graph)
    original_stereo = read_json(baseline_artifact / "shared_stereo_observations.json")
    native_rows, native_candidates, native_report, native_paths = build_native_recovery_rows(
        record,
        baseline_candidate,
        state,
        stage_record,
        source_stage,
        original_stereo,
    )
    gap_report = load_gap_report(gap_report_path, record, baseline_candidate)
    full_d405_times = load_full_d405_times(Path(record["session"]) / "d405_frames.csv")
    gap_rows, _gap_candidates, gap_diag = build_timeline_gap_shared_rows(
        state.times,
        state.rotations.as_matrix(),
        full_d405_times,
        native_rows,
        native_candidates,
        gap_report,
        support_policy=gap_support_policy,
    )
    gap_report_for_solver = {
        **native_report,
        "schema": "timeline_gap_trial_native_recovery_plus_gap_v1",
        "gap_support_policy": gap_support_policy,
        "timeline_gap_candidates": gap_diag,
    }
    constant_artifact, _constant_candidate, _constant_graph, constant_paths = physical.validate_constant_artifact(record, constant_gauge)
    combined_artifact, combined_candidate, combined_paths = paired.validate_combined_reference(record, baseline_artifact, combined_reference)
    consumed_before = snapshot_paths([
        baseline_artifact / "candidate_manifest.json",
        baseline_artifact / "graph_report.json",
        baseline_artifact / "shared_stereo_observations.json",
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
        gap_report_path,
        Path(record["session"]) / "d405_frames.csv",
        Path(record["session"]) / "external_imu/imu.bin",
        Path(record["vins_dir"]) / "vio_corrected_stream.csv",
        Path(record["vins_dir"]) / "run_acceptance.json",
        *gap_guard_paths(gap_report),
        *native_paths,
        *constant_paths,
        *combined_paths,
    ])
    result["reference_current_best"] = {
        "artifact_dir": str(combined_artifact.resolve()),
        "role": "frozen_currentbest_comparator_not_replayed_control",
        "schema": combined_candidate.get("schema"),
        "candidate_manifest_sha256": file_hash(combined_artifact / "candidate_manifest.json"),
        "graph_report_sha256": file_hash(combined_artifact / "graph_report.json"),
        "body_trajectory_fused_sha256": file_hash(combined_artifact / "body_trajectory_fused.csv"),
    }
    arms = {
        CONTROL_VARIANT: (native_rows, native_report, "native_cached_recovery_only"),
        GAP_VARIANT: (gap_rows, gap_report_for_solver, "native_cached_recovery_plus_timeline_gap"),
    }
    for variant, (rows, report, role) in arms.items():
        variant_dir = record_dir / variant
        variant_dir.mkdir(parents=True)
        result["variants"][variant] = physical.run_solver_variant(
            record,
            baseline_artifact,
            variant,
            variant_dir,
            state,
            baseline_candidate,
            rows,
            report,
            [*native_paths, gap_report_path],
            read_json(constant_artifact / "local_motion_factors.json"),
            "constant_ir_gauge_selected",
            [*constant_paths, *combined_paths, gap_report_path],
            frozen_hashes,
        )
        result["variants"][variant]["arm_role"] = role
    assert_hashes_unchanged(consumed_before)
    result["status"] = (
        "COMPLETED"
        if all("score" in arm for arm in result["variants"].values())
        else "INCOMPLETE_VARIANTS"
    )
    return result


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for variant in (CONTROL_VARIANT, GAP_VARIANT):
        scores = [
            result.get("variants", {}).get(variant, {}).get("score")
            for result in results
            if result.get("variants", {}).get(variant, {}).get("score") is not None
        ]
        out[variant] = {
            "scored_count": len(scores),
            "precision_pass_count": sum(score.get("result") == "PASS" for score in scores),
            "worst_max_m": max((score.get("ate_translation_max_m") for score in scores), default=None),
        }
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--constant-gauge", type=Path, required=True)
    parser.add_argument("--combined-reference", type=Path, required=True)
    parser.add_argument("--source-stage", type=Path, required=True)
    parser.add_argument("--gap-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--gap-support-policy", choices=("all", "nonoverlap"), default="all")
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink():
        parser.error("output must be new; previous results are never overwritten")
    manifest = read_json(args.manifest)
    records = base.validate_records(manifest, [args.dataset])
    if len(records) != 1:
        raise ValueError("timeline gap trial requires exactly one dataset")
    baseline_summary = read_json(args.baseline / "summary.json")
    baseline_by_id = base.baseline_results(baseline_summary)
    if args.dataset not in baseline_by_id:
        raise ValueError(f"baseline summary missing dataset: {args.dataset}")
    source_stage = corpus_eval.load_source_stage(args.source_stage)
    frozen_hashes = base.snapshot_hashes(frozen_code_paths(args.source_stage, args.gap_report))
    args.output.mkdir(parents=True)
    summary = {
        "schema": SCHEMA,
        "status": "RUNNING",
        "development_only": True,
        "blind_test": False,
        "production_promoted": False,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "dataset": args.dataset,
        "manifest_sha256": file_hash(args.manifest),
        "baseline_summary_sha256": file_hash(args.baseline / "summary.json"),
        "source_stage_preflight_sha256": source_stage["sha256"],
        "gap_report_sha256": file_hash(args.gap_report),
        "gap_support_policy": args.gap_support_policy,
        "variants": [CONTROL_VARIANT, GAP_VARIANT],
        "code_sha256": frozen_hashes,
        "results": [],
    }
    write_json(args.output / "summary.json", summary)
    started = time.monotonic()
    if base.code_changed(frozen_hashes):
        summary["status"] = "STOP_CODE_CHANGED"
        write_json(args.output / "summary.json", summary)
        return 2
    try:
        result = run_record(
            records[0],
            baseline=args.baseline,
            constant_gauge=args.constant_gauge,
            combined_reference=args.combined_reference,
            source_stage=source_stage,
            gap_report_path=args.gap_report.resolve(),
            output=args.output,
            frozen_hashes=frozen_hashes,
            gap_support_policy=args.gap_support_policy,
        )
    except base.StopCodeChanged:
        summary["status"] = "STOP_CODE_CHANGED"
        write_json(args.output / "summary.json", summary)
        return 2
    except Exception as error:
        result = {
            "id": args.dataset,
            "status": "FAILED_PRECHECK_OR_SOLVER_EXCEPTION",
            "error": f"{type(error).__name__}: {error}",
            "denominator_retained": True,
            "variants": {},
        }
    result["elapsed_s"] = time.monotonic() - started
    summary["results"].append(result)
    summary["aggregates"] = aggregate(summary["results"])
    summary["status"] = "COMPLETED" if result.get("status") == "COMPLETED" else "COMPLETED_WITH_FAILURES"
    after_hashes = base.snapshot_hashes(frozen_code_paths(args.source_stage, args.gap_report))
    if after_hashes != frozen_hashes:
        summary["status"] = "STOP_CODE_CHANGED"
        summary["code_sha256_after"] = after_hashes
        write_json(args.output / "summary.json", summary)
        return 2
    write_json(args.output / "summary.json", summary)
    return 0 if summary["status"] == "COMPLETED" else 3


if __name__ == "__main__":
    raise SystemExit(main())
