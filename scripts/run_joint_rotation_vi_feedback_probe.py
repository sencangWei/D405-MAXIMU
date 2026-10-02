#!/usr/bin/env python3
"""Development-only joint-rotation feedback through the established VI solver.

This wrapper is deliberately narrow: it reuses the physical-stereo full
visual-inertial replay path, and changes only the rotation sequence used to
recompute constant-gauge learned factors and physical stereo rows.  It never
uses scorer output or ground truth to construct a candidate.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import run_constant_ir_gauge_probe as constant  # noqa: E402
import run_physical_stereo_lever_probe as physical  # noqa: E402

base, fusion, corpus = physical.base, physical.fusion, physical.corpus

ORIGINAL_VARIANT = "original_rotation_control"
FEEDBACK_VARIANT = "staged_joint_rotation_feedback"
VARIANTS = [ORIGINAL_VARIANT, FEEDBACK_VARIANT]
BASELINE_POLICY = "both"
CONSTANT_VARIANT = "selected"
COMBINED_VARIANT = "physical_stereo_constant_gauge"
JOINT_VARIANT = "joint_rotation_position"
SCHEMA = "umi_joint_rotation_vi_feedback_development_regression_v1"

CONSTANT_RUNNER_MODULE = ROOT / "scripts/run_constant_ir_gauge_probe.py"
PHYSICAL_RUNNER_MODULE = ROOT / "scripts/run_physical_stereo_lever_probe.py"
JOINT_RUNNER_MODULE = ROOT / "scripts/run_joint_stereo_se3_probe.py"
JOINT_SOLVER_MODULE = ROOT / "ego_vio/vio/joint_stereo_se3.py"
JOINT_STEREO_MODULE = ROOT / "ego_vio/vio/stereo_se3_factors.py"
CONSTANT_MODULE = ROOT / "ego_vio/vio/constant_ir_gauge.py"


def read_json(path: Path) -> Any:
    return base.read_json(path)


def write_json(path: Path, value: Any) -> None:
    base.write_json(path, value)


def file_hash(path: Path) -> str:
    return base.file_hash(path)


def frozen_code_paths() -> list[Path]:
    paths = [
        *physical.frozen_code_paths(),
        CONSTANT_RUNNER_MODULE,
        PHYSICAL_RUNNER_MODULE,
        JOINT_RUNNER_MODULE,
        JOINT_SOLVER_MODULE,
        JOINT_STEREO_MODULE,
        CONSTANT_MODULE,
        Path(__file__),
    ]
    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = Path(path).resolve()
        if resolved not in seen:
            seen.add(resolved)
            deduped.append(Path(path))
    return deduped


def check_frozen(hashes: dict[str, str]) -> None:
    if base.code_changed(hashes):
        raise base.StopCodeChanged("STOP_CODE_CHANGED")


def validate_completed_summary(root: Path, label: str) -> dict[str, Any]:
    summary_path = root / "summary.json"
    if not summary_path.is_file():
        raise ValueError(f"{label} summary is missing")
    summary = read_json(summary_path)
    if summary.get("status") not in ("COMPLETED", "COMPLETED_WITH_FAILURES"):
        raise ValueError(f"{label} summary is not closed: {summary.get('status')!r}")
    return summary


def validate_requested_records_in_summary(
    records: list[dict[str, Any]],
    summary: dict[str, Any],
    label: str,
) -> None:
    result_ids = {row.get("id") for row in summary.get("results", [])}
    missing = {record["id"] for record in records} - result_ids
    if missing:
        raise ValueError(f"{label} summary missing requested records: {sorted(missing)}")


def baseline_artifact_dir(baseline: Path, record_id: str) -> Path:
    return baseline / record_id / BASELINE_POLICY


def constant_artifact_dir(root: Path, record_id: str) -> Path:
    return root / record_id / CONSTANT_VARIANT


def combined_artifact_dir(root: Path, record_id: str) -> Path:
    return root / record_id / COMBINED_VARIANT


def joint_artifact_dir(root: Path, record_id: str) -> Path:
    return root / record_id / JOINT_VARIANT


def _require_hash(path: Path, expected: str | None, label: str) -> None:
    if not expected:
        raise ValueError(f"{label} hash missing")
    if file_hash(path) != expected:
        raise ValueError(f"{label} hash mismatch")


def _validate_all_input_hashes(candidate: dict[str, Any], label: str) -> None:
    hashes = candidate.get("input_sha256")
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError(f"{label} input hashes missing")
    for source, expected in hashes.items():
        path = Path(source)
        if not path.is_file() or file_hash(path) != expected:
            raise ValueError(f"{label} input hash changed: {source}")


def candidate_expected_input_hashes(
    label: str,
    candidate: dict[str, Any],
) -> dict[str, str]:
    hashes = candidate.get("input_sha256")
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError(f"{label} input hashes missing")
    expected: dict[str, str] = {}
    for source, digest in hashes.items():
        path = Path(source)
        if not isinstance(digest, str) or not digest:
            raise ValueError(f"{label} input hash malformed: {source}")
        resolved = str(path.resolve())
        if resolved in expected and expected[resolved] != digest:
            raise ValueError(f"{label} input hash conflict: {resolved}")
        expected[resolved] = digest
    return expected


def merge_expected_input_hashes(*items: tuple[str, dict[str, Any]]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for label, candidate in items:
        for path, digest in candidate_expected_input_hashes(label, candidate).items():
            if path in merged and merged[path] != digest:
                raise ValueError(f"candidate input hash conflict: {path}")
            merged[path] = digest
    return merged


def validate_constant_artifact(
    record: dict[str, Any],
    constant_root: Path,
) -> tuple[Path, dict[str, Any], list[Path]]:
    artifact, candidate, _graph, paths = physical.validate_constant_artifact(
        record,
        constant_root,
    )
    return artifact, candidate, paths


def validate_combined_artifact(
    record: dict[str, Any],
    combined_root: Path,
) -> tuple[Path, dict[str, Any], list[Path]]:
    artifact = combined_artifact_dir(combined_root, record["id"])
    paths = [
        artifact / "candidate_manifest.json",
        artifact / "graph_report.json",
        artifact / "local_motion_factors.json",
        artifact / "shared_stereo_observations.json",
        artifact / "body_trajectory_fused.csv",
    ]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise ValueError(f"missing combined control artifact: {missing[:3]}")
    candidate = read_json(artifact / "candidate_manifest.json")
    graph = read_json(artifact / "graph_report.json")
    base.require_onboard_report(candidate, "combined control candidate")
    base.require_onboard_report(graph, "combined control graph")
    if candidate.get("schema") != "umi_physical_stereo_lever_candidate_v1":
        raise ValueError("combined control candidate schema mismatch")
    if Path(candidate.get("session", "")).resolve() != Path(record["session"]).resolve():
        raise ValueError("combined control candidate session mismatch")
    if graph.get("output_frame") != "body_imu_origin":
        raise ValueError("combined control graph output mismatch")
    if candidate.get("policy_arguments", {}).get("variant") != COMBINED_VARIANT:
        raise ValueError("combined control variant mismatch")
    _require_hash(
        artifact / "body_trajectory_fused.csv",
        candidate.get("output_estimate_sha256"),
        "combined control estimate",
    )
    _validate_all_input_hashes(candidate, "combined control candidate")
    return artifact, candidate, paths


def canonical_trajectory_serialized_rotation(matrix: np.ndarray) -> np.ndarray:
    """Round-trip a rotation through write_trajectory's 9-decimal quaternion CSV."""

    quaternion = Rotation.from_matrix(np.asarray(matrix, dtype=float)).as_quat()
    serialized = np.asarray([float(f"{value:.9f}") for value in quaternion], dtype=float)
    norm = float(np.linalg.norm(serialized))
    if not np.isfinite(norm) or norm <= 0.0:
        raise ValueError("serialized first-gauge quaternion is invalid")
    return Rotation.from_quat(serialized / norm).as_matrix()


