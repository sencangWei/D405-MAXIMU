#!/usr/bin/env python3
"""Isolated pose-only SE(3) pilot; same-data fixed-R and joint-R controls.

This is not the production visual-inertial graph: acceleration, velocity and
gravity states are deliberately absent. No Tracker information enters the
solver. Existing frozen scoring is called only after an estimate is frozen.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import run_physical_stereo_lever_probe as physical
from ego_vio.vio.joint_stereo_se3 import solve_joint_stereo_se3
from ego_vio.vio.stereo_se3_factors import build_shared_stereo_se3_factors

base, fusion, corpus = physical.base, physical.fusion, physical.corpus
VARIANTS = ("fixed_rotation", "joint_rotation_position")
MISSING_PHYSICS = ["acceleration", "velocity", "gravity"]


def verify_factor_proof(record, baseline_dir, proof_root):
    """Bind baseline factor files to the completed reconstruction census."""
    proof_path = proof_root / record["id"] / "selected/candidate_manifest.json"
    proof = base.read_json(proof_path)
    base.require_onboard_report(proof, "source-factor reconstruction proof")
    if Path(proof.get("session", "")).resolve() != Path(record["session"]).resolve():
        raise ValueError("factor reconstruction proof session mismatch")
    hashes = proof.get("input_sha256", {})
    paths = [baseline_dir / "local_motion_factors.json",
             baseline_dir / "shared_stereo_observations.json"]
    for path in paths:
        if hashes.get(str(path.resolve())) != base.file_hash(path):
            raise ValueError(f"baseline factors changed since reconstruction: {path}")
    factors = base.read_json(paths[0])
    identity = proof.get("source_factor_reconstruction", {})
    if (identity.get("factor_count") != len(factors)
            or identity.get("max_metric_displacement_abs_error_m") != 0.0):
        raise ValueError("factor proof does not establish exact reconstruction")
    return factors, proof_path


def solver_stereo_factors(times, candidates, original_rows):
    normalized = []
    for item in candidates:
        extrinsic = np.asarray(item["body_t_camera"], dtype=float)
        normalized.append({
            **item,
            "first_index": item["reference_first_index"],
            "second_index": item["reference_second_index"],
            "body_R_camera": extrinsic[:3, :3].tolist(),
            "body_t_camera_m": extrinsic[:3, 3].tolist(),
            "external_ground_truth_used": False,
            "slam_supervision": False,
        })
    selected, report = build_shared_stereo_se3_factors(times, normalized, original_rows)
    result = []
    for item in selected:
        extrinsic = np.eye(4)
        extrinsic[:3, 3] = item["virtual_body_t_camera_m"]
        result.append({
            **item,
            "body_t_camera": extrinsic.tolist(),
            "rotation_camera_j_from_i": item["rotation_body_j_from_i_matrix"],
            "metric_displacement_camera_i_m": item["metric_displacement_body_i_m"],
            "rotation_sigma_rad": float(np.deg2rad(1.0)),
        })
    return result, report


def configured_gyro_noise():
    storage = cv2.FileStorage(str(corpus.VINS_CONFIG), cv2.FILE_STORAGE_READ)
    try:
        density = float(storage.getNode("gyr_n").real())
    finally:
        storage.release()
    if not np.isfinite(density) or density <= 0.0:
        raise ValueError("formal gyroscope noise density is invalid")
    return density


def gyro_factors(state, noise_density, imu_source_path):
    """Integrate real IMU samples, including verified camera-only gaps."""
    if not np.isfinite(noise_density) or noise_density <= 0:
        raise ValueError("invalid gyro noise density")
    imu_times = np.asarray(state.imu_times, dtype=float)
    source_hash = base.file_hash(Path(imu_source_path))
    if (imu_times.size < 2 or np.any(np.diff(imu_times) <= 0.0)
            or not np.all(np.isfinite(imu_times))
            or np.asarray(state.gyro).shape != (imu_times.size, 3)
            or not np.all(np.isfinite(state.gyro))):
        raise ValueError("invalid calibrated gyro stream")
    query = np.asarray(state.mono, dtype=float) + state.config["td_s"]
    if (len(query) != len(state.times) or np.any(np.diff(query) <= 0)
            or not np.all(np.isfinite(query))):
        raise ValueError("camera-to-monotonic binding is invalid")
    factors, gap_max = [], 0.0
    for first in range(len(query) - 1):
        second = first + 1
        start, end = float(query[first]), float(query[second])
        if not imu_times[0] <= start < end <= imu_times[-1]:
            raise ValueError("gyro factor lies outside physical IMU coverage")
        lo = max(0, int(np.searchsorted(imu_times, start, side="right")) - 1)
        hi = min(len(imu_times) - 1, int(np.searchsorted(imu_times, end)))
        gap = float(np.max(np.diff(imu_times[lo:hi + 1])))
        if gap > 0.010:
            raise ValueError("gyro factor crosses an unobserved IMU interval")
        gap_max = max(gap_max, gap)
        delta = fusion.integrate_gyro(imu_times, state.gyro, start, end)
        factors.append({
            "first_index": first, "second_index": second,
            "delta_rotation_body_i_to_body_j": delta.as_matrix().tolist(),
            "confidence": 1.0,
            "imu_coverage_verified": True,
            "imu_sample_count": hi - lo + 1,
            "imu_source_sha256": source_hash,
            "max_imu_sample_gap_s": gap,
            "rotation_sigma_rad": float(noise_density * np.sqrt(end - start)),
        })
    return factors, {
        "gyro_noise_density_rad_per_sqrt_s": noise_density,
        "noise_source": str(corpus.VINS_CONFIG),
        "td_s": state.config["td_s"], "imu_factor_count": len(factors),
        "max_observed_imu_gap_s": gap_max,
        "camera_only_gap_count": int(np.count_nonzero(np.diff(state.times) > 0.050)),
        "policy": "10ms_max_physical_imu_gap_no_missing_sample_interpolation",
    }


def check_frozen(hashes):
    if base.code_changed(hashes):
        raise base.StopCodeChanged("STOP_CODE_CHANGED")


def validate_solution(solution, state):
    positions = np.asarray(solution["positions"], dtype=float)
    rotations = np.asarray(solution["rotations"], dtype=float)
    if (not np.array_equal(solution["times"], state.times)
            or positions.shape != state.positions.shape
            or not np.all(np.isfinite(positions))
            or rotations.shape != (len(state.times), 3, 3)
            or not np.all(np.isfinite(rotations))):
        raise ValueError("solver changed timeline or returned invalid poses")
    if (not np.allclose(rotations @ rotations.transpose(0, 2, 1), np.eye(3), atol=1e-8, rtol=0)
            or not np.allclose(np.linalg.det(rotations), 1., atol=1e-8, rtol=0)):
        raise ValueError("solver rotations are not SO3")
    if (not np.array_equal(positions[0], state.positions[0])
            or not np.allclose(rotations[0], state.rotations.as_matrix()[0], atol=1e-12, rtol=0)):
        raise ValueError("solver changed the fixed first-pose gauge")


def run_record(record, baseline, proof_root, output, hashes):
    rid = record["id"]
    artifact = baseline / rid / "both"
    result = {"id": rid, "status": "IN_PROGRESS", "variants": {}}
    try:
        base.validate_record_sources(record)
        candidate, graph = base.validate_baseline_artifact(record, artifact)
        state = base.load_bound_reference(record)
        base.validate_baseline_trajectory_identity(artifact, state, graph)
        learned, proof_path = verify_factor_proof(record, artifact, proof_root)
        original_stereo = base.read_json(artifact / "shared_stereo_observations.json")
        eye_candidates, eye_report, source_paths = physical.load_all_eye_candidates(
            record, candidate, state.times)
        # Reuse current row/count/confidence guards before adding rotations.
        physical.transform_shared_rows(state, eye_candidates, original_stereo)
        stereo, stereo_report = solver_stereo_factors(
            state.times, eye_candidates, original_stereo)
        gyro, gyro_report = gyro_factors(state, configured_gyro_noise(),
            Path(record["session"]) / "external_imu/imu.bin")
        paths = [artifact / "candidate_manifest.json", artifact / "graph_report.json",
                 artifact / "local_motion_factors.json",
                 artifact / "shared_stereo_observations.json", proof_path, *source_paths]
        source_hashes = base.snapshot_hashes(paths)
    except Exception as error:
        if isinstance(error, base.StopCodeChanged):
            raise
        for variant in VARIANTS:
            result["variants"][variant] = {"error_code": "SOURCE_FAILED", "error": str(error)}
        result["status"] = "INCOMPLETE_VARIANTS"
        return result

    for variant in VARIANTS:
        directory = output / rid / variant
        directory.mkdir(parents=True)
        try:
            check_frozen({**hashes, **source_hashes})
            base.write_json(directory / "stereo_se3_factors.json", stereo)
            base.write_json(directory / "gyro_factors.json", gyro)
            solution = solve_joint_stereo_se3(
                state.times, state.positions, state.rotations.as_matrix(), stereo, gyro,
                learned, optimize_rotations=(variant == "joint_rotation_position"))
            diag = solution["diagnostic"]
            base.write_json(directory / "solver_diagnostic.json", diag)
            if not diag["least_squares_success"]:
                raise ValueError("joint pose solver did not converge; no scored fallback")
            validate_solution(solution, state)
            estimate = directory / "body_trajectory_fused.csv"
            fusion.write_trajectory(estimate, state.rows, solution["positions"],
                                    Rotation.from_matrix(solution["rotations"]))
            estimate_sha = base.file_hash(estimate)
            base.write_json(directory / "candidate_manifest.json", {
                "schema": "umi_joint_stereo_se3_pose_only_candidate_v1",
                "status": "EXPERIMENTAL_NOT_ACCEPTED", "accepted": False,
                "external_ground_truth_used": False, "slam_supervision": False,
                "session": str(Path(record["session"]).resolve()),
                "variant": variant, "missing_physics": MISSING_PHYSICS,
                "input_sha256": {**candidate["input_sha256"], **source_hashes, **hashes},
                "output_estimate_sha256": estimate_sha,
                "bound_samples": len(state.times), "eye_report": eye_report,
                "stereo_report": stereo_report, "gyro_report": gyro_report,
                "solver_diagnostic": diag,
            })
            check_frozen({**hashes, **source_hashes})
            score = corpus.score_frozen(record, estimate, directory / "score",
                                        directory.parent, f"score_{rid}_{variant}")
            check_frozen({**hashes, **source_hashes})
            if base.file_hash(estimate) != estimate_sha:
                raise ValueError("estimate changed during scoring")
            result["variants"][variant] = {"artifact_dir": str(directory.resolve()),
                "score": score, "estimate_sha256": estimate_sha, "solver_diagnostic": diag}
        except base.StopCodeChanged:
            raise
        except Exception as error:
            result["variants"][variant] = {"error_code": "VARIANT_FAILED", "error": str(error),
                                           "artifact_dir": str(directory.resolve())}
        base.write_json(output / rid / "result.json", result)
    result["status"] = ("COMPLETED" if all("score" in value for value in result["variants"].values())
                        else "INCOMPLETE_VARIANTS")
    base.write_json(output / rid / "result.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "baseline", "factor-proof", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--dataset", action="append", default=[])
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink():
        parser.error("output must be new; evidence is never overwritten")
    records = base.validate_records(base.read_json(args.manifest), args.dataset)
    baseline_rows = base.baseline_results(base.read_json(args.baseline / "summary.json"))
    proof_summary = base.read_json(args.factor_proof / "summary.json")
    if proof_summary.get("status") not in ("COMPLETED", "COMPLETED_WITH_FAILURES"):
        raise ValueError("factor reconstruction proof must be a completed batch")
    if not {r["id"] for r in records} <= set(baseline_rows):
        raise ValueError("baseline missing requested records")
    paths = physical.frozen_code_paths() + [Path(__file__),
        ROOT / "ego_vio/vio/joint_stereo_se3.py", ROOT / "ego_vio/vio/stereo_se3_factors.py"]
    hashes = base.snapshot_hashes(paths)
    args.output.mkdir(parents=True)
    summary = {"schema": "umi_joint_stereo_se3_pose_only_regression_v1", "status": "RUNNING",
        "development_only": True, "production_promoted": False, "blind_test": False,
        "dataset_count": len(records), "completed_count": 0, "variants": list(VARIANTS),
        "manifest_sha256": base.file_hash(args.manifest), "code_sha256": hashes,
        "baseline": str(args.baseline.resolve()), "factor_proof": str(args.factor_proof.resolve()),
        "missing_physics": MISSING_PHYSICS, "results": []}
    base.write_json(args.output / "summary.json", summary)
    for record in records:
        started = time.monotonic()
        try:
            check_frozen(hashes)
            baseline_row = baseline_rows[record["id"]]
            if baseline_row["status"] != "COMPLETED":
                result = {"id": record["id"], "status": baseline_row["status"], "variants": {},
                          "source_baseline_error": baseline_row.get("error")}
            else:
                result = run_record(record, args.baseline, args.factor_proof, args.output, hashes)
        except base.StopCodeChanged:
            summary["status"] = "STOP_CODE_CHANGED"
            base.write_json(args.output / "summary.json", summary)
            return 2
        result["elapsed_s"] = time.monotonic() - started
        base.write_json(args.output / record["id"] / "result.json", result)
        summary["results"].append(result)
        summary["completed_count"] += 1
        summary["aggregates"] = physical.aggregate(summary["results"], len(records), list(VARIANTS))
        base.write_json(args.output / "summary.json", summary)
    summary["status"] = ("COMPLETED" if all(r["status"] == "COMPLETED" for r in summary["results"])
                         else "COMPLETED_WITH_FAILURES")
    base.write_json(args.output / "summary.json", summary)
    return 0 if summary["status"] == "COMPLETED" else 3


if __name__ == "__main__":
    raise SystemExit(main())
