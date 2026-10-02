#!/usr/bin/env python3
"""Development-only symmetric IR recovery appendix evaluation.

Reuse the frozen paired evaluator, but admit separately proven native rows from
formerly rejected observations in normally merged PASS reports. Source reports,
global scales, learned factors, the backend, and output timelines stay unchanged.
"""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import sys
from typing import Any
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import evaluate_sift_lm_dual_combined_probe as paired  # noqa: E402
from ego_vio.vio.recovered_stereo_pairs import build_recovered_shared_rows  # noqa: E402


APPENDIX_SCHEMA = "umi_independent_ir_recovery_appendix_v1"
REFINED_VARIANT = "currentbest_independent_ir_recovered_pairs"


def validate_recovery_appendix(
    record: dict[str, Any],
    stage_record: dict[str, Any],
    baseline_candidate: dict[str, Any],
    reference_times: np.ndarray,
    eye_reports: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[Path]]:
    """Bind recovery evidence to immutable source failures and accepted reports."""
    path = Path(stage_record["recovery_appendix_path"])
    if paired.file_hash(path) != stage_record["recovery_appendix_sha256"]:
        raise ValueError("recovery appendix hash mismatch")
    appendix = paired.read_json(path)
    paired.base.require_onboard_report(appendix, "IR recovery appendix")
    if appendix.get("schema") != APPENDIX_SCHEMA or appendix.get("id") != record["id"]:
        raise ValueError("recovery appendix schema/record mismatch")
    if Path(appendix.get("session", "")).resolve() != Path(record["session"]).resolve():
        raise ValueError("recovery appendix session mismatch")
    guard = appendix.get("consumed_source_guard", {})
    guarded = guard.get("guarded_before_sha256")
    if guard.get("guarded_after_verified") is not True or not isinstance(guarded, dict) or not guarded:
        raise ValueError("recovery consumed source guard missing")
    guarded_paths = {Path(binding["path"]).resolve(): binding["sha256"] for binding in guarded.values()}
    consumed = [path]
    for binding in guarded.values():
        source_path = Path(binding["path"])
        if paired.file_hash(source_path) != binding["sha256"]:
            raise ValueError("recovery consumed source guard hash changed")
        consumed.append(source_path)
    parent = Path(appendix["source_stage_preflight"])
    if paired.file_hash(parent) != appendix["source_stage_preflight_sha256"]:
        raise ValueError("recovery parent stage hash mismatch")
    parent_stage = paired.load_source_stage(parent.parent)
    parent_row = paired.source_eval.validate_source_stage_record(record["id"], parent_stage)
    for key in ("refined_left_sources", "refined_right_sources", "source_override_sha256",
                "independent_right_geometry_refresh"):
        if stage_record.get(key) != parent_row.get(key):
            raise ValueError(f"recovery changed frozen stage sources: {key}")

    contexts = appendix["eye_contexts"]
    if set(contexts) != {"left", "right"}:
        raise ValueError("recovery eye contexts must be symmetric")
    consumed.append(parent)
    source_reports = {}
    trajectory_times = {}
    metric_times = {}
    metadata = {}
    for eye in ("left", "right"):
        context, merged = contexts[eye], eye_reports[eye]
        scale = float(context["reference_scale"])
        if not np.isfinite(scale) or scale <= 0 or scale != float(merged["scale_m_per_mast3r_unit"]):
            raise ValueError("recovery changed frozen reference scale")
        admitted = {str(Path(x).resolve()) for x in merged["merged_report_paths"]}
        declared = {str(Path(x).resolve()) for x in context["merged_report_paths"]}
        if declared != admitted:
            raise ValueError("recovery report admission does not match normal merge")
        trajectory = Path(context["reference_trajectory_path"]).resolve()
        if guarded_paths.get(trajectory) != context["reference_trajectory_sha256"]:
            raise ValueError("recovery raw trajectory missing from consumed source guard")
        if paired.file_hash(trajectory) != context["reference_trajectory_sha256"]:
            raise ValueError("recovery raw trajectory hash mismatch")
        trajectory_times[eye] = paired.fusion.load_trajectory(trajectory)[0]
        metric_trajectory = paired.physical.eye_trajectory_path_from_baseline(baseline_candidate, eye)
        paired.source_eval.validate_candidate_hashes(baseline_candidate, [metric_trajectory])
        metric_times[eye] = paired.fusion.load_trajectory(metric_trajectory)[0]
        if not np.array_equal(trajectory_times[eye], metric_times[eye]):
            raise ValueError("recovery raw/metric trajectory timeline mismatch")
        metadata[eye] = paired.physical.validate_eye_metadata(baseline_candidate, eye)
        consumed.extend([trajectory, metric_trajectory])
        for source, digest in context["report_sha256"].items():
            source_path = Path(source)
            if paired.file_hash(source_path) != digest:
                raise ValueError("recovery source report hash mismatch")
            source_reports[(eye, str(source_path.resolve()))] = paired.read_json(source_path)
            consumed.append(source_path)
        if not admitted <= {key[1] for key in source_reports if key[0] == eye}:
            raise ValueError("recovery accepted report missing source hash")
        for source_path in admitted:
            declared_trajectory = source_reports[(eye, source_path)].get("trajectory")
            if not declared_trajectory or Path(declared_trajectory).resolve() != trajectory:
                raise ValueError("recovery source report trajectory mismatch")

    candidates = []
    rejected = 0
    skipped = {}
    seen = set()
    for entry in appendix["observations"]:
        eye = entry["eye"]
        if eye not in contexts:
            raise ValueError("invalid recovery eye")
        source_path = str(Path(entry["source_report_path"]).resolve())
        context = contexts[eye]
        if source_path not in {str(Path(x).resolve()) for x in context["merged_report_paths"]}:
            raise ValueError("recovery from rejected optional report")
        if entry["source_report_sha256"] != context["report_sha256"].get(source_path):
            raise ValueError("recovery entry source hash mismatch")
        source = source_reports[(eye, source_path)]
        if source.get("result") != "PASS" or entry.get("source_report_result") != "PASS":
            raise ValueError("recovery source report must remain PASS")
        index = entry["source_observation_index"]
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(source["observations"]):
            raise ValueError("recovery source row index invalid")
        identity = (eye, source_path, index)
        if identity in seen:
            raise ValueError("duplicate recovery source row")
        seen.add(identity)
        original = source["observations"][index]
        if original != entry["original_observation"] or original.get("accepted") is not False:
            raise ValueError("recovery must preserve original rejected row")
        if original.get("reason") == "translation_excitation_low":
            raise ValueError("recovery cannot admit original low-excitation row")
        native = entry["native_observation"]
        for key in ("first_index", "second_index", "first_t_sec", "second_t_sec"):
            if native.get(key) != original.get(key):
                raise ValueError("recovery native row changed endpoints/timestamps")
        paired.physical.validate_raw_observation_source(native, trajectory_times[eye])
        if native.get("accepted") is not True:
            rejected += 1
            continue
        if (entry["raw_forward_summary"].get("accepted") is not True
                or entry["raw_reverse_summary"].get("accepted") is not True):
            raise ValueError("recovery native acceptance lacks bidirectional support")
        vector = np.asarray(native.get("metric_displacement_camera_i_m"), dtype=float)
        quaternion = np.asarray(native.get("pnp_rotation_quaternion_xyzw"), dtype=float)
        if (native.get("metric_displacement_frame") != f"infrared_{eye}_camera_i"
                or vector.shape != (3,) or quaternion.shape != (4,)
                or not np.all(np.isfinite(vector)) or not np.all(np.isfinite(quaternion))
                or not np.isclose(np.linalg.norm(quaternion), 1.0, atol=1e-6)):
            raise ValueError("recovery native measurement invalid")
        candidate, reason = paired.physical.reference_bound_eye_candidate(
            reference_times, metric_times[eye], eye, native,
            paired.fusion.stereo_observation_confidence(native, float(context["reference_scale"])),
            metadata[eye]["effective_body_T_camera"],
        )
        if reason is not None:
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        candidate["recovery_source_report_path"] = source_path
        candidate["recovery_source_observation_index"] = index
        candidates.append(candidate)
    return candidates, {
        "schema": "independent_ir_recovery_appendix_validation_v1",
        "source_reports_unchanged": True,
        "reference_scales_unchanged": True,
        "raw_metric_timelines_equal": True,
        "native_rejected_rows_retained": rejected,
        "recovered_eye_candidate_count": len(candidates),
        "skipped_candidates": skipped,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "appendix_path": str(path.resolve()),
        "appendix_sha256": paired.file_hash(path),
    }, list(dict.fromkeys(consumed))