def validate_joint_rotation_artifact(
    record: dict[str, Any],
    joint_root: Path,
    state,
) -> tuple[np.ndarray, dict[str, Any], list[Path], dict[str, Any]]:
    artifact = joint_artifact_dir(joint_root, record["id"])
    paths = [
        artifact / "candidate_manifest.json",
        artifact / "solver_diagnostic.json",
        artifact / "body_trajectory_fused.csv",
    ]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise ValueError(f"missing joint rotation artifact: {missing[:3]}")
    candidate = read_json(artifact / "candidate_manifest.json")
    solver_diagnostic = read_json(artifact / "solver_diagnostic.json")
    base.require_onboard_report(candidate, "joint rotation candidate")
    if candidate.get("schema") != "umi_joint_stereo_se3_pose_only_candidate_v1":
        raise ValueError("joint rotation candidate schema mismatch")
    if candidate.get("variant") != JOINT_VARIANT:
        raise ValueError("joint rotation variant mismatch")
    if Path(candidate.get("session", "")).resolve() != Path(record["session"]).resolve():
        raise ValueError("joint rotation candidate session mismatch")
    if candidate.get("bound_samples") != len(state.times):
        raise ValueError("joint rotation sample count mismatch")
    diagnostic = candidate.get("solver_diagnostic", {})
    if solver_diagnostic != diagnostic:
        raise ValueError("joint rotation solver diagnostic file mismatch")
    if diagnostic.get("least_squares_success") is not True:
        raise ValueError("joint rotation solver did not converge")
    if diagnostic.get("optimize_rotations") is not True:
        raise ValueError("joint rotation artifact is not the joint-R arm")
    estimate = artifact / "body_trajectory_fused.csv"
    _require_hash(estimate, candidate.get("output_estimate_sha256"), "joint estimate")
    _validate_all_input_hashes(candidate, "joint rotation candidate")

    times, _positions, rotations, _rows = fusion.load_trajectory(estimate)
    if len(times) != len(state.times) or not np.allclose(
        times,
        state.times,
        rtol=0.0,
        atol=1e-12,
    ):
        raise ValueError("joint rotation estimate timestamps changed")
    matrices = rotations.as_matrix()
    if matrices.shape != (len(state.times), 3, 3):
        raise ValueError("joint rotation estimate rotation shape mismatch")
    if not np.all(np.isfinite(matrices)):
        raise ValueError("joint rotation estimate contains non-finite rotations")
    if not np.allclose(matrices @ matrices.transpose(0, 2, 1), np.eye(3), atol=1e-8, rtol=0):
        raise ValueError("joint rotation estimate contains non-SO3 rotations")
    if not np.allclose(np.linalg.det(matrices), 1.0, atol=1e-8, rtol=0):
        raise ValueError("joint rotation estimate determinant is invalid")
    original_first = state.rotations.as_matrix()[0]
    expected_serialized_first = canonical_trajectory_serialized_rotation(original_first)
    first_serialization_abs_error = float(
        np.max(np.abs(matrices[0] - expected_serialized_first))
    )
    first_serialization_angle_rad = float(
        Rotation.from_matrix(expected_serialized_first.T @ matrices[0]).magnitude()
    )
    first_original_angle_rad = float(
        Rotation.from_matrix(original_first.T @ matrices[0]).magnitude()
    )
    if (
        first_serialization_abs_error > 1e-12
        or first_serialization_angle_rad > 1e-12
    ):
        raise ValueError("joint rotation changed the first-pose gauge")
    matrices = matrices.copy()
    matrices[0] = original_first
    gauge_report = {
        "schema": "joint_rotation_first_gauge_serialization_v1",
        "policy": "accept_exact_trajectory_quaternion_9_decimal_roundtrip_only",
        "first_pose_restored_to_original_reference_rotation": True,
        "serialized_first_max_abs_error": first_serialization_abs_error,
        "serialized_first_angle_rad": first_serialization_angle_rad,
        "original_vs_serialized_first_angle_rad": first_original_angle_rad,
    }
    return matrices, candidate, paths, gauge_report


