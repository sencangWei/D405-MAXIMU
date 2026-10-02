#!/usr/bin/env python3
"""Evaluate SIFT-LM LEFT overrides in the current best dual physical context.

This development runner keeps the current-best context fixed:

* shared stereo selection is the existing dual LEFT/RIGHT physical policy
* RIGHT reports remain the original raw cached reports
* learned factors are the selected constant-gauge factors, identical in both arms
* VINS/IMU/backend/scorer/timestamps are unchanged

The only source change in the refined arm is the LEFT refined SIFT-LM stereo
reports produced by ``run_sift_lm_physical_probe.py --run``.  The original arm
must replay the existing current-best combined trajectory within 1e-7 m, or the
record is not accepted for comparison.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import fuse_mast3r_stereo_imu as fusion  # noqa: E402
import run_learned_segment_probe as base  # noqa: E402
import run_physical_stereo_lever_probe as physical  # noqa: E402
import evaluate_sift_lm_physical_source_probe as source_eval  # noqa: E402


SCHEMA = "umi_sift_lm_dual_combined_probe_v1"
ORIGINAL_VARIANT = "currentbest_original_dual_control"
REFINED_VARIANT = "currentbest_refined_left_sift_lm"
COMBINED_VARIANT = physical.COMBINED_VARIANT
CONTROL_REPLAY_MAX_DELTA_M = 1e-7
ROTATION_REPLAY_TOLERANCE_RAD = 5e-9


def read_json(path: Path) -> Any:
    return base.read_json(path)


def write_json(path: Path, value: Any) -> None:
    base.write_json(path, value)


def file_hash(path: Path) -> str:
    return base.file_hash(path)


def baseline_artifact_dir(baseline: Path, record_id: str) -> Path:
    return baseline / record_id / physical.BASELINE_POLICY


def combined_artifact_dir(combined_reference: Path, record_id: str) -> Path:
    return combined_reference / record_id / COMBINED_VARIANT


def frozen_code_paths(source_stage: Path) -> list[Path]:
    return [
        *physical.frozen_code_paths(),
        ROOT / "scripts/evaluate_sift_lm_physical_source_probe.py",
        ROOT / "scripts/run_sift_lm_physical_probe.py",
        Path(__file__),
        source_stage / "preflight_report.json",
    ]


def load_source_stage(path: Path) -> dict[str, Any]:
    return source_eval.load_source_stage(path)


def snapshot_paths(paths: list[Path]) -> dict[str, str]:
    hashes = {}
    for path in paths:
        resolved = str(Path(path).resolve())
        if resolved in hashes:
            continue
        if not Path(path).is_file():
            raise ValueError(f"consumed source path missing: {resolved}")
        hashes[resolved] = file_hash(Path(path))
    return hashes


def assert_hashes_unchanged(before: dict[str, str]) -> None:
    for source, expected in before.items():
        path = Path(source)
        if not path.is_file() or file_hash(path) != expected:
            raise ValueError(f"consumed source changed during record: {source}")


def validate_combined_reference(
    record: dict[str, Any],
    baseline_artifact: Path,
    root: Path,
) -> tuple[Path, dict[str, Any], list[Path]]:
    artifact = combined_artifact_dir(root, record["id"])
    paths = [
        artifact / "candidate_manifest.json",
        artifact / "graph_report.json",
        artifact / "local_motion_factors.json",
        artifact / "shared_stereo_observations.json",
        artifact / "body_trajectory_fused.csv",
    ]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise ValueError(f"missing combined reference artifact: {missing[:3]}")
    candidate = read_json(artifact / "candidate_manifest.json")
    graph = read_json(artifact / "graph_report.json")
    base.require_onboard_report(candidate, "combined reference candidate")
    base.require_onboard_report(graph, "combined reference graph")
    if candidate.get("schema") != "umi_physical_stereo_lever_candidate_v1":
        raise ValueError("combined reference candidate schema mismatch")
    if graph.get("schema") != "umi_physical_stereo_lever_graph_diagnostic_v1":
        raise ValueError("combined reference graph schema mismatch")
    if Path(candidate.get("session", "")).resolve() != Path(record["session"]).resolve():
        raise ValueError("combined reference session mismatch")
    if graph.get("output_frame") != "body_imu_origin":
        raise ValueError("combined reference graph output frame mismatch")
    policy = candidate.get("policy_arguments")
    if graph.get("policy_arguments") != policy:
        raise ValueError("combined reference candidate/graph policy mismatch")
    if not isinstance(policy, dict):
        raise ValueError("combined reference policy missing")
    if policy.get("variant") != COMBINED_VARIANT:
        raise ValueError("combined reference is not physical_stereo_constant_gauge")
    if policy.get("motion_factor_source") != "constant_ir_gauge_selected":
        raise ValueError("combined reference does not use constant selected motion factors")
    if policy.get("source_policy") != physical.BASELINE_POLICY:
        raise ValueError("combined reference source policy mismatch")
    if Path(candidate.get("source_baseline_artifact", "")).resolve() != baseline_artifact.resolve():
        raise ValueError("combined reference source baseline artifact mismatch")
    if Path(candidate.get("source_candidate_manifest", "")).resolve() != (baseline_artifact / "candidate_manifest.json").resolve():
        raise ValueError("combined reference source candidate manifest mismatch")
    input_hashes = candidate.get("input_sha256")
    if not isinstance(input_hashes, dict) or not input_hashes:
        raise ValueError("combined reference input_sha256 missing")
    for source, expected in input_hashes.items():
        path = Path(source)
        if not path.is_file() or file_hash(path) != expected:
            raise ValueError(f"combined reference input hash changed: {source}")
    if file_hash(artifact / "body_trajectory_fused.csv") != candidate.get("output_estimate_sha256"):
        raise ValueError("combined reference estimate hash mismatch")
    return artifact, candidate, paths


def validate_combined_replay(control_estimate: Path, combined_estimate: Path) -> dict[str, Any]:
    control_times, control_positions, control_rotations, control_rows = fusion.load_trajectory(control_estimate)
    combined_times, combined_positions, combined_rotations, combined_rows = fusion.load_trajectory(combined_estimate)
    if control_times.size == 0 or combined_times.size == 0:
        raise ValueError("control replay trajectory is empty")
    if len(control_rows) != len(combined_rows):
        raise ValueError("control replay sample count does not match combined reference")
    if (
        not np.all(np.isfinite(control_times))
        or not np.all(np.isfinite(combined_times))
        or not np.all(np.isfinite(control_positions))
        or not np.all(np.isfinite(combined_positions))
        or not np.all(np.isfinite(control_rotations.as_quat()))
        or not np.all(np.isfinite(combined_rotations.as_quat()))
    ):
        raise ValueError("control replay trajectory contains non-finite values")
    if control_times.shape != combined_times.shape or np.max(np.abs(control_times - combined_times)) > 1e-9:
        raise ValueError("control replay timeline does not match combined reference")
    rotation_error = (control_rotations.inv() * combined_rotations).magnitude()
    max_rotation_error = float(np.max(rotation_error))
    if max_rotation_error > ROTATION_REPLAY_TOLERANCE_RAD:
        raise ValueError(
            "control replay orientation does not match combined reference: "
            f"{max_rotation_error:.3e} > {ROTATION_REPLAY_TOLERANCE_RAD:.3e}"
        )
    deltas = np.linalg.norm(control_positions - combined_positions, axis=1)
    max_delta = float(np.max(deltas))
    if not np.isfinite(max_delta):
        raise ValueError("control replay position delta is non-finite")
    if max_delta > CONTROL_REPLAY_MAX_DELTA_M:
        raise ValueError(
            "control replay does not reproduce current best combined trajectory: "
            f"{max_delta:.3e} > {CONTROL_REPLAY_MAX_DELTA_M:.3e}"
        )
    return {
        "schema": "currentbest_control_replay_agreement_v1",
        "max_position_delta_m": max_delta,
        "p95_position_delta_m": float(np.percentile(deltas, 95)),
        "threshold_m": CONTROL_REPLAY_MAX_DELTA_M,
        "max_rotation_error_rad": max_rotation_error,
        "rotation_threshold_rad": ROTATION_REPLAY_TOLERANCE_RAD,
        "sample_count": int(len(control_times)),
    }


def load_refined_all_eye_candidates(
    record: dict[str, Any],
    baseline_candidate: dict[str, Any],
    reference_times: np.ndarray,
    stage_record: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[Path], dict[str, str]]:
    refined_paths = [Path(path) for path in stage_record["refined_left_sources"]]
    overrides = dict(stage_record["source_override_sha256"])
    left_candidates, left_report, left_paths = source_eval.load_left_candidates_from_reports(
        record,
        baseline_candidate,
        reference_times,
        refined_paths,
        refined_override_sha256=overrides,
    )
    right_candidates, right_report, right_paths = physical.load_eye_candidates(
        record,
        "right",
        baseline_candidate,
        reference_times,
    )
    return (
        [*left_candidates, *right_candidates],
        {
            "schema": "sift_lm_refined_left_original_right_eye_candidates_v1",
            "left": left_report,
            "right": right_report,
            "right_eye_unchanged_original_source": True,
            "left_source_override_sha256": overrides,
        },
        [*left_paths, *right_paths],
        overrides,
    )


def pair_candidates(candidates: list[dict[str, Any]]) -> dict[tuple[int, int], list[dict[str, Any]]]:
    mapped: dict[tuple[int, int], list[dict[str, Any]]] = {}
    seen: set[tuple[tuple[int, int], str]] = set()
    for candidate in candidates:
        pair = (
            int(candidate["reference_first_index"]),
            int(candidate["reference_second_index"]),
        )
        eye = str(candidate["eye"])
        key = (pair, eye)
        if key in seen:
            raise ValueError(f"duplicate same-eye candidate for pair {pair}: {eye}")
        seen.add(key)
        mapped.setdefault(pair, []).append(candidate)
    return mapped


def refresh_shared_row_confidences(
    original_rows: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Keep the frozen pair/timeline policy and refresh current winner confidence."""
    by_pair = pair_candidates(candidates)
    refreshed = []
    changed = 0
    for row in original_rows:
        if row.get("accepted") is not True:
            raise ValueError("shared stereo rows must all be accepted")
        pair = (int(row["first_index"]), int(row["second_index"]))
        pair_candidates_for_row = by_pair.get(pair)
        if not pair_candidates_for_row:
            raise ValueError(f"shared row pair missing from current candidates: {pair}")
        best_confidence = max(float(candidate["observation_confidence"]) for candidate in pair_candidates_for_row)
        copied = dict(row)
        if abs(float(copied.get("pnp_inlier_ratio")) - best_confidence) > 1e-12:
            changed += 1
        copied["pnp_inlier_ratio"] = best_confidence
        copied["confidence_source"] = "current_candidate_winner"
        refreshed.append(copied)
    extra_pairs = set(by_pair) - {
        (int(row["first_index"]), int(row["second_index"])) for row in original_rows
    }
    if extra_pairs:
        raise ValueError(f"current candidates contain pairs absent from frozen shared rows: {sorted(extra_pairs)[:3]}")
    return refreshed, {
        "schema": "current_winner_confidence_refresh_v1",
        "row_count": len(refreshed),
        "confidence_changed_row_count": changed,
        "pair_policy": "frozen_original_shared_pairs",
    }