def main(argv: list[str] | None = None) -> int:
    """Install reversible, process-local adapters; never change frozen scripts."""
    original_load = paired.load_refined_all_eye_candidates
    original_run = paired.run_record
    original_scope = paired.source_upgrade_scope
    original_paths = paired.frozen_code_paths
    context = {}

    def load(record, candidate, times, stage_record):
        existing, reports, paths, overrides = original_load(record, candidate, times, stage_record)
        recovered, proof, recovery_paths = validate_recovery_appendix(record, stage_record, candidate, times, reports)
        context.update(times=times, existing=existing, recovered=recovered)
        _templates, all_candidates, _diagnostic = build_recovered_shared_rows(
            times, context["original_rows"], existing, recovered,
        )
        reports["recovery_appendix"] = proof
        return all_candidates, reports, [*paths, *recovery_paths], overrides

    def refresh(rows, candidates):
        templates, all_candidates, diagnostic = build_recovered_shared_rows(
            context["times"], rows, context["existing"], context["recovered"],
        )
        if all_candidates != candidates:
            raise ValueError("recovery candidate deduplication changed across adapters")
        return templates, diagnostic

    def run(record, baseline, *args, **kwargs):
        context.clear()
        context["original_rows"] = paired.read_json(
            paired.baseline_artifact_dir(baseline, record["id"]) / "shared_stereo_observations.json"
        )
        return original_run(record, baseline, *args, **kwargs)

    def scope(overrides, role):
        result = original_scope(overrides, role)
        if overrides:
            result.update(
                symmetric_native_recovery_appendix=True,
                original_source_reports_unchanged=True,
                optional_failed_reports_salvaged=False,
                fixed_pair_geometry_refresh_only=False,
                shared_factor_policy="union of frozen and proven recovered physical pairs; one factor per pair",
            )
        return result

    def paths(source_stage):
        return [*original_paths(source_stage), Path(__file__),
                ROOT / "ego_vio/vio/recovered_stereo_pairs.py",
                ROOT / "scripts/prepare_independent_ir_recovery_probe.py"]

    with ExitStack() as stack:
        for name, value in {
            "load_refined_all_eye_candidates": load,
            "refresh_shared_row_confidences": refresh,
            "run_record": run,
            "source_upgrade_scope": scope,
            "frozen_code_paths": paths,
            "SCHEMA": "umi_independent_ir_recovery_paired_probe_v1",
            "REFINED_VARIANT": REFINED_VARIANT,
        }.items():
            stack.enter_context(patch.object(paired, name, value))
        return paired.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