def transform_motion_factors(
    state,
    tracks: dict[str, dict[str, Any]],
    original_factors: list[dict[str, Any]],
    reference_rotations: np.ndarray,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return constant.transform_existing_motion_factors(
        state.times,
        reference_rotations,
        [tracks["left"], tracks["right"]],
        original_factors,
    )


def transform_shared_rows_with_rotations(
    state,
    reference_rotations: np.ndarray,
    eye_candidates: list[dict[str, Any]],
    original_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    transformed, report = physical.transform_shared_stereo_body_lever(
        state.times,
        reference_rotations,
        eye_candidates,
        original_rows,
    )
    identity = physical.validate_shared_stereo_identity(original_rows, transformed)
    identity["transform_report"] = report
    return transformed, identity


def _compare_vec3_lists(
    generated: list[dict[str, Any]],
    expected: list[dict[str, Any]],
    key: str,
    label: str,
) -> dict[str, Any]:
    if len(generated) != len(expected):
        raise ValueError(f"{label} count mismatch: {len(generated)} != {len(expected)}")
    max_abs = 0.0
    for index, (left, right) in enumerate(zip(generated, expected)):
        for scalar_key in ("eye", "first_index", "second_index", "confidence", "pnp_inlier_ratio"):
            if scalar_key in left or scalar_key in right:
                if left.get(scalar_key) != right.get(scalar_key):
                    raise ValueError(f"{label} row {index} {scalar_key} mismatch")
        left_vec = np.asarray(left.get(key), dtype=float)
        right_vec = np.asarray(right.get(key), dtype=float)
        if left_vec.shape != (3,) or right_vec.shape != (3,):
            raise ValueError(f"{label} row {index} vector shape mismatch")
        if not np.all(np.isfinite(left_vec)) or not np.all(np.isfinite(right_vec)):
            raise ValueError(f"{label} row {index} vector contains non-finite values")
        error = float(np.max(np.abs(left_vec - right_vec)))
        max_abs = max(max_abs, error)
        if error > 1e-12:
            raise ValueError(f"{label} row {index} vector changed by {error:.3e}")
    return {"schema": f"{label}_identity_v1", "count": len(generated), "max_abs_error_m": max_abs}


def validate_control_identity(
    variant_dir: Path,
    combined_artifact: Path,
    generated_motion: list[dict[str, Any]],
    generated_stereo: list[dict[str, Any]],
) -> dict[str, Any]:
    expected_motion = read_json(combined_artifact / "local_motion_factors.json")
    expected_stereo = read_json(combined_artifact / "shared_stereo_observations.json")
    motion_identity = _compare_vec3_lists(
        generated_motion,
        expected_motion,
        "metric_displacement_world_m",
        "control_motion_factor",
    )
    stereo_identity = _compare_vec3_lists(
        generated_stereo,
        expected_stereo,
        "metric_displacement_camera_i_m",
        "control_stereo_row",
    )
    estimate = variant_dir / "body_trajectory_fused.csv"
    if file_hash(estimate) != file_hash(combined_artifact / "body_trajectory_fused.csv"):
        raise ValueError("original-rotation control estimate does not byte-match combined artifact")
    return {
        "schema": "original_rotation_control_identity_v1",
        "combined_artifact": str(combined_artifact.resolve()),
        "estimate_sha256": file_hash(estimate),
        "motion_factor_sha256": file_hash(variant_dir / "local_motion_factors.json"),
        "stereo_rows_sha256": file_hash(variant_dir / "shared_stereo_observations.json"),
        "motion": motion_identity,
        "stereo": stereo_identity,
    }


def state_with_rotations(state, rotations: np.ndarray):
    return SimpleNamespace(**{**vars(state), "rotations": Rotation.from_matrix(rotations)})


def augment_candidate_manifest(
    variant_dir: Path,
    *,
    variant: str,
    arm_label: str,
    reference_rotation_source: str,
    joint_candidate: dict[str, Any],
    joint_gauge_report: dict[str, Any],
    joint_paths: list[Path],
    control_identity: dict[str, Any] | None,
    extra_paths: list[Path],
    frozen_hashes: dict[str, str],
) -> None:
    manifest_path = variant_dir / "candidate_manifest.json"
    candidate = read_json(manifest_path)
    input_hashes = dict(candidate.get("input_sha256", {}))
    for path in [Path(__file__), *joint_paths, *extra_paths]:
        input_hashes[str(Path(path).resolve())] = file_hash(Path(path))
    input_hashes.update(frozen_hashes)
    candidate["input_sha256"] = input_hashes
    candidate["schema"] = "umi_joint_rotation_vi_feedback_candidate_v1"
    candidate["variant"] = variant
    candidate["staged_single_rotation_feedback"] = {
        "schema": "staged_single_rotation_feedback_v1",
        "arm": arm_label,
        "reference_rotation_source": reference_rotation_source,
        "uses_joint_positions": False,
        "uses_ground_truth_for_construction": False,
        "joint_variant": JOINT_VARIANT,
        "joint_output_estimate_sha256": joint_candidate.get("output_estimate_sha256"),
        "joint_first_gauge_serialization": joint_gauge_report,
        "control_identity": control_identity,
    }
    write_json(manifest_path, candidate)


def run_solver_arm(
    record: dict[str, Any],
    artifact: Path,
    variant: str,
    variant_dir: Path,
    state,
    baseline_candidate: dict[str, Any],
    transformed_stereo: list[dict[str, Any]],
    stereo_report: dict[str, Any],
    raw_report_paths: list[Path],
    motion_factors: list[dict[str, Any]],
    motion_source: str,
    extra_paths: list[Path],
    frozen_hashes: dict[str, str],
) -> dict[str, Any]:
    check_frozen(frozen_hashes)
    return physical.run_solver_variant(
        record,
        artifact,
        variant,
        variant_dir,
        state,
        baseline_candidate,
        transformed_stereo,
        stereo_report,
        raw_report_paths,
        motion_factors,
        motion_source,
        extra_paths,
        frozen_hashes,
    )


def run_variant_failure(variant_dir: Path, error: Exception, stage: str = "run_record") -> dict[str, Any]:
    return {
        "artifact_dir": str(variant_dir.resolve()),
        "error_code": "VARIANT_FAILED",
        "stage": stage,
        "error": f"{type(error).__name__}: {error}",
    }


def run_record(
    record: dict[str, Any],
    baseline: Path,
    constant_root: Path,
    joint_root: Path,
    combined_root: Path,
    output: Path,
    frozen_hashes: dict[str, str],
) -> dict[str, Any]:
    record_id = record["id"]
    artifact = baseline_artifact_dir(baseline, record_id)
    result = {"id": record_id, "status": "IN_PROGRESS", "variants": {}}
    try:
        check_frozen(frozen_hashes)
        base.validate_record_sources(record)
        baseline_candidate, baseline_graph = base.validate_baseline_artifact(record, artifact)
        state = base.load_bound_reference(record)
        base.validate_baseline_trajectory_identity(artifact, state, baseline_graph)
        tracks, _track_metadata, track_paths = constant.reconstruct_tracks(
            record,
            state,
            baseline_candidate,
        )
        original_factors = read_json(artifact / "local_motion_factors.json")
        original_stereo = read_json(artifact / "shared_stereo_observations.json")
        constant_artifact, constant_candidate, constant_paths = validate_constant_artifact(
            record,
            constant_root,
        )
        combined_artifact, combined_candidate, combined_paths = validate_combined_artifact(
            record,
            combined_root,
        )
        (
            joint_rotations,
            joint_candidate,
            joint_paths,
            joint_gauge_report,
        ) = validate_joint_rotation_artifact(
            record,
            joint_root,
            state,
        )
        eye_candidates, eye_reports, raw_report_paths = physical.load_all_eye_candidates(
            record,
            baseline_candidate,
            state.times,
        )
        source_paths = [
            artifact / "candidate_manifest.json",
            artifact / "graph_report.json",
            artifact / "local_motion_factors.json",
            artifact / "shared_stereo_observations.json",
            artifact / "body_trajectory_fused.csv",
            *constant_paths,
            *combined_paths,
            *joint_paths,
            *track_paths,
            *raw_report_paths,
        ]
        source_hashes = base.snapshot_hashes(source_paths)
        expected_input_hashes = merge_expected_input_hashes(
            ("baseline candidate", baseline_candidate),
            ("constant candidate", constant_candidate),
            ("combined candidate", combined_candidate),
            ("joint candidate", joint_candidate),
        )
        guarded_hashes = {**source_hashes, **expected_input_hashes, **frozen_hashes}
        check_frozen(guarded_hashes)
    except base.StopCodeChanged:
        raise
    except Exception as error:
        for variant in VARIANTS:
            variant_dir = output / record_id / variant
            variant_dir.mkdir(parents=True, exist_ok=True)
            result["variants"][variant] = run_variant_failure(variant_dir, error, "source_validation")
        result["status"] = "INCOMPLETE_VARIANTS"
        return result

    original_dir = output / record_id / ORIGINAL_VARIANT
    feedback_dir = output / record_id / FEEDBACK_VARIANT
    original_dir.mkdir(parents=True)
    try:
        original_rotations = state.rotations.as_matrix()
        original_motion, original_motion_report = transform_motion_factors(
            state,
            tracks,
            original_factors,
            original_rotations,
        )
        expected_constant = read_json(constant_artifact / "local_motion_factors.json")
        constant_identity = _compare_vec3_lists(
            original_motion,
            expected_constant,
            "metric_displacement_world_m",
            "constant_recomputed_motion_factor",
        )
        original_stereo_rows, original_stereo_report = transform_shared_rows_with_rotations(
            state,
            original_rotations,
            eye_candidates,
            original_stereo,
        )
        original_stereo_report["eye_candidate_reports"] = eye_reports
        original_stereo_report["constant_recomputed_motion_factor"] = constant_identity
        original_stereo_report["constant_transform_report"] = original_motion_report
        original_result = run_solver_arm(
            record,
            artifact,
            ORIGINAL_VARIANT,
            original_dir,
            state,
            baseline_candidate,
            original_stereo_rows,
            original_stereo_report,
            raw_report_paths,
            original_motion,
            "constant_ir_gauge_recomputed_original_rotation",
            [*constant_paths, *combined_paths, *track_paths, *joint_paths],
            guarded_hashes,
        )
        control_identity = validate_control_identity(
            original_dir,
            combined_artifact,
            original_motion,
            original_stereo_rows,
        )
        augment_candidate_manifest(
            original_dir,
            variant=ORIGINAL_VARIANT,
            arm_label="control",
            reference_rotation_source="original_vins_body_rotations",
            joint_candidate=joint_candidate,
            joint_gauge_report=joint_gauge_report,
            joint_paths=joint_paths,
            control_identity=control_identity,
            extra_paths=[*constant_paths, *combined_paths, *track_paths],
            frozen_hashes=guarded_hashes,
        )
        check_frozen(guarded_hashes)
        result["variants"][ORIGINAL_VARIANT] = {
            **original_result,
            "control_identity": control_identity,
        }
    except base.StopCodeChanged:
        raise
    except Exception as error:
        result["variants"][ORIGINAL_VARIANT] = run_variant_failure(
            original_dir,
            error,
            "original_rotation_control",
        )
        feedback_dir.mkdir(parents=True, exist_ok=True)
        result["variants"][FEEDBACK_VARIANT] = run_variant_failure(
            feedback_dir,
            RuntimeError("original-rotation control failed; feedback skipped"),
            "control_identity",
        )
        result["status"] = "INCOMPLETE_VARIANTS"
        return result

    feedback_dir.mkdir(parents=True)
    try:
        feedback_motion, feedback_motion_report = transform_motion_factors(
            state,
            tracks,
            original_factors,
            joint_rotations,
        )
        feedback_stereo, feedback_stereo_report = transform_shared_rows_with_rotations(
            state,
            joint_rotations,
            eye_candidates,
            original_stereo,
        )
        feedback_stereo_report["eye_candidate_reports"] = eye_reports
        feedback_stereo_report["constant_transform_report"] = feedback_motion_report
        feedback_result = run_solver_arm(
            record,
            artifact,
            FEEDBACK_VARIANT,
            feedback_dir,
            state_with_rotations(state, joint_rotations),
            baseline_candidate,
            feedback_stereo,
            feedback_stereo_report,
            raw_report_paths,
            feedback_motion,
            "constant_ir_gauge_recomputed_joint_rotation_feedback",
            [*constant_paths, *combined_paths, *track_paths, *joint_paths],
            guarded_hashes,
        )
        augment_candidate_manifest(
            feedback_dir,
            variant=FEEDBACK_VARIANT,
            arm_label="staged_feedback",
            reference_rotation_source="joint_stereo_se3_pose_only_rotations",
            joint_candidate=joint_candidate,
            joint_gauge_report=joint_gauge_report,
            joint_paths=joint_paths,
            control_identity=None,
            extra_paths=[*constant_paths, *combined_paths, *track_paths],
            frozen_hashes=guarded_hashes,
        )
        check_frozen(guarded_hashes)
        result["variants"][FEEDBACK_VARIANT] = feedback_result
    except base.StopCodeChanged:
        raise
    except Exception as error:
        result["variants"][FEEDBACK_VARIANT] = run_variant_failure(
            feedback_dir,
            error,
            "staged_joint_rotation_feedback",
        )

    result["status"] = (
        "COMPLETED"
        if all("score" in variant for variant in result["variants"].values())
        else "INCOMPLETE_VARIANTS"
    )
    return result


def aggregate(results: list[dict[str, Any]], dataset_count: int) -> dict[str, Any]:
    return physical.aggregate(results, dataset_count, VARIANTS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--constant-root", type=Path, required=True)
    parser.add_argument("--joint-root", type=Path, required=True)
    parser.add_argument("--combined-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", action="append", default=[])
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink():
        parser.error("output must be new; previous results are never overwritten")

    manifest = read_json(args.manifest)
    records = base.validate_records(manifest, args.dataset)
    baseline_summary = read_json(args.baseline / "summary.json")
    if baseline_summary.get("status") not in ("COMPLETED", "COMPLETED_WITH_FAILURES"):
        raise ValueError(f"baseline summary is not closed: {baseline_summary.get('status')!r}")
    constant_summary = validate_completed_summary(args.constant_root, "constant gauge")
    joint_summary = validate_completed_summary(args.joint_root, "joint SE3")
    combined_summary = validate_completed_summary(args.combined_root, "combined control")
    for label, summary in (
        ("baseline", baseline_summary),
        ("constant gauge", constant_summary),
        ("joint SE3", joint_summary),
        ("combined control", combined_summary),
    ):
        validate_requested_records_in_summary(records, summary, label)
    baseline_by_id = base.baseline_results(baseline_summary)
    missing = {record["id"] for record in records} - set(baseline_by_id)
    if missing:
        raise ValueError(f"baseline summary is missing records: {sorted(missing)}")

    frozen_hashes = base.snapshot_hashes(frozen_code_paths())
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
        "constant_summary_sha256": file_hash(args.constant_root / "summary.json"),
        "joint_summary_sha256": file_hash(args.joint_root / "summary.json"),
        "combined_summary_sha256": file_hash(args.combined_root / "summary.json"),
        "baseline": str(args.baseline.resolve()),
        "constant_root": str(args.constant_root.resolve()),
        "joint_root": str(args.joint_root.resolve()),
        "combined_root": str(args.combined_root.resolve()),
        "variants": VARIANTS,
        "policy_arguments": {
            "experiment": "staged_single_rotation_feedback_not_full_joint_vi",
            "full_vi_solver": "physical.run_solver_variant",
            "original_control_must_byte_match_combined": True,
            "joint_positions_consumed": False,
            "physical_stereo_transform": "body_lever_from_raw_merged_stereo_v1",
            "learned_factor_transform": "constant_ir_fixed_so3_gauge_v1",
            **base.BASELINE_POLICY_ARGUMENTS,
        },
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
                "source_baseline_error": baseline_row.get("error"),
                "variants": {},
            }
        else:
            try:
                result = run_record(
                    record,
                    args.baseline,
                    args.constant_root,
                    args.joint_root,
                    args.combined_root,
                    args.output,
                    frozen_hashes,
                )
            except base.StopCodeChanged:
                summary["status"] = "STOP_CODE_CHANGED"
                write_json(args.output / "summary.json", summary)
                return 2
        result["elapsed_s"] = time.monotonic() - started
        write_json(args.output / record["id"] / "result.json", result)
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