def postprocess_candidate_metadata(
    variant_dir: Path,
    baseline_candidate: dict[str, Any],
    source_override_sha256: dict[str, str],
    combined_reference: Path,
    constant_artifact: Path,
    motion_factor_sha: str,
    arm_role: str,
    control_replay: dict[str, Any] | None,
) -> None:
    candidate_path = variant_dir / "candidate_manifest.json"
    graph_path = variant_dir / "graph_report.json"
    candidate = read_json(candidate_path)
    candidate["baseline_input_sha256_preserved"] = dict(baseline_candidate.get("input_sha256", {}))
    candidate["source_override_sha256"] = dict(source_override_sha256)
    candidate["current_best_context"] = {
        "source": "physical_stereo_constant_gauge_currentbest",
        "combined_reference_artifact": str(combined_reference.resolve()),
        "control_replay_agreement": control_replay,
        "arm_role": arm_role,
    }
    candidate["source_upgrade_scope"] = {
        "partial_source_upgrade": bool(source_override_sha256),
        "left_refined_sift_lm_reports": bool(source_override_sha256),
        "right_geometry_source": "original_right_reports_unchanged",
        "stale_right_derived_geometry_unchanged": bool(source_override_sha256),
    }
    candidate["learned_factor_context"] = {
        "source": "constant_ir_gauge_selected",
        "identical_between_arms": True,
        "path": str((constant_artifact / "local_motion_factors.json").resolve()),
        "sha256": motion_factor_sha,
    }
    write_json(candidate_path, candidate)
    graph = read_json(graph_path)
    graph["current_best_context"] = candidate["current_best_context"]
    graph["source_upgrade_scope"] = candidate["source_upgrade_scope"]
    graph["learned_factor_context"] = candidate["learned_factor_context"]
    write_json(graph_path, graph)


