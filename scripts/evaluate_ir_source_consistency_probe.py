#!/usr/bin/env python3
"""Development-only native-source consistency replay; no parameter sweep.

Reuse symmetric recovery stereo measurements, but rebuild existing learned
factor confidence metadata from their refreshed per-eye measurements. Original
track poses/scales and constant-gauge targets are immutable. No recovered new
pair becomes a learned factor. Missing refreshed measurements explicitly retain
the already hash-bound old observation; they are not silently removed.
"""

from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import sys
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import evaluate_independent_ir_recovery_probe as recovery  # noqa: E402
import run_constant_ir_gauge_probe as gauge  # noqa: E402
from ego_vio.vio.learned_source_consistency import (  # noqa: E402
    sync_existing_factor_source_metadata,
)

paired = recovery.paired
VARIANT = "currentbest_ir_recovered_source_consistent"


def refreshed_existing_tracks(tracks, candidates):
    """Only replace existing accepted raw-pair observations, never append."""
    mapped = {}
    for candidate in candidates:
        key = (candidate["eye"], candidate["first_index"], candidate["second_index"])
        if key in mapped:
            raise ValueError("duplicate refreshed raw eye/pair")
        mapped[key] = candidate
    result = deepcopy(tracks)
    refreshed, fallback = 0, []
    for eye, track in result.items():
        for index, observation in enumerate(track["observations"]):
            if not observation or observation.get("accepted") is not True:
                continue
            key = (eye, observation["first_index"], observation["second_index"])
            candidate = mapped.get(key)
            if candidate is None:
                fallback.append({"eye": eye, "first_index": key[1], "second_index": key[2]})
                continue
            for name in ("first_t_sec", "second_t_sec", "metric_displacement_frame"):
                if candidate[name] != observation[name]:
                    raise ValueError(f"refreshed observation changed binding: {name}")
            if not np.array_equal(candidate["body_t_camera"], track["body_t_camera"]):
                raise ValueError("refreshed observation changed body camera extrinsic")
            # The existing builder reads this displacement, not PnP rotations.
            observation["metric_displacement_camera_i_m"] = deepcopy(
                candidate["metric_displacement_camera_i_m"]
            )
            track["observation_confidences"][index] = candidate["observation_confidence"]
            refreshed += 1
    return result, {
        "refreshed_existing_observation_count": refreshed,
        "retained_original_observations_without_new_source": fallback,
        "appended_learned_observation_count": 0,
        "track_pose_and_scale_policy": "frozen reconstructed baseline",
    }


def validate_unchanged_targets(original, rebuilt):
    def mapping(factors):
        result = {}
        for factor in factors:
            key = (factor["eye"], factor["first_index"], factor["second_index"])
            if key in result:
                raise ValueError("duplicate learned target identity")
            result[key] = factor
        return result
    before, after = mapping(original), mapping(rebuilt)
    if set(before) != set(after) or len(original) != len(rebuilt):
        raise ValueError("native source rebuild changed learned factor keys/count")
    maximum = 0.0
    for key, old in before.items():
        delta = np.max(np.abs(
            np.asarray(old["metric_displacement_world_m"], dtype=float)
            - np.asarray(after[key]["metric_displacement_world_m"], dtype=float)
        ))
        if not np.isfinite(delta) or delta > 1e-12:
            raise ValueError("native source rebuild changed learned target")
        maximum = max(maximum, float(delta))
    return {"factor_count": len(original), "max_target_abs_error_m": maximum}


