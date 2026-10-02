#!/usr/bin/env python3
"""Evaluate refined SIFT-LM LEFT stereo sources through the physical backend.

This development runner consumes the source-only output of
``run_sift_lm_physical_probe.py --run``.  It compares two matched LEFT-only
measurement arms:

* original unrefined LEFT raw reports
* refined LEFT source override reports

The VINS state, raw adapter-v2 learned factors, backend, scorer, timestamps,
and source record set are otherwise unchanged.  RIGHT reports are not treated
as independent evidence in this first pass.
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
import run_dual_ir_regression_corpus as corpus  # noqa: E402
import run_learned_segment_probe as base  # noqa: E402
import run_physical_stereo_lever_probe as physical  # noqa: E402


SCHEMA = "umi_sift_lm_physical_source_probe_v1"
BASELINE_POLICY = "both"
ORIGINAL_VARIANT = "left_unrefined_matched_control"
REFINED_VARIANT = "left_sift_lm_refined_matched"
OPTIONAL_STEREO_POLICY = physical.OPTIONAL_STEREO_POLICY


def read_json(path: Path) -> Any:
    return base.read_json(path)


def write_json(path: Path, value: Any) -> None:
    base.write_json(path, value)


def file_hash(path: Path) -> str:
    return base.file_hash(path)


def baseline_artifact_dir(baseline: Path, record_id: str) -> Path:
    return baseline / record_id / BASELINE_POLICY


def frozen_code_paths(source_stage: Path) -> list[Path]:
    return [
        *physical.frozen_code_paths(),
        ROOT / "scripts/run_sift_lm_physical_probe.py",
        Path(__file__),
        source_stage / "preflight_report.json",
    ]


def load_source_stage(source_stage: Path) -> dict[str, Any]:
    report_path = source_stage / "preflight_report.json"
    report = read_json(report_path)
    if report.get("external_ground_truth_used") is not False:
        raise ValueError("source stage used ground truth")
    if report.get("slam_supervision") is not False:
        raise ValueError("source stage used SLAM supervision")
    refined = report.get("refined_sources")
    if not isinstance(refined, list):
        raise ValueError("source stage refined_sources missing")
    by_id: dict[str, dict[str, Any]] = {}
    for row in refined:
        record_id = row.get("id")
        if not record_id or record_id in by_id:
            raise ValueError("source stage refined_sources has missing/duplicate id")
        override = row.get("source_override_sha256")
        paths = row.get("refined_left_sources")
        if not isinstance(override, dict) or not override:
            raise ValueError(f"source stage {record_id} missing source_override_sha256")
        if not isinstance(paths, list) or not paths:
            raise ValueError(f"source stage {record_id} missing refined_left_sources")
        for source in paths:
            resolved = str(Path(source).resolve())
            if resolved not in override:
                raise ValueError(f"refined LEFT path lacks override hash: {resolved}")
            if file_hash(Path(resolved)) != override[resolved]:
                raise ValueError(f"refined LEFT override hash changed: {resolved}")
        by_id[str(record_id)] = row
    return {
        "path": str(report_path.resolve()),
        "sha256": file_hash(report_path),
        "raw": report,
        "refined_by_id": by_id,
    }


def validate_source_stage_record(
    record_id: str,
    source_stage: dict[str, Any],
) -> dict[str, Any]:
    refined = source_stage["refined_by_id"].get(record_id)
    if refined is None:
        raise ValueError(f"source stage has no refined LEFT sources for {record_id}")
    return refined


def validate_candidate_hashes(candidate: dict[str, Any], paths: list[Path]) -> dict[str, str]:
    hashes = {}
    bound = candidate.get("input_sha256")
    if not isinstance(bound, dict) or not bound:
        raise ValueError("baseline candidate input_sha256 missing")
    for path in paths:
        resolved = str(path.resolve())
        if resolved not in bound:
            raise ValueError(f"baseline candidate does not bind source: {resolved}")
        digest = file_hash(path)
        if digest != bound[resolved]:
            raise ValueError(f"baseline-bound source changed: {resolved}")
        hashes[resolved] = digest
    return hashes


def load_left_candidates_from_reports(
    record: dict[str, Any],
    baseline_candidate: dict[str, Any],
    reference_times: np.ndarray,
    report_paths: list[Path],
    *,
    refined_override_sha256: dict[str, str] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[Path]]:
    if len(report_paths) != len(physical.eye_report_names("left")):
        raise ValueError("expected the four LEFT stereo reports")
    trajectory_path = physical.eye_trajectory_path_from_baseline(baseline_candidate, "left")
    validate_candidate_hashes(baseline_candidate, [trajectory_path])
    if refined_override_sha256 is None:
        validate_candidate_hashes(baseline_candidate, report_paths)
    else:
        for path in report_paths:
            resolved = str(path.resolve())
            if resolved not in refined_override_sha256:
                raise ValueError(f"refined report missing source override hash: {resolved}")
            if file_hash(path) != refined_override_sha256[resolved]:
                raise ValueError(f"refined report source override hash changed: {resolved}")
    trajectory_times, _positions, _rotations, _rows = fusion.load_trajectory(trajectory_path)
    reports = []
    for index, path in enumerate(report_paths):
        report = fusion.load_json_report(path)
        if index == 0:
            fusion.validate_onboard_report(report, path, "umi_mast3r_stereo_scale_v2")
        if Path(report.get("session", "")).resolve() != Path(record["session"]).resolve():
            raise ValueError(f"LEFT report session mismatch: {path}")
        if report.get("observation_frame") != "infrared_left_camera_i":
            raise ValueError(f"LEFT report frame mismatch: {path}")
        report["report_path"] = str(path.resolve())
        reports.append(report)
    merged = fusion.merge_stereo_reports(
        reports[0],
        reports[1:],
        optional_policy=OPTIONAL_STEREO_POLICY,
    )
    metadata = physical.validate_eye_metadata(baseline_candidate, "left")
    reference_scale = float(merged["scale_m_per_mast3r_unit"])
    candidates = []
    skipped: dict[str, int] = {}
    accepted_raw = 0
    rejected_raw = 0
    for observation in merged.get("observations", []):
        if not observation.get("accepted", False):
            rejected_raw += 1
            continue
        accepted_raw += 1
        candidate, reason = physical.reference_bound_eye_candidate(
            reference_times,
            trajectory_times,
            "left",
            observation,
            fusion.stereo_observation_confidence(observation, reference_scale),
            metadata["effective_body_T_camera"],
        )
        if reason is None:
            candidates.append(candidate)
        else:
            skipped[reason] = skipped.get(reason, 0) + 1
    physical.validate_no_same_eye_reference_duplicates(candidates)
    report = {
        "schema": "sift_lm_left_eye_candidates_v1",
        "eye": "left",
        "candidate_count": len(candidates),
        "accepted_raw_observation_count": accepted_raw,
        "rejected_raw_observation_count": rejected_raw,
        "skipped_candidate_counts": skipped,
        "merged_report_paths": merged.get("merged_report_paths", []),
        "merged_report_count": merged.get("merged_report_count"),
        "optional_stereo_policy": OPTIONAL_STEREO_POLICY,
        "optional_report_rejections": merged.get("optional_report_rejections", []),
        "metric_trajectory": str(trajectory_path.resolve()),
        "scale_m_per_mast3r_unit": reference_scale,
        "effective_body_T_camera": metadata["effective_body_T_camera"],
        "measurement_frame": "body_i",
        "right_eye_used_as_independent_evidence": False,
    }
    return candidates, report, [trajectory_path, *report_paths]


def candidate_by_pair(candidates: list[dict[str, Any]]) -> dict[tuple[int, int], dict[str, Any]]:
    mapped: dict[tuple[int, int], dict[str, Any]] = {}
    for candidate in candidates:
        if candidate.get("eye") != "left":
            raise ValueError("SIFT-LM source probe accepts LEFT candidates only")
        pair = (
            int(candidate["reference_first_index"]),
            int(candidate["reference_second_index"]),
        )
        if pair in mapped:
            raise ValueError(f"duplicate LEFT reference pair: {pair}")
        mapped[pair] = candidate
    return mapped


def matched_body_rows_from_candidates(
    reference_times: np.ndarray,
    candidates: list[dict[str, Any]],
    matched_pairs: list[tuple[int, int]],
) -> list[dict[str, Any]]:
    by_pair = candidate_by_pair(candidates)
    rows = []
    for first, second in matched_pairs:
        candidate = by_pair[(first, second)]
        rows.append(
            {
                "accepted": True,
                "first_index": first,
                "second_index": second,
                "first_t_sec": float(reference_times[first]),
                "second_t_sec": float(reference_times[second]),
                "metric_displacement_camera_i_m": [0.0, 0.0, 0.0],
                "metric_displacement_frame": "body_i",
                "scale": 1.0,
                "pnp_inlier_ratio": float(candidate["observation_confidence"]),
                "rotation_error_deg": 0.0,
                "source_pair_policy": "left_only_matched_reference_pair",
            }
        )
    return rows


def build_matched_left_measurements(
    state: Any,
    original_candidates: list[dict[str, Any]],
    refined_candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    original_pairs = set(candidate_by_pair(original_candidates))
    refined_pairs = set(candidate_by_pair(refined_candidates))
    matched_pairs = sorted(original_pairs & refined_pairs)
    if not matched_pairs:
        raise ValueError("no matched LEFT reference pairs between original/refined sources")
    original_rows = matched_body_rows_from_candidates(
        state.times,
        original_candidates,
        matched_pairs,
    )
    refined_rows = matched_body_rows_from_candidates(
        state.times,
        refined_candidates,
        matched_pairs,
    )
    original_transformed, original_report = physical.transform_shared_rows(
        state,
        original_candidates,
        original_rows,
    )
    refined_transformed, refined_report = physical.transform_shared_rows(
        state,
        refined_candidates,
        refined_rows,
    )
    return original_transformed, refined_transformed, {
        "schema": "sift_lm_left_matched_measurement_set_v1",
        "matched_pair_count": len(matched_pairs),
        "original_left_pair_count": len(original_pairs),
        "refined_left_pair_count": len(refined_pairs),
        "dropped_original_only_pair_count": len(original_pairs - refined_pairs),
        "dropped_refined_only_pair_count": len(refined_pairs - original_pairs),
        "measurement_frame": "body_i",
        "right_eye_used_as_independent_evidence": False,
        "original_transform": original_report,
        "refined_transform": refined_report,
    }


def provenance_hashes(
    baseline_candidate: dict[str, Any],
    artifact: Path,
    source_stage: dict[str, Any],
    source_override_sha256: dict[str, str],
    extra_paths: list[Path],
) -> dict[str, str]:
    hashes = dict(baseline_candidate.get("input_sha256", {}))
    for path in (
        artifact / "candidate_manifest.json",
        artifact / "graph_report.json",
        artifact / "local_motion_factors.json",
        artifact / "shared_stereo_observations.json",
        artifact / "body_trajectory_fused.csv",
        Path(source_stage["path"]),
        Path(__file__),
        *extra_paths,
    ):
        hashes[str(Path(path).resolve())] = file_hash(Path(path))
    for source, digest in source_override_sha256.items():
        hashes[str(Path(source).resolve())] = digest
    return hashes


def run_solver_variant(
    record: dict[str, Any],
    artifact: Path,
    variant: str,
    variant_dir: Path,
    state: Any,
    baseline_candidate: dict[str, Any],
    stereo_rows: list[dict[str, Any]],
    measurement_report: dict[str, Any],
    source_stage: dict[str, Any],
    source_override_sha256: dict[str, str],
    extra_paths: list[Path],
    frozen_hashes: dict[str, str],
) -> dict[str, Any]:
    motion_factors = read_json(artifact / "local_motion_factors.json")
    motion_factor_sha = file_hash(artifact / "local_motion_factors.json")
    write_json(variant_dir / "local_motion_factors.json", motion_factors)
    write_json(variant_dir / "shared_stereo_observations.json", stereo_rows)
    write_json(variant_dir / "measurement_report.json", measurement_report)
    refined, graph_solver = fusion.refine_positions_visual_inertial(
        state.positions,
        state.rotations,
        stereo_rows,
        state.mono,
        state.imu_times,
        state.gyro,
        state.accel,
        np.eye(4),
        state.config["td_s"],
        node_stride=1,
        max_correction_m=None,
        relative_motion_positions_body=state.positions,
        relative_motion_valid=np.ones(len(state.times), dtype=bool),
        secondary_visual_factors=motion_factors,
        use_visual_position_prior=False,
        solve_metric_scale=False,
        correction_cap_mode="global",
        stereo_factor_confidences=np.asarray(
            [float(row["pnp_inlier_ratio"]) for row in stereo_rows]
        ),
    )
    graph_solver = dict(graph_solver)
    graph_solver["sift_lm_physical_source_measurement"] = measurement_report
    estimate = variant_dir / "body_trajectory_fused.csv"
    fusion.write_trajectory(estimate, state.rows, refined, state.rotations)
    estimate_sha = file_hash(estimate)
    candidate = {
        "schema": "umi_sift_lm_physical_source_candidate_v1",
        "status": "EXPERIMENTAL_NOT_ACCEPTED",
        "accepted": False,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "session": str(Path(record["session"]).resolve()),
        "source_baseline_artifact": str(artifact.resolve()),
        "source_candidate_manifest": str((artifact / "candidate_manifest.json").resolve()),
        "input_sha256": provenance_hashes(
            baseline_candidate,
            artifact,
            source_stage,
            source_override_sha256,
            extra_paths,
        ),
        "baseline_input_sha256_preserved": dict(baseline_candidate.get("input_sha256", {})),
        "source_override_sha256": dict(source_override_sha256),
        "output_estimate_sha256": estimate_sha,
        "bound_samples": len(state.rows),
        "baseline_policy_arguments": baseline_candidate["policy_arguments"],
        "learned_factor_context": {
            "source": "raw_adapter_v2_local_motion_factors",
            "identical_between_arms": True,
            "path": str((artifact / "local_motion_factors.json").resolve()),
            "sha256": motion_factor_sha,
            "factor_count": len(motion_factors),
        },
        "policy_arguments": {
            "source_policy": BASELINE_POLICY,
            "variant": variant,
            "measurement_source": "left_only_matched_original_vs_sift_lm_refined",
            "right_eye_used_as_independent_evidence": False,
            "physical_stereo_transform": "body_lever_from_left_raw_reports",
            **base.BASELINE_POLICY_ARGUMENTS,
        },
        "measurement_report": measurement_report,
        "vins_source_validation": state.source_quality,
        "reference_time_binding": state.binding,
        "imu": state.imu_info,
    }
    write_json(variant_dir / "candidate_manifest.json", candidate)
    graph_report = {
        "schema": "umi_sift_lm_physical_source_graph_diagnostic_v1",
        "status": "EXPERIMENTAL_NOT_ACCEPTED",
        "accepted": False,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "output_frame": "body_imu_origin",
        "output_samples": len(state.rows),
        "td_s": state.config["td_s"],
        "policy_arguments": candidate["policy_arguments"],
        "source_graph_report": str((artifact / "graph_report.json").resolve()),
        "joint_position_solver": graph_solver,
    }
    write_json(variant_dir / "graph_report.json", graph_report)
    if base.code_changed(frozen_hashes):
        raise base.StopCodeChanged("STOP_CODE_CHANGED before scoring")
    if file_hash(estimate) != estimate_sha:
        raise RuntimeError("estimate changed before scoring")
    score = corpus.score_frozen(
        record,
        estimate,
        variant_dir / "score",
        variant_dir.parent,
        f"score_{record['id']}_{variant}",
    )
    if base.code_changed(frozen_hashes):
        raise base.StopCodeChanged("STOP_CODE_CHANGED after scoring")
    if file_hash(estimate) != estimate_sha:
        raise RuntimeError("estimate changed during scoring")
    return {
        "artifact_dir": str(variant_dir.resolve()),
        "score": score,
        "estimate_sha256": estimate_sha,
        "matched_pair_count": measurement_report.get("matched_pair_count"),
    }


def run_variant_failure(variant_dir: Path, error: Exception) -> dict[str, Any]:
    return {
        "artifact_dir": str(variant_dir.resolve()),
        "error_code": "VARIANT_FAILED",
        "stage": "run_record",
        "error": f"{type(error).__name__}: {error}",
    }


def run_record(
    record: dict[str, Any],
    baseline: Path,
    output: Path,
    source_stage: dict[str, Any],
    frozen_hashes: dict[str, str],
) -> dict[str, Any]:
    record_id = record["id"]
    artifact = baseline_artifact_dir(baseline, record_id)
    result = {
        "id": record_id,
        "status": "IN_PROGRESS",
        "reference_raw_baseline": {},
        "variants": {},
    }
    try:
        stage_record = validate_source_stage_record(record_id, source_stage)
        base.validate_record_sources(record)
        baseline_candidate, baseline_graph = base.validate_baseline_artifact(record, artifact)
        state = base.load_bound_reference(record)
        base.validate_baseline_trajectory_identity(artifact, state, baseline_graph)
        original_paths = physical.eye_report_paths_from_baseline(baseline_candidate, "left")
        refined_paths = [Path(path) for path in stage_record["refined_left_sources"]]
        original_candidates, original_report, original_bound_paths = load_left_candidates_from_reports(
            record,
            baseline_candidate,
            state.times,
            original_paths,
            refined_override_sha256=None,
        )
        refined_candidates, refined_report, refined_bound_paths = load_left_candidates_from_reports(
            record,
            baseline_candidate,
            state.times,
            refined_paths,
            refined_override_sha256=stage_record["source_override_sha256"],
        )
        original_rows, refined_rows, matched_report = build_matched_left_measurements(
            state,
            original_candidates,
            refined_candidates,
        )
        result["reference_raw_baseline"] = {
            "artifact_dir": str(artifact.resolve()),
            "candidate_manifest_sha256": file_hash(artifact / "candidate_manifest.json"),
            "graph_report_sha256": file_hash(artifact / "graph_report.json"),
            "body_trajectory_fused_sha256": file_hash(artifact / "body_trajectory_fused.csv"),
            "local_motion_factors_sha256": file_hash(artifact / "local_motion_factors.json"),
            "role": "reference_only_raw_adapter_v2_not_combined_source_arm",
        }
        original_measurement = {
            **matched_report,
            "variant": ORIGINAL_VARIANT,
            "left_eye_candidate_report": original_report,
            "source_override_sha256": {},
        }
        refined_measurement = {
            **matched_report,
            "variant": REFINED_VARIANT,
            "left_eye_candidate_report": refined_report,
            "source_override_sha256": dict(stage_record["source_override_sha256"]),
        }
        variant_inputs = {
            ORIGINAL_VARIANT: (original_rows, original_measurement, {}, original_bound_paths),
            REFINED_VARIANT: (
                refined_rows,
                refined_measurement,
                stage_record["source_override_sha256"],
                refined_bound_paths,
            ),
        }
    except base.StopCodeChanged:
        raise
    except Exception as error:
        for variant in (ORIGINAL_VARIANT, REFINED_VARIANT):
            variant_dir = output / record_id / variant
            variant_dir.mkdir(parents=True, exist_ok=True)
            result["variants"][variant] = run_variant_failure(variant_dir, error)
        result["status"] = "INCOMPLETE_VARIANTS"
        return result

    for variant, (rows, report, overrides, paths) in variant_inputs.items():
        variant_dir = output / record_id / variant
        variant_dir.mkdir(parents=True)
        try:
            result["variants"][variant] = run_solver_variant(
                record,
                artifact,
                variant,
                variant_dir,
                state,
                baseline_candidate,
                rows,
                report,
                source_stage,
                overrides,
                paths,
                frozen_hashes,
            )
        except base.StopCodeChanged:
            raise
        except Exception as error:
            result["variants"][variant] = run_variant_failure(variant_dir, error)
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
        "source_stage": str(args.source_stage.resolve()),
        "variants": [ORIGINAL_VARIANT, REFINED_VARIANT],
        "reference_raw_baseline_policy": "reference_only_raw_adapter_v2_not_combined_source_arm",
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
    return 0 if summary["status"] == "COMPLETED" else 3


if __name__ == "__main__":
    raise SystemExit(main())
