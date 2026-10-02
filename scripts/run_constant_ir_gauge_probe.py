#!/usr/bin/env python3
"""Development-only constant-IR-gauge learned factor replay.

Consumes a completed dual-IR adapter batch.  It rebuilds the left/right tracks
from onboard cache inputs, transforms only the learned motion-factor
displacements into the constant SO3 gauge, freezes the candidate, and scores
with the existing frozen scorer.  No frontends or GPU work are launched here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import fuse_mast3r_dual_ir_symmetric as symmetric  # noqa: E402
import fuse_mast3r_stereo_imu as fusion  # noqa: E402
import run_dual_ir_regression_corpus as corpus  # noqa: E402
import run_learned_segment_probe as base  # noqa: E402


VARIANT = "selected"
BASELINE_POLICY = "both"
SCHEMA = "umi_constant_ir_gauge_development_regression_v1"
TRANSFORM_MODULE = ROOT / "ego_vio/vio/constant_ir_gauge.py"
BASE_RUNNER_MODULE = ROOT / "scripts/run_learned_segment_probe.py"
POLICY_ARGUMENTS = {
    "source_policy": BASELINE_POLICY,
    "variant": VARIANT,
    "learned_factor_transform": "constant_ir_fixed_so3_gauge_v1",
    **base.BASELINE_POLICY_ARGUMENTS,
}


def transform_existing_motion_factors(reference_times, reference_rotations, tracks, original_factors):
    from ego_vio.vio.constant_ir_gauge import (  # noqa: PLC0415
        transform_existing_motion_factors as transform,
    )

    return transform(reference_times, reference_rotations, tracks, original_factors)


def frozen_code_paths() -> list[Path]:
    paths = [
        ROOT / "scripts/run_dual_ir_regression_corpus.py",
        ROOT / "scripts/fuse_mast3r_dual_ir_symmetric.py",
        ROOT / "scripts/fuse_mast3r_stereo_imu.py",
        ROOT / "ego_vio/vio/symmetric_ir_factors.py",
        ROOT / "scripts/prepare_dual_ir_eye_cache.py",
        ROOT / "ego_vio/vio/dual_ir_factors.py",
        ROOT / "scripts/derive_right_ir_stereo_scale.py",
        ROOT / "scripts/align_mast3r_scale_with_imu.py",
        ROOT / "scripts/mast3r_slam_precision_workflow.sh",
        ROOT / "scripts/score_steamvr_slam.py",
        corpus.VINS_CONFIG,
        corpus.IMU_CALIBRATION,
        corpus.OFFLINE_CONFIG,
        BASE_RUNNER_MODULE,
        TRANSFORM_MODULE,
        Path(__file__),
    ]
    return paths


def baseline_artifact_dir(baseline: Path, record_id: str) -> Path:
    return baseline / record_id / BASELINE_POLICY


def write_json(path: Path, value: Any) -> None:
    base.write_json(path, value)


def read_json(path: Path) -> Any:
    return base.read_json(path)


def file_hash(path: Path) -> str:
    return base.file_hash(path)


def snapshot_hashes(paths: list[Path]) -> dict[str, str]:
    return base.snapshot_hashes(paths)


def code_changed(frozen_hashes: dict[str, str]) -> bool:
    return base.code_changed(frozen_hashes)


def validate_track_source_hashes(candidate: dict[str, Any], paths: list[Path]) -> dict[str, str]:
    baseline_hashes = candidate.get("input_sha256", {})
    track_hashes = {}
    for path in paths:
        resolved = str(Path(path).resolve())
        if resolved not in baseline_hashes:
            raise ValueError(f"track source not bound in baseline candidate: {resolved}")
        digest = file_hash(Path(path))
        if digest != baseline_hashes[resolved]:
            raise ValueError(f"track source hash changed: {resolved}")
        track_hashes[resolved] = digest
    return track_hashes


def bound_eye_cache_directory(candidate: dict[str, Any], eye: str) -> Path:
    """Use the actual frozen baseline cache, not an earlier recording hint."""
    names = {"left": "trajectory_imu_metric.csv", "right": "imu_metric_trajectory.csv"}
    if eye not in names:
        raise ValueError("unknown eye")
    paths = [Path(path) for path in candidate.get("input_sha256", {})
             if Path(path).name == names[eye]]
    if len(paths) != 1:
        raise ValueError(f"expected exactly one hash-bound {eye} trajectory")
    return paths[0].parent


def reconstruct_tracks(record: dict[str, Any], state, baseline_candidate: dict[str, Any]):
    tracks = {}
    metadata = {}
    all_paths = []
    for eye in ("left", "right"):
        track, report, paths = symmetric.load_eye(
            bound_eye_cache_directory(baseline_candidate, eye),
            eye,
            Path(record["session"]),
            state.config,
            state.imu_times,
            state.gyro,
            optional_stereo_policy="reject_window",
        )
        tracks[eye] = track
        metadata[eye] = report
        all_paths.extend(Path(path) for path in paths)
    validate_track_source_hashes(baseline_candidate, all_paths)
    return tracks, metadata, all_paths


def validate_reconstructed_factor_identity(
    original_factors: list[dict[str, Any]],
    reconstructed_factors: list[dict[str, Any]],
) -> dict[str, Any]:
    """Prove reconstructed cache inputs rebuild the baseline learned factors."""

    if len(original_factors) != len(reconstructed_factors):
        raise ValueError(
            "reconstructed motion factor count changed: "
            f"{len(reconstructed_factors)} != {len(original_factors)}"
        )
    scalar_keys = (
        "confidence",
        "own_confidence",
        "own_observation_confidence",
        "own_stereo_residual_m",
    )
    max_vector_error = 0.0
    for index, (original, reconstructed) in enumerate(
        zip(original_factors, reconstructed_factors)
    ):
        for key in ("eye", "first_index", "second_index"):
            if original.get(key) != reconstructed.get(key):
                raise ValueError(
                    f"reconstructed motion factor {index} {key} changed: "
                    f"{reconstructed.get(key)!r} != {original.get(key)!r}"
                )
        for key in scalar_keys:
            if key in original or key in reconstructed:
                if key not in original or key not in reconstructed:
                    raise ValueError(
                        f"reconstructed motion factor {index} {key} presence changed"
                    )
                if not np.isclose(
                    float(original[key]),
                    float(reconstructed[key]),
                    rtol=0.0,
                    atol=1e-12,
                ):
                    raise ValueError(
                        f"reconstructed motion factor {index} {key} changed: "
                        f"{reconstructed[key]!r} != {original[key]!r}"
                    )
        original_vector = np.asarray(original.get("metric_displacement_world_m"), dtype=float)
        reconstructed_vector = np.asarray(
            reconstructed.get("metric_displacement_world_m"), dtype=float
        )
        if original_vector.shape != (3,) or reconstructed_vector.shape != (3,):
            raise ValueError(f"reconstructed motion factor {index} vector shape changed")
        vector_error = float(np.max(np.abs(original_vector - reconstructed_vector)))
        max_vector_error = max(max_vector_error, vector_error)
        if vector_error > 1e-12:
            raise ValueError(
                f"reconstructed motion factor {index} vector changed by {vector_error:.3e}"
            )
    return {
        "schema": "reconstructed_motion_factor_identity_v1",
        "factor_count": len(original_factors),
        "max_metric_displacement_abs_error_m": max_vector_error,
    }


def rebuild_source_factors(state, tracks: dict[str, dict[str, Any]], baseline_policy: dict[str, Any]):
    return symmetric.build_symmetric_ir_factors(
        state.times,
        state.rotations.as_matrix(),
        [tracks["left"], tracks["right"]],
        stereo_weight_policy=baseline_policy["stereo_weight_policy"],
        learned_motion_consistency_limit_m=baseline_policy[
            "learned_motion_consistency_limit_m"
        ],
    )


def provenance_hashes(
    baseline_candidate: dict[str, Any],
    artifact: Path,
    track_paths: list[Path],
) -> dict[str, str]:
    hashes = dict(baseline_candidate.get("input_sha256", {}))
    for path in (
        artifact / "candidate_manifest.json",
        artifact / "graph_report.json",
        artifact / "local_motion_factors.json",
        artifact / "shared_stereo_observations.json",
        artifact / "body_trajectory_fused.csv",
        BASE_RUNNER_MODULE,
        TRANSFORM_MODULE,
        Path(__file__),
        *track_paths,
    ):
        hashes[str(Path(path).resolve())] = file_hash(Path(path))
    return hashes


def run_record(record: dict[str, Any], baseline: Path, output: Path, frozen_hashes: dict[str, str]) -> dict[str, Any]:
    record_id = record["id"]
    artifact = baseline_artifact_dir(baseline, record_id)
    result = {"id": record_id, "status": "IN_PROGRESS", "variants": {}}
    variant_dir = output / record_id / VARIANT
    variant_dir.mkdir(parents=True)
    try:
        base.validate_record_sources(record)
        baseline_candidate, baseline_graph = base.validate_baseline_artifact(record, artifact)
        state = base.load_bound_reference(record)
        base.validate_baseline_trajectory_identity(artifact, state, baseline_graph)
        tracks, track_metadata, track_paths = reconstruct_tracks(
            record, state, baseline_candidate
        )
        original_factors = read_json(artifact / "local_motion_factors.json")
        shared_stereo = read_json(artifact / "shared_stereo_observations.json")
        reconstructed_factors, _reconstructed_stereo, reconstruction_report = (
            rebuild_source_factors(state, tracks, baseline_candidate["policy_arguments"])
        )
        reconstruction_identity = validate_reconstructed_factor_identity(
            original_factors,
            reconstructed_factors,
        )
        reconstruction_identity["builder_report"] = reconstruction_report
        transformed_factors, transform_report = transform_existing_motion_factors(
            state.times,
            state.rotations.as_matrix(),
            [tracks["left"], tracks["right"]],
            original_factors,
        )
        write_json(variant_dir / "local_motion_factors.json", transformed_factors)
        write_json(variant_dir / "shared_stereo_observations.json", shared_stereo)
        write_json(variant_dir / "constant_ir_gauge_transform_report.json", transform_report)
        refined, graph_solver = fusion.refine_positions_visual_inertial(
            state.positions,
            state.rotations,
            shared_stereo,
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
            secondary_visual_factors=transformed_factors,
            use_visual_position_prior=False,
            solve_metric_scale=False,
            correction_cap_mode="global",
            stereo_factor_confidences=np.asarray(
                [
                    float(observation.get("pnp_inlier_ratio", 0.5))
                    for observation in shared_stereo
                ]
            ),
        )
        graph_solver = dict(graph_solver)
        graph_solver["constant_ir_gauge_transform"] = transform_report
        estimate = variant_dir / "body_trajectory_fused.csv"
        fusion.write_trajectory(estimate, state.rows, refined, state.rotations)
        estimate_sha = file_hash(estimate)
        candidate = {
            "schema": "umi_constant_ir_gauge_candidate_v1",
            "status": "EXPERIMENTAL_NOT_ACCEPTED",
            "accepted": False,
            "external_ground_truth_used": False,
            "slam_supervision": False,
            "session": str(Path(record["session"]).resolve()),
            "source_baseline_artifact": str(artifact.resolve()),
            "source_candidate_manifest": str((artifact / "candidate_manifest.json").resolve()),
            "input_sha256": provenance_hashes(baseline_candidate, artifact, track_paths),
            "output_estimate_sha256": estimate_sha,
            "bound_samples": len(state.rows),
            "baseline_policy_arguments": baseline_candidate["policy_arguments"],
            "policy_arguments": dict(POLICY_ARGUMENTS),
            "track_reports": track_metadata,
            "source_factor_reconstruction": reconstruction_identity,
            "transform_diagnostic": transform_report,
            "vins_source_validation": state.source_quality,
            "reference_time_binding": state.binding,
            "imu": state.imu_info,
        }
        write_json(variant_dir / "candidate_manifest.json", candidate)
        graph_report = {
            "schema": "umi_constant_ir_gauge_graph_diagnostic_v1",
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
        if code_changed(frozen_hashes):
            raise base.StopCodeChanged("STOP_CODE_CHANGED before scoring")
        if file_hash(estimate) != estimate_sha:
            raise RuntimeError("estimate changed before scoring")
        score = corpus.score_frozen(
            record,
            estimate,
            variant_dir / "score",
            output / record_id,
            f"score_{record_id}_{VARIANT}",
        )
        if code_changed(frozen_hashes):
            raise base.StopCodeChanged("STOP_CODE_CHANGED after scoring")
        if file_hash(estimate) != estimate_sha:
            raise RuntimeError("estimate changed during scoring")
        result["variants"][VARIANT] = {
            "artifact_dir": str(variant_dir.resolve()),
            "score": score,
            "estimate_sha256": estimate_sha,
            "transform": {
                "schema": transform_report.get("schema"),
                "changed_factor_count": transform_report.get("changed_factor_count"),
                "input_factor_count": transform_report.get("input_factor_count"),
            },
        }
        result["status"] = "COMPLETED"
    except base.StopCodeChanged:
        raise
    except Exception as error:
        result["status"] = "INCOMPLETE_VARIANTS"
        result["variants"][VARIANT] = {
            "artifact_dir": str(variant_dir.resolve()),
            "error_code": "VARIANT_FAILED",
            "stage": "run_record",
            "error": f"{type(error).__name__}: {error}",
        }
    return result


def aggregate(results: list[dict[str, Any]], dataset_count: int) -> dict[str, Any]:
    return base.aggregate(results, dataset_count)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
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
    frozen_hashes = snapshot_hashes(frozen_code_paths())
    args.output.mkdir(parents=True)
    summary = {
        "schema": SCHEMA,
        "status": "RUNNING",
        "development_only": True,
        "blind_test": False,
        "production_promoted": False,
        "dataset_count": len(records),
        "completed_count": 0,
        "manifest_sha256": file_hash(args.manifest),
        "baseline_summary_sha256": file_hash(args.baseline / "summary.json"),
        "baseline": str(args.baseline.resolve()),
        "variants": [VARIANT],
        "policy_arguments": dict(POLICY_ARGUMENTS),
        "code_sha256": frozen_hashes,
        "results": [],
    }
    write_json(args.output / "summary.json", summary)
    for index, record in enumerate(records, 1):
        if code_changed(frozen_hashes):
            summary["status"] = "STOP_CODE_CHANGED"
            write_json(args.output / "summary.json", summary)
            return 2
        started = time.monotonic()
        baseline_row = baseline_by_id[record["id"]]
        if baseline_row.get("status") == "PREPARATION_OR_INPUT_FAILED":
            result = {
                "id": record["id"],
                "status": "PREPARATION_OR_INPUT_FAILED",
                "source_baseline_status": baseline_row.get("status"),
                "source_baseline_error": baseline_row.get("error"),
                "variants": {},
            }
        elif baseline_row.get("status") != "COMPLETED":
            result = {
                "id": record["id"],
                "status": baseline_row.get("status", "INCOMPLETE_VARIANTS"),
                "source_baseline_status": baseline_row.get("status"),
                "variants": {},
            }
        else:
            try:
                result = run_record(record, args.baseline, args.output, frozen_hashes)
            except base.StopCodeChanged:
                summary["status"] = "STOP_CODE_CHANGED"
                write_json(args.output / "summary.json", summary)
                return 2
        result["elapsed_s"] = time.monotonic() - started
        summary["results"].append(result)
        summary["completed_count"] = index
        summary["aggregates"] = aggregate(summary["results"], len(records))
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