def main(argv=None):
    original_load = paired.load_refined_all_eye_candidates
    original_solver = paired.physical.run_solver_variant
    original_metadata = paired.postprocess_candidate_metadata
    original_paths = paired.frozen_code_paths
    candidates_by_id = {}

    def load(record, *args, **kwargs):
        loaded = original_load(record, *args, **kwargs)
        candidates_by_id[record["id"]] = loaded[0]
        return loaded

    def solve(record, artifact, variant, variant_dir, state, candidate,
              stereo_rows, stereo_report, raw_paths, motion_factors,
              motion_source, extra_paths, hashes):
        if variant == paired.ORIGINAL_VARIANT:
            return original_solver(record, artifact, variant, variant_dir, state,
                                   candidate, stereo_rows, stereo_report, raw_paths,
                                   motion_factors, motion_source, extra_paths, hashes)
        tracks, _metadata, track_paths = gauge.reconstruct_tracks(record, state, candidate)
        consumed = paired.snapshot_paths(track_paths)
        old = paired.read_json(artifact / "local_motion_factors.json")
        reconstructed, _, _ = gauge.rebuild_source_factors(state, tracks, candidate["policy_arguments"])
        old_identity = gauge.validate_reconstructed_factor_identity(old, reconstructed)
        frozen, _ = gauge.transform_existing_motion_factors(
            state.times, state.rotations.as_matrix(),
            [tracks["left"], tracks["right"]], old,
        )
        gauge.validate_reconstructed_factor_identity(motion_factors, frozen)
        updated_tracks, source_report = refreshed_existing_tracks(tracks, candidates_by_id[record["id"]])
        rebuilt, _, _ = gauge.rebuild_source_factors(state, updated_tracks, candidate["policy_arguments"])
        target_identity = validate_unchanged_targets(old, rebuilt)
        updated, sync_report = sync_existing_factor_source_metadata(motion_factors, rebuilt)
        paired.assert_hashes_unchanged(consumed)
        report_path = variant_dir / "learned_source_consistency_report.json"
        paired.write_json(report_path, {
            "schema": "umi_learned_source_consistency_probe_v1",
            "development_only": True, "external_ground_truth_used": False,
            "slam_supervision": False, "old_source_identity": old_identity,
            "new_source_target_identity": target_identity,
            "source_refresh": source_report, "metadata_sync": sync_report,
            "policy_arguments_unchanged": candidate["policy_arguments"],
            "consumed_track_sha256": consumed,
        })
        result = original_solver(record, artifact, variant, variant_dir, state,
                                 candidate, stereo_rows, stereo_report, raw_paths,
                                 updated, "same_native_source_consistency",
                                 [*extra_paths, *track_paths, report_path], hashes)
        paired.assert_hashes_unchanged(consumed)
        return result

    def metadata(variant_dir, *args, **kwargs):
        original_metadata(variant_dir, *args, **kwargs)
        report = variant_dir / "learned_source_consistency_report.json"
        if not report.exists():
            return
        factors = variant_dir / "local_motion_factors.json"
        for name in ("candidate_manifest.json", "graph_report.json"):
            path = variant_dir / name
            value = paired.read_json(path)
            original_context = value["learned_factor_context"]
            value["learned_factor_context"] = {
                "source": "same_native_source_consistency",
                "identical_between_arms": False,
                "path": str(factors.resolve()), "sha256": paired.file_hash(factors),
                "frozen_target_context": original_context,
                "consistency_report": str(report.resolve()),
                "consistency_report_sha256": paired.file_hash(report),
            }
            value["source_upgrade_scope"]["learned_source_metadata_synchronized"] = True
            paired.write_json(path, value)

    def paths(source_stage):
        return [*original_paths(source_stage), Path(__file__),
                Path(gauge.__file__), ROOT / "ego_vio/vio/constant_ir_gauge.py",
                ROOT / "ego_vio/vio/learned_source_consistency.py"]

    with ExitStack() as stack:
        for owner, name, value in (
            (paired, "load_refined_all_eye_candidates", load),
            (paired.physical, "run_solver_variant", solve),
            (paired, "postprocess_candidate_metadata", metadata),
            (paired, "frozen_code_paths", paths),
            (recovery, "REFINED_VARIANT", VARIANT),
        ):
            stack.enter_context(patch.object(owner, name, value))
        return recovery.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