def run_arm(
    *,
    record: dict[str, Any],
    baseline_artifact: Path,
    variant: str,
    variant_dir: Path,
    state: Any,
    baseline_candidate: dict[str, Any],
    stereo_rows: list[dict[str, Any]],
    stereo_report: dict[str, Any],
    raw_paths: list[Path],
    constant_artifact: Path,
    constant_paths: list[Path],
    frozen_hashes: dict[str, str],
    combined_reference_artifact: Path,
    source_override_sha256: dict[str, str],
    arm_role: str,
    control_replay: dict[str, Any] | None,
) -> dict[str, Any]:
    motion_factors = read_json(constant_artifact / "local_motion_factors.json")
    motion_factor_sha = file_hash(constant_artifact / "local_motion_factors.json")
    original_score = physical.corpus.score_frozen
    score_context: dict[str, Any] = {}

    def score_with_final_metadata(*args, **kwargs):
        estimate = Path(args[1])
        replay = control_replay
        if variant == ORIGINAL_VARIANT:
            replay = validate_combined_replay(
                estimate,
                combined_reference_artifact / "body_trajectory_fused.csv",
            )
            score_context["control_replay"] = replay
        postprocess_candidate_metadata(
            variant_dir,
            baseline_candidate,
            source_override_sha256,
            combined_reference_artifact,
            constant_artifact,
            motion_factor_sha,
            arm_role,
            replay,
        )
        frozen_outputs = snapshot_paths([
            estimate,
            variant_dir / "candidate_manifest.json",
            variant_dir / "graph_report.json",
        ])
        score = original_score(*args, **kwargs)
        assert_hashes_unchanged(frozen_outputs)
        return score

    physical.corpus.score_frozen = score_with_final_metadata
    try:
        result = physical.run_solver_variant(
            record,
            baseline_artifact,
            variant,
            variant_dir,
            state,
            baseline_candidate,
            stereo_rows,
            stereo_report,
            raw_paths,
            motion_factors,
            "constant_ir_gauge_selected",
            [*constant_paths, combined_reference_artifact / "candidate_manifest.json",
             combined_reference_artifact / "graph_report.json",
             combined_reference_artifact / "body_trajectory_fused.csv"],
            frozen_hashes,
        )
    finally:
        physical.corpus.score_frozen = original_score
    if "control_replay" in score_context:
        result["control_replay_agreement"] = score_context["control_replay"]
    return result


