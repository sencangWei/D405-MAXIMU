#!/usr/bin/env python3
"""Development-only physical stereo lever replay over frozen dual-IR artifacts.

This runner consumes an already completed adapter-v2 batch.  It rebuilds only
the shared stereo rows from cached onboard stereo reports and physical
body-camera levers, then replays the existing body-frame fusion/scoring path.
It does not run frontends, MASt3R, or any selector that sees ground truth.
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
from ego_vio.vio.dual_ir_factors import _interval_has_gap, _nearest_index  # noqa: E402


BASELINE_POLICY = "both"
PHYSICAL_VARIANT = "physical_stereo_only"
COMBINED_VARIANT = "physical_stereo_constant_gauge"
SCHEMA = "umi_physical_stereo_lever_development_regression_v1"
TRANSFORM_MODULE = ROOT / "ego_vio/vio/physical_stereo_lever.py"
BASE_RUNNER_MODULE = ROOT / "scripts/run_learned_segment_probe.py"
CONSTANT_RUNNER_MODULE = ROOT / "scripts/run_constant_ir_gauge_probe.py"
OPTIONAL_STEREO_POLICY = "reject_window"


def transform_shared_stereo_body_lever(
    reference_times,
    reference_rotations,
    eye_candidates,
    original_shared_rows,
):
    from ego_vio.vio.physical_stereo_lever import (  # noqa: PLC0415
        transform_shared_stereo_body_lever as transform,
    )

    return transform(
        reference_times,
        reference_rotations,
        eye_candidates,
        original_shared_rows,
    )


def frozen_code_paths() -> list[Path]:
    return [
        ROOT / "scripts/run_dual_ir_regression_corpus.py",
        ROOT / "scripts/fuse_mast3r_stereo_imu.py",
        ROOT / "scripts/fuse_mast3r_dual_ir_symmetric.py",
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


def baseline_artifact_dir(baseline: Path, record_id: str) -> Path:
    return baseline / record_id / BASELINE_POLICY


def read_json(path: Path) -> Any:
    return base.read_json(path)


def write_json(path: Path, value: Any) -> None:
    base.write_json(path, value)


def file_hash(path: Path) -> str:
    return base.file_hash(path)


def snapshot_hashes(paths: list[Path]) -> dict[str, str]:
    return base.snapshot_hashes(paths)


def code_changed(frozen_hashes: dict[str, str]) -> bool:
    return base.code_changed(frozen_hashes)


def eye_report_names(eye: str) -> list[str]:
    if eye == "left":
        return ["stereo_scale_bidirectional_report.json"] + [
            f"stereo_scale_{kind}_report.json"
            for kind in ("long_hops", "dense10hz", "multisecond")
        ]
    if eye == "right":
        return ["stereo_scale_right_report.json"] + [
            f"stereo_scale_{kind}_right_report.json"
            for kind in ("long_hops", "dense10hz", "multisecond")
        ]
    raise ValueError(f"unknown eye: {eye}")


def eye_report_paths_from_baseline(candidate: dict[str, Any], eye: str) -> list[Path]:
    by_name: dict[str, Path] = {}
    names = eye_report_names(eye)
    expected = set(names)
    for source in candidate.get("input_sha256", {}):
        path = Path(source)
        if path.name in expected:
            if path.name in by_name:
                raise ValueError(f"duplicate {eye} raw stereo report binding: {path.name}")
            by_name[path.name] = path
    missing = [name for name in names if name not in by_name]
    if missing:
        raise ValueError(f"missing {eye} raw stereo report binding: {missing}")
    return [by_name[name] for name in names]


def eye_trajectory_name(eye: str) -> str:
    if eye == "left":
        return "trajectory_imu_metric.csv"
    if eye == "right":
        return "imu_metric_trajectory.csv"
    raise ValueError(f"unknown eye: {eye}")


def eye_trajectory_path_from_baseline(candidate: dict[str, Any], eye: str) -> Path:
    name = eye_trajectory_name(eye)
    matches = [
        Path(source)
        for source in candidate.get("input_sha256", {})
        if Path(source).name == name
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one {eye} metric trajectory binding, found {len(matches)}")
    return matches[0]


def validate_source_hashes(candidate: dict[str, Any], paths: list[Path]) -> dict[str, str]:
    input_hashes = candidate.get("input_sha256", {})
    source_hashes = {}
    for path in paths:
        resolved = str(path.resolve())
        if resolved not in input_hashes:
            raise ValueError(f"raw stereo report not bound in baseline candidate: {resolved}")
        digest = file_hash(path)
        if digest != input_hashes[resolved]:
            raise ValueError(f"raw stereo report hash changed: {resolved}")
        source_hashes[resolved] = digest
    return source_hashes


def validate_eye_metadata(candidate: dict[str, Any], eye: str) -> dict[str, Any]:
    metadata = candidate.get("eye_reports", {}).get(eye)
    if not isinstance(metadata, dict):
        raise ValueError(f"baseline candidate missing {eye} eye metadata")
    body_t_camera = np.asarray(metadata.get("effective_body_T_camera"), dtype=float)
    if body_t_camera.shape != (4, 4) or not np.all(np.isfinite(body_t_camera)):
        raise ValueError(f"{eye} effective_body_T_camera is invalid")
    calibration = metadata.get("factory_stereo_calibration")
    if not isinstance(calibration, dict):
        raise ValueError(f"{eye} factory stereo calibration is missing")
    return {
        "effective_body_T_camera": body_t_camera.tolist(),
        "factory_stereo_calibration": calibration,
    }


def load_eye_candidates(
    record: dict[str, Any],
    eye: str,
    baseline_candidate: dict[str, Any],
    reference_times,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[Path]]:
    session = Path(record["session"])
    paths = eye_report_paths_from_baseline(baseline_candidate, eye)
    trajectory_path = eye_trajectory_path_from_baseline(baseline_candidate, eye)
    validate_source_hashes(baseline_candidate, [trajectory_path, *paths])
    trajectory_times, _positions, _rotations, _rows = fusion.load_trajectory(trajectory_path)
    reports = []
    frame = f"infrared_{eye}_camera_i"
    for index, path in enumerate(paths):
        report = fusion.load_json_report(path)
        if index == 0:
            fusion.validate_onboard_report(report, path, "umi_mast3r_stereo_scale_v2")
        if Path(report.get("session", "")).resolve() != session.resolve():
            raise ValueError(f"{eye} stereo report session mismatch")
        if report.get("observation_frame") != frame:
            raise ValueError(f"{eye} stereo report frame mismatch")
        report["report_path"] = str(path.resolve())
        reports.append(report)
    merged = fusion.merge_stereo_reports(
        reports[0],
        reports[1:],
        optional_policy=OPTIONAL_STEREO_POLICY,
    )
    metadata = validate_eye_metadata(baseline_candidate, eye)
    if merged.get("factory_stereo_calibration") != metadata["factory_stereo_calibration"]:
        raise ValueError(f"{eye} factory stereo calibration metadata mismatch")
    reference_scale = float(merged["scale_m_per_mast3r_unit"])
    candidates = []
    skipped: dict[str, int] = {}
    accepted_raw_observations = 0
    for observation in merged.get("observations", []):
        if not observation.get("accepted", False):
            continue
        accepted_raw_observations += 1
        candidate, skip_reason = reference_bound_eye_candidate(
            reference_times,
            trajectory_times,
            eye,
            observation,
            fusion.stereo_observation_confidence(observation, reference_scale),
            metadata["effective_body_T_camera"],
        )
        if skip_reason is None:
            candidates.append(candidate)
        else:
            skipped[skip_reason] = skipped.get(skip_reason, 0) + 1
    validate_no_same_eye_reference_duplicates(candidates)
    report = {
        "schema": "physical_stereo_lever_eye_candidates_v1",
        "eye": eye,
        "candidate_count": len(candidates),
        "accepted_raw_observation_count": accepted_raw_observations,
        "skipped_candidate_counts": skipped,
        "merged_report_paths": merged.get("merged_report_paths", []),
        "merged_report_count": merged.get("merged_report_count"),
        "optional_stereo_policy": OPTIONAL_STEREO_POLICY,
        "optional_report_rejections": merged.get("optional_report_rejections", []),
        "metric_trajectory": str(trajectory_path.resolve()),
        "scale_m_per_mast3r_unit": reference_scale,
        "factory_stereo_calibration": metadata["factory_stereo_calibration"],
        "effective_body_T_camera": metadata["effective_body_T_camera"],
    }
    return candidates, report, [trajectory_path, *paths]


def load_all_eye_candidates(
    record: dict[str, Any],
    baseline_candidate: dict[str, Any],
    reference_times,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[Path]]:
    candidates = []
    reports = {}
    paths = []
    for eye in ("left", "right"):
        eye_candidates, report, eye_paths = load_eye_candidates(
            record,
            eye,
            baseline_candidate,
            reference_times,
        )
        candidates.extend(eye_candidates)
        reports[eye] = report
        paths.extend(eye_paths)
    return candidates, reports, paths


def reference_pair(reference_times, first_t_sec: Any, second_t_sec: Any) -> tuple[int, int]:
    times = np.asarray(reference_times, dtype=float)
    first_time = float(first_t_sec)
    second_time = float(second_t_sec)
    first = _nearest_index(times, first_time)
    second = _nearest_index(times, second_time)
    if first is None or second is None:
        raise ValueError("raw stereo observation does not bind reference times")
    if second <= first:
        raise ValueError("raw stereo observation reference endpoints are not ordered")
    if _interval_has_gap(times, first, second):
        raise ValueError("raw stereo observation reference interval has a gap")
    return int(first), int(second)


def reference_bound_eye_candidate(
    reference_times,
    trajectory_times,
    eye: str,
    observation: dict[str, Any],
    confidence: float,
    body_t_camera: list[list[float]],
) -> tuple[dict[str, Any] | None, str | None]:
    raw_first, raw_second = validate_raw_observation_source(observation, trajectory_times)
    if raw_second <= raw_first:
        raise ValueError("accepted raw stereo observation endpoints are not ordered")
    if _interval_has_gap(np.asarray(trajectory_times, dtype=float), raw_first, raw_second):
        return None, "raw_trajectory_interval_gap"
    if confidence <= 0.0:
        return None, "zero_observation_confidence"
    try:
        first, second = reference_pair(
            reference_times,
            observation.get("first_t_sec"),
            observation.get("second_t_sec"),
        )
    except ValueError as error:
        message = str(error)
        if "does not bind reference times" in message:
            return None, "missing_reference_endpoint"
        if "reference interval has a gap" in message:
            return None, "reference_interval_gap"
        if "reference endpoints are not ordered" in message:
            return None, "reversed_reference_endpoints"
        raise
    return {
        "eye": eye,
        "first_index": raw_first,
        "second_index": raw_second,
        "first_t_sec": observation.get("first_t_sec"),
        "second_t_sec": observation.get("second_t_sec"),
        "metric_displacement_camera_i_m": observation.get(
            "metric_displacement_camera_i_m"
        ),
        "metric_displacement_frame": observation.get("metric_displacement_frame"),
        "observation_confidence": confidence,
        "body_t_camera": body_t_camera,
        "reference_first_index": first,
        "reference_second_index": second,
    }, None


def validate_raw_observation_source(
    observation: dict[str, Any],
    trajectory_times,
) -> tuple[int, int]:
    times = np.asarray(trajectory_times, dtype=float)
    for key in ("first_index", "second_index"):
        value = observation.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
            raise ValueError(f"raw stereo observation has malformed {key}")
    first = int(observation["first_index"])
    second = int(observation["second_index"])
    if first < 0 or second < 0 or first >= len(times) or second >= len(times):
        raise ValueError("raw stereo observation index out of trajectory range")
    first_t = float(observation.get("first_t_sec"))
    second_t = float(observation.get("second_t_sec"))
    if not np.isfinite(first_t) or not np.isfinite(second_t):
        raise ValueError("raw stereo observation timestamp is not finite")
    if (
        abs(float(times[first]) - first_t) > 0.010
        or abs(float(times[second]) - second_t) > 0.010
    ):
        raise ValueError("raw stereo observation index/timestamp mismatch")
    return first, second


def validate_no_same_eye_reference_duplicates(candidates: list[dict[str, Any]]) -> None:
    seen: set[tuple[str, int, int]] = set()
    for candidate in candidates:
        key = (
            str(candidate["eye"]),
            int(candidate["reference_first_index"]),
            int(candidate["reference_second_index"]),
        )
        if key in seen:
            raise ValueError(
                "duplicate same-eye raw stereo candidate for reference pair "
                f"{key}; exact baseline own-confidence dedup metadata is required"
            )
        seen.add(key)


def validate_shared_stereo_identity(
    original_rows: list[dict[str, Any]],
    transformed_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    if len(original_rows) != len(transformed_rows):
        raise ValueError(
            "physical stereo transform changed row count: "
            f"{len(transformed_rows)} != {len(original_rows)}"
        )
    keys = (
        "accepted",
        "first_index",
        "second_index",
        "first_t_sec",
        "second_t_sec",
        "metric_displacement_frame",
        "scale",
        "pnp_inlier_ratio",
        "rotation_error_deg",
    )
    max_vector_delta = 0.0
    for index, (original, transformed) in enumerate(zip(original_rows, transformed_rows)):
        for key in keys:
            if original.get(key) != transformed.get(key):
                raise ValueError(f"physical stereo transform changed row {index} {key}")
        before = np.asarray(original.get("metric_displacement_camera_i_m"), dtype=float)
        after = np.asarray(transformed.get("metric_displacement_camera_i_m"), dtype=float)
        if before.shape != (3,) or after.shape != (3,):
            raise ValueError(f"physical stereo row {index} vector shape invalid")
        if not np.all(np.isfinite(before)) or not np.all(np.isfinite(after)):
            raise ValueError(f"physical stereo row {index} vector is not finite")
        max_vector_delta = max(max_vector_delta, float(np.linalg.norm(after - before)))
    return {
        "schema": "physical_stereo_shared_row_identity_v1",
        "row_count": len(original_rows),
        "max_vector_delta_m": max_vector_delta,
    }


def transform_shared_rows(state, eye_candidates, original_shared_rows):
    transformed_rows, report = transform_shared_stereo_body_lever(
        state.times,
        state.rotations.as_matrix(),
        eye_candidates,
        original_shared_rows,
    )
    identity = validate_shared_stereo_identity(original_shared_rows, transformed_rows)
    identity["transform_report"] = report
    return transformed_rows, identity


def provenance_hashes(
    baseline_candidate: dict[str, Any],
    artifact: Path,
    raw_report_paths: list[Path],
    extra_paths: list[Path],
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
        *raw_report_paths,
        *extra_paths,
    ):
        hashes[str(Path(path).resolve())] = file_hash(Path(path))
    return hashes


def validate_constant_artifact(
    record: dict[str, Any],
    constant_root: Path,
) -> tuple[Path, dict[str, Any], dict[str, Any], list[Path]]:
    artifact = constant_root / record["id"] / "selected"
    paths = [
        artifact / "candidate_manifest.json",
        artifact / "graph_report.json",
        artifact / "local_motion_factors.json",
        artifact / "body_trajectory_fused.csv",
    ]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise ValueError(f"missing constant gauge artifact: {missing[:3]}")
    candidate = read_json(artifact / "candidate_manifest.json")
    graph = read_json(artifact / "graph_report.json")
    base.require_onboard_report(candidate, "constant gauge candidate")
    base.require_onboard_report(graph, "constant gauge graph")
    if candidate.get("schema") != "umi_constant_ir_gauge_candidate_v1":
        raise ValueError("constant gauge candidate schema mismatch")
    if Path(candidate.get("session", "")).resolve() != Path(record["session"]).resolve():
        raise ValueError("constant gauge candidate session mismatch")
    if graph.get("output_frame") != "body_imu_origin":
        raise ValueError("constant gauge graph output mismatch")
    estimate = artifact / "body_trajectory_fused.csv"
    if file_hash(estimate) != candidate.get("output_estimate_sha256"):
        raise ValueError("constant gauge estimate hash mismatch")
    motion_factors = artifact / "local_motion_factors.json"
    expected_motion_hash = candidate.get("output_motion_factors_sha256")
    if not expected_motion_hash:
        raise ValueError("constant gauge motion factor hash missing")
    if file_hash(motion_factors) != expected_motion_hash:
        raise ValueError("constant gauge motion factor hash mismatch")
    input_hashes = candidate.get("input_sha256")
    if not isinstance(input_hashes, dict) or not input_hashes:
        raise ValueError("constant gauge input hashes missing")
    for source, expected in input_hashes.items():
        path = Path(source)
        if not path.is_file() or file_hash(path) != expected:
            raise ValueError(f"constant gauge input hash changed: {source}")
    return artifact, candidate, graph, paths


def run_solver_variant(
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
    extra_provenance_paths: list[Path],
    frozen_hashes: dict[str, str],
) -> dict[str, Any]:
    write_json(variant_dir / "local_motion_factors.json", motion_factors)
    write_json(variant_dir / "shared_stereo_observations.json", transformed_stereo)
    write_json(variant_dir / "physical_stereo_lever_report.json", stereo_report)
    refined, graph_solver = fusion.refine_positions_visual_inertial(
        state.positions,
        state.rotations,
        transformed_stereo,
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
        stereo_factor_confidences=np.asarray([
            float(observation.get("pnp_inlier_ratio", 0.5))
            for observation in transformed_stereo
        ]),
    )
    graph_solver = dict(graph_solver)
    graph_solver["physical_stereo_lever"] = stereo_report
    estimate = variant_dir / "body_trajectory_fused.csv"
    fusion.write_trajectory(estimate, state.rows, refined, state.rotations)
    estimate_sha = file_hash(estimate)
    policy_arguments = {
        "source_policy": BASELINE_POLICY,
        "variant": variant,
        "physical_stereo_transform": "body_lever_from_raw_merged_stereo_v1",
        "motion_factor_source": motion_source,
        **base.BASELINE_POLICY_ARGUMENTS,
    }
    candidate = {
        "schema": "umi_physical_stereo_lever_candidate_v1",
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
            raw_report_paths,
            extra_provenance_paths,
        ),
        "output_estimate_sha256": estimate_sha,
        "bound_samples": len(state.rows),
        "baseline_policy_arguments": baseline_candidate["policy_arguments"],
        "policy_arguments": policy_arguments,
        "eye_candidate_reports": stereo_report.get("eye_candidate_reports"),
        "stereo_transform": stereo_report,
        "vins_source_validation": state.source_quality,
        "reference_time_binding": state.binding,
        "imu": state.imu_info,
    }
    write_json(variant_dir / "candidate_manifest.json", candidate)
    graph_report = {
        "schema": "umi_physical_stereo_lever_graph_diagnostic_v1",
        "status": "EXPERIMENTAL_NOT_ACCEPTED",
        "accepted": False,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "output_frame": "body_imu_origin",
        "output_samples": len(state.rows),
        "td_s": state.config["td_s"],
        "policy_arguments": policy_arguments,
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
        variant_dir.parent,
        f"score_{record['id']}_{variant}",
    )
    if code_changed(frozen_hashes):
        raise base.StopCodeChanged("STOP_CODE_CHANGED after scoring")
    if file_hash(estimate) != estimate_sha:
        raise RuntimeError("estimate changed during scoring")
    return {
        "artifact_dir": str(variant_dir.resolve()),
        "score": score,
        "estimate_sha256": estimate_sha,
        "motion_factor_source": motion_source,
        "stereo_transform": {
            "schema": stereo_report.get("schema"),
            "row_count": stereo_report.get("row_count"),
            "max_vector_delta_m": stereo_report.get("max_vector_delta_m"),
        },
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
    constant_root: Path | None,
    frozen_hashes: dict[str, str],
) -> dict[str, Any]:
    record_id = record["id"]
    artifact = baseline_artifact_dir(baseline, record_id)
    result = {"id": record_id, "status": "IN_PROGRESS", "variants": {}}
    try:
        base.validate_record_sources(record)
        baseline_candidate, baseline_graph = base.validate_baseline_artifact(record, artifact)
        state = base.load_bound_reference(record)
        base.validate_baseline_trajectory_identity(artifact, state, baseline_graph)
        original_factors = read_json(artifact / "local_motion_factors.json")
        original_stereo = read_json(artifact / "shared_stereo_observations.json")
        eye_candidates, eye_reports, raw_report_paths = load_all_eye_candidates(
            record,
            baseline_candidate,
            state.times,
        )
        transformed_stereo, stereo_identity = transform_shared_rows(
            state,
            eye_candidates,
            original_stereo,
        )
        stereo_identity["eye_candidate_reports"] = eye_reports
    except base.StopCodeChanged:
        raise
    except Exception as error:
        for variant in result_variants(constant_root):
            variant_dir = output / record_id / variant
            variant_dir.mkdir(parents=True, exist_ok=True)
            result["variants"][variant] = run_variant_failure(variant_dir, error)
        result["status"] = "INCOMPLETE_VARIANTS"
        return result

    for variant in result_variants(constant_root):
        variant_dir = output / record_id / variant
        variant_dir.mkdir(parents=True)
        try:
            if variant == PHYSICAL_VARIANT:
                motion_factors = original_factors
                motion_source = "baseline_adapter_v2"
                extra_paths = []
            else:
                if constant_root is None:
                    raise ValueError("constant gauge root is required")
                constant_artifact, _constant_candidate, _constant_graph, constant_paths = (
                    validate_constant_artifact(record, constant_root)
                )
                motion_factors = read_json(constant_artifact / "local_motion_factors.json")
                motion_source = "constant_ir_gauge_selected"
                extra_paths = constant_paths
            result["variants"][variant] = run_solver_variant(
                record,
                artifact,
                variant,
                variant_dir,
                state,
                baseline_candidate,
                transformed_stereo,
                stereo_identity,
                raw_report_paths,
                motion_factors,
                motion_source,
                extra_paths,
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


def result_variants(constant_root: Path | None) -> list[str]:
    variants = [PHYSICAL_VARIANT]
    if constant_root is not None:
        variants.append(COMBINED_VARIANT)
    return variants


def aggregate(results: list[dict[str, Any]], dataset_count: int, variants: list[str]) -> dict[str, Any]:
    aggregates = {}
    for variant in variants:
        scores = [
            result.get("variants", {}).get(variant, {}).get("score")
            for result in results
            if result.get("variants", {}).get(variant, {}).get("score") is not None
        ]
        aggregates[f"{variant}/none"] = {
            "dataset_count": dataset_count,
            "scored_count": len(scores),
            "unscored_count": dataset_count - len(scores),
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
    return aggregates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--constant-gauge", type=Path, default=None)
    parser.add_argument("--dataset", action="append", default=[])
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink():
        parser.error("output must be new; previous results are never overwritten")
    variants = result_variants(args.constant_gauge)
    manifest = read_json(args.manifest)
    records = base.validate_records(manifest, args.dataset)
    baseline_summary = read_json(args.baseline / "summary.json")
    baseline_by_id = base.baseline_results(baseline_summary)
    missing = {record["id"] for record in records} - set(baseline_by_id)
    if missing:
        raise ValueError(f"baseline summary is missing records: {sorted(missing)}")
    frozen_paths = frozen_code_paths()
    if args.constant_gauge is not None:
        frozen_paths.append(CONSTANT_RUNNER_MODULE)
    frozen_hashes = snapshot_hashes(frozen_paths)
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
        "constant_gauge": (
            str(args.constant_gauge.resolve()) if args.constant_gauge is not None else None
        ),
        "variants": variants,
        "policy_arguments": {
            "physical_stereo_transform": "body_lever_from_raw_merged_stereo_v1",
            "optional_stereo_policy": OPTIONAL_STEREO_POLICY,
            **base.BASELINE_POLICY_ARGUMENTS,
        },
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
                result = run_record(
                    record,
                    args.baseline,
                    args.output,
                    args.constant_gauge,
                    frozen_hashes,
                )
            except base.StopCodeChanged:
                summary["status"] = "STOP_CODE_CHANGED"
                write_json(args.output / "summary.json", summary)
                return 2
        result["elapsed_s"] = time.monotonic() - started
        summary["results"].append(result)
        summary["completed_count"] = index
        summary["aggregates"] = aggregate(summary["results"], len(records), variants)
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