def run_record(
    record: dict[str, Any],
    baseline: Path,
    constant_root: Path,
    combined_reference: Path,
    output: Path,
    source_stage: dict[str, Any],
    frozen_hashes: dict[str, str],
) -> dict[str, Any]:
    record_id = record["id"]
    baseline_artifact = baseline_artifact_dir(baseline, record_id)
    result = {"id": record_id, "status": "IN_PROGRESS", "variants": {}}
    try:
        stage_record = source_eval.validate_source_stage_record(record_id, source_stage)
        base.validate_record_sources(record)
        baseline_candidate, baseline_graph = base.validate_baseline_artifact(record, baseline_artifact)
        state = base.load_bound_reference(record)
        base.validate_baseline_trajectory_identity(baseline_artifact, state, baseline_graph)
        original_stereo = read_json(baseline_artifact / "shared_stereo_observations.json")
        original_candidates, original_eye_reports, original_paths = physical.load_all_eye_candidates(
            record,
            baseline_candidate,
            state.times,
        )
        original_rows, original_report = physical.transform_shared_rows(
            state,
            original_candidates,
            original_stereo,
        )
        original_report["eye_candidate_reports"] = original_eye_reports
        refined_candidates, refined_eye_reports, refined_paths, overrides = load_refined_all_eye_candidates(
            record,
            baseline_candidate,
            state.times,
            stage_record,
        )
        refreshed_stereo, confidence_refresh = refresh_shared_row_confidences(
            original_stereo,
            refined_candidates,
        )
        refined_rows, refined_report = physical.transform_shared_rows(
            state,
            refined_candidates,
            refreshed_stereo,
        )
        refined_report["eye_candidate_reports"] = refined_eye_reports
        refined_report["confidence_refresh"] = confidence_refresh
        constant_artifact, _constant_candidate, _constant_graph, constant_paths = physical.validate_constant_artifact(
            record,
            constant_root,
        )
        combined_artifact, combined_candidate, combined_paths = validate_combined_reference(
            record,
            baseline_artifact,
            combined_reference,
        )
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
            Path(record["session"]) / "d405_frames.csv",
            Path(record["session"]) / "external_imu/imu.bin",
            Path(record["vins_dir"]) / "vio_corrected_stream.csv",
            Path(record["vins_dir"]) / "run_acceptance.json",
            *original_paths,
            *refined_paths,
            *constant_paths,
            *combined_paths,
        ])
        result["reference_current_best"] = {
            "artifact_dir": str(combined_artifact.resolve()),
            "candidate_manifest_sha256": file_hash(combined_artifact / "candidate_manifest.json"),
            "graph_report_sha256": file_hash(combined_artifact / "graph_report.json"),
            "body_trajectory_fused_sha256": file_hash(combined_artifact / "body_trajectory_fused.csv"),
            "role": "best_context_comparator_not_source_selector",
            "schema": combined_candidate.get("schema"),
        }
        arms = {
            ORIGINAL_VARIANT: (original_rows, original_report, original_paths, {}, "currentbest_original_control"),
            REFINED_VARIANT: (refined_rows, refined_report, refined_paths, overrides, "refined_left_override_original_right"),
        }
    except base.StopCodeChanged:
        raise
    except Exception as error:
        for variant in (ORIGINAL_VARIANT, REFINED_VARIANT):
            variant_dir = output / record_id / variant
            variant_dir.mkdir(parents=True, exist_ok=True)
            result["variants"][variant] = physical.run_variant_failure(variant_dir, error)
        result["status"] = "INCOMPLETE_VARIANTS"
        return result

    control_rows, control_report, control_paths, _control_overrides, control_role = arms[ORIGINAL_VARIANT]
    control_dir = output / record_id / ORIGINAL_VARIANT
    control_dir.mkdir(parents=True)
    try:
        result["variants"][ORIGINAL_VARIANT] = run_arm(
            record=record,
            baseline_artifact=baseline_artifact,
            variant=ORIGINAL_VARIANT,
            variant_dir=control_dir,
            state=state,
            baseline_candidate=baseline_candidate,
            stereo_rows=control_rows,
            stereo_report=control_report,
            raw_paths=control_paths,
            constant_artifact=constant_artifact,
            constant_paths=[*constant_paths, *combined_paths],
            frozen_hashes=frozen_hashes,
            combined_reference_artifact=combined_artifact,
            source_override_sha256={},
            arm_role=control_role,
            control_replay=None,
        )
        control_replay = result["variants"][ORIGINAL_VARIANT].get("control_replay_agreement")
        if control_replay is None:
            raise ValueError("control replay agreement missing after original arm")
    except base.StopCodeChanged:
        raise
    except Exception as error:
        result["variants"][ORIGINAL_VARIANT] = physical.run_variant_failure(control_dir, error)
        refined_dir = output / record_id / REFINED_VARIANT
        refined_dir.mkdir(parents=True, exist_ok=True)
        result["variants"][REFINED_VARIANT] = {
            "artifact_dir": str(refined_dir.resolve()),
            "error_code": "CONTROL_REPLAY_FAILED",
            "stage": "control_replay",
            "error": f"{type(error).__name__}: {error}",
        }
        result["status"] = "INCOMPLETE_VARIANTS"
        return result

    refined_rows_for_arm, refined_report_for_arm, refined_paths_for_arm, refined_overrides, refined_role = arms[REFINED_VARIANT]
    refined_dir = output / record_id / REFINED_VARIANT
    refined_dir.mkdir(parents=True)
    try:
        result["variants"][REFINED_VARIANT] = run_arm(
            record=record,
            baseline_artifact=baseline_artifact,
            variant=REFINED_VARIANT,
            variant_dir=refined_dir,
            state=state,
            baseline_candidate=baseline_candidate,
            stereo_rows=refined_rows_for_arm,
            stereo_report=refined_report_for_arm,
            raw_paths=refined_paths_for_arm,
            constant_artifact=constant_artifact,
            constant_paths=[*constant_paths, *combined_paths],
            frozen_hashes=frozen_hashes,
            combined_reference_artifact=combined_artifact,
            source_override_sha256=refined_overrides,
            arm_role=refined_role,
            control_replay=control_replay,
        )
    except base.StopCodeChanged:
        raise
    except Exception as error:
        result["variants"][REFINED_VARIANT] = physical.run_variant_failure(refined_dir, error)
    assert_hashes_unchanged(consumed_before)
    result["status"] = (
        "COMPLETED"
        if all("score" in variant for variant in result["variants"].values())
        else "INCOMPLETE_VARIANTS"
    )
    return result


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    output = {}
    for variant in (ORIGINAL_VARIANT, REFINED_VARIANT):
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--constant-gauge", type=Path, required=True)
    parser.add_argument("--combined-reference", type=Path, required=True)
    parser.add_argument("--source-stage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", action="append", default=[])
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink():
        parser.error("output must be new; previous results are never overwritten")
    manifest = read_json(args.manifest)
    records = base.validate_records(manifest, args.dataset)
    baseline_summary = read_json(args.baseline / "summary.json")
    baseline_by_id = base.baseline_results(baseline_summary)
    missing = {record["id"] for record in records} - set(baseline_by_id)
    if missing:
        raise ValueError(f"baseline summary is missing records: {sorted(missing)}")
    source_stage = load_source_stage(args.source_stage)
    frozen_hashes = base.snapshot_hashes(frozen_code_paths(args.source_stage))
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
        "manifest_sha256": file_hash(args.manifest),
        "baseline_summary_sha256": file_hash(args.baseline / "summary.json"),
        "source_stage_preflight_sha256": source_stage["sha256"],
        "baseline": str(args.baseline.resolve()),
        "constant_gauge": str(args.constant_gauge.resolve()),
        "combined_reference": str(args.combined_reference.resolve()),
        "source_stage": str(args.source_stage.resolve()),
        "variants": [ORIGINAL_VARIANT, REFINED_VARIANT],
        "control_replay_required_max_delta_m": CONTROL_REPLAY_MAX_DELTA_M,
        "code_sha256": frozen_hashes,
        "results": [],
    }
    write_json(args.output / "summary.json", summary)
    for index, record in enumerate(records, 1):
        if base.code_changed(frozen_hashes):
            summary["status"] = "STOP_CODE_CHANGED"
            write_json(args.output / "summary.json", summary)
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
                    args.output,
                    source_stage,
                    frozen_hashes,
                )
            except base.StopCodeChanged:
                summary["status"] = "STOP_CODE_CHANGED"
                write_json(args.output / "summary.json", summary)
                return 2
        result["elapsed_s"] = time.monotonic() - started
        summary["results"].append(result)
        summary["completed_count"] = index
        summary["aggregates"] = aggregate(summary["results"])
        write_json(args.output / "summary.json", summary)
    summary["status"] = (
        "COMPLETED"
        if all(result.get("status") == "COMPLETED" for result in summary["results"])
        else "COMPLETED_WITH_FAILURES"
    )
    write_json(args.output / "summary.json", summary)
    after_hashes = base.snapshot_hashes(frozen_code_paths(args.source_stage))
    if after_hashes != frozen_hashes:
        summary["status"] = "STOP_CODE_CHANGED"
        summary["code_sha256_after"] = after_hashes
        write_json(args.output / "summary.json", summary)
        return 2
    return 0 if summary["status"] == "COMPLETED" else 3


if __name__ == "__main__":
    raise SystemExit(main())
