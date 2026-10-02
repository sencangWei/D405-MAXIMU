#!/usr/bin/env python3
"""Development-only learned-segment gate replay over frozen dual-IR artifacts.

This script consumes an already completed adapter batch.  It does not prepare
frontends, run MASt3R, or select by ground truth.  External reference data is
passed only to the existing scorer after the candidate trajectory is frozen.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
from types import SimpleNamespace
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import fuse_mast3r_dual_ir_symmetric as symmetric  # noqa: E402
import fuse_mast3r_stereo_imu as fusion  # noqa: E402
import run_dual_ir_regression_corpus as corpus  # noqa: E402
from ego_vio.vio.learned_segment_reliability import (  # noqa: E402
    apply_learned_segment_reliability,
)


VARIANT = "selected"
BASELINE_POLICY = "both"
SCHEMA = "umi_learned_segment_gate_development_regression_v1"
BASELINE_POLICY_ARGUMENTS = {
    "max_correction_mm": None,
    "correction_cap_mode": "global",
    "eyes": "both",
    "stereo_weight_policy": "observation",
    "disable_learned_motion": False,
    "learned_motion_consistency_limit_m": None,
    "optional_stereo_policy": "reject_window",
}
POLICY_ARGUMENTS = {
    "source_policy": BASELINE_POLICY,
    "variant": VARIANT,
    "learned_segment_gate": "duration_segment_reliability_v1",
    **BASELINE_POLICY_ARGUMENTS,
}
ROTATION_SERIALIZATION_TOLERANCE_RAD = 5e-9
ANCHOR_POSITION_SERIALIZATION_TOLERANCE_M = 1e-6


class StopCodeChanged(RuntimeError):
    pass


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def frozen_code_paths() -> list[Path]:
    """Current corpus freeze set plus this development adapter and gate module."""
    return [
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
        Path(__file__),
        ROOT / "ego_vio/vio/learned_segment_reliability.py",
    ]


def snapshot_hashes(paths: list[Path]) -> dict[str, str]:
    return {str(path.resolve()): file_hash(path) for path in paths}


def code_changed(frozen_hashes: dict[str, str]) -> bool:
    for path, digest in frozen_hashes.items():
        if file_hash(Path(path)) != digest:
            return True
    return False


def validate_records(manifest: dict[str, Any], datasets: list[str]) -> list[dict[str, Any]]:
    records = list(manifest.get("records", []))
    ids = [record.get("id") for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate manifest record id")
    if datasets:
        selected = set(datasets)
        missing = selected - set(ids)
        if missing:
            raise ValueError(f"unknown dataset id: {sorted(missing)}")
        records = [record for record in records if record["id"] in selected]
    return records


def baseline_results(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = list(summary.get("results", []))
    ids = [row.get("id") for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate baseline result id")
    return {row["id"]: row for row in rows}


def require_onboard_report(report: dict[str, Any], label: str) -> None:
    if report.get("external_ground_truth_used") is not False:
        raise ValueError(f"{label} used ground truth")
    if report.get("slam_supervision") is not False:
        raise ValueError(f"{label} used slam supervision")


def validate_baseline_contract(candidate: dict[str, Any], graph: dict[str, Any]) -> None:
    if candidate.get("schema") != "umi_dual_ir_symmetric_experiment_v1":
        raise ValueError("baseline candidate schema mismatch")
    if graph.get("schema") != "umi_dual_ir_symmetric_graph_diagnostic_v1":
        raise ValueError("baseline graph schema mismatch")
    if candidate.get("shared_gauge") != "VINS_body_world_first_node_only":
        raise ValueError("baseline shared gauge mismatch")
    if candidate.get("primary_eye") is not None:
        raise ValueError("baseline primary eye must be None")
    if candidate.get("shared_scale_state") is not False:
        raise ValueError("baseline shared scale state must be False")
    body_t_camera = np.asarray(candidate.get("body_t_camera_in_solver"), dtype=float)
    if body_t_camera.shape != (4, 4) or not np.allclose(body_t_camera, np.eye(4), atol=1e-12):
        raise ValueError("baseline body_t_camera_in_solver must be identity")


def validate_record_sources(record: dict[str, Any]) -> None:
    for key in ("session", "capture_dir", "vins_dir", "reference_manifest"):
        if not record.get(key):
            raise ValueError(f"record missing {key}")
    session = Path(record["session"])
    vins_dir = Path(record["vins_dir"])
    required = [
        session / "d405_frames.csv",
        session / "external_imu/imu.bin",
        vins_dir / "vio_corrected_stream.csv",
        vins_dir / "run_acceptance.json",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError(f"missing record inputs: {missing[:3]}")


def required_baseline_input_paths(record: dict[str, Any]) -> list[Path]:
    session = Path(record["session"])
    vins_dir = Path(record["vins_dir"])
    return [
        vins_dir / "vio_corrected_stream.csv",
        vins_dir / "run_acceptance.json",
        session / "d405_frames.csv",
        session / "external_imu/imu.bin",
        symmetric.VINS_CONFIG,
        symmetric.IMU_CONFIG,
    ]


def validate_input_hashes(candidate: dict[str, Any], record: dict[str, Any]) -> None:
    input_hashes = candidate.get("input_sha256")
    if not isinstance(input_hashes, dict) or not input_hashes:
        raise ValueError("baseline candidate input_sha256 is missing")
    for path in required_baseline_input_paths(record):
        resolved = str(path.resolve())
        if resolved not in input_hashes:
            raise ValueError(f"baseline candidate input hash missing: {resolved}")
        if not path.is_file() or file_hash(path) != input_hashes[resolved]:
            raise ValueError(f"baseline candidate input hash changed: {resolved}")
    for source, expected in input_hashes.items():
        source_path = Path(source)
        if not source_path.is_file() or file_hash(source_path) != expected:
            raise ValueError(f"baseline input hash changed: {source}")


def validate_baseline_artifact(record: dict[str, Any], artifact: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    paths = {
        "candidate": artifact / "candidate_manifest.json",
        "graph": artifact / "graph_report.json",
        "factors": artifact / "local_motion_factors.json",
        "stereo": artifact / "shared_stereo_observations.json",
        "estimate": artifact / "body_trajectory_fused.csv",
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise ValueError(f"missing baseline artifacts: {missing[:3]}")
    candidate = read_json(paths["candidate"])
    graph = read_json(paths["graph"])
    validate_baseline_contract(candidate, graph)
    require_onboard_report(candidate, "baseline candidate")
    require_onboard_report(graph, "baseline graph")
    if Path(candidate.get("session", "")).resolve() != Path(record["session"]).resolve():
        raise ValueError("baseline candidate session mismatch")
    if graph.get("output_frame") != "body_imu_origin":
        raise ValueError("baseline graph output is not body_imu_origin")
    if candidate.get("policy_arguments") != BASELINE_POLICY_ARGUMENTS:
        raise ValueError("baseline policy arguments are not the frozen adapter-v2 policy")
    if graph.get("policy_arguments") != candidate.get("policy_arguments"):
        raise ValueError("baseline graph/candidate policy mismatch")
    validate_input_hashes(candidate, record)
    return candidate, graph


def baseline_artifact_dir(baseline: Path, record_id: str) -> Path:
    return baseline / record_id / BASELINE_POLICY


def load_bound_reference(record: dict[str, Any]):
    session = Path(record["session"])
    vins_dir = Path(record["vins_dir"])
    config = fusion.load_vins_config(symmetric.VINS_CONFIG, -0.009109323)
    imu_times, gyro, accel, imu_info = fusion.load_calibrated_imu(
        session / "external_imu/imu.bin", symmetric.IMU_CONFIG
    )
    vins_path = vins_dir / "vio_corrected_stream.csv"
    vins_report_path = vins_dir / "run_acceptance.json"
    times, positions, rotations, rows = fusion.load_trajectory(vins_path)
    source_quality = fusion.validate_relative_motion_report(
        fusion.load_json_report(vins_report_path),
        vins_report_path,
        vins_path,
        session,
        len(rows),
    )
    times, positions, rotations, rows, mono, binding = symmetric.bind_body_reference(
        session / "d405_frames.csv", times, positions, rotations, rows
    )
    return SimpleNamespace(
        config=config,
        imu_times=imu_times,
        gyro=gyro,
        accel=accel,
        imu_info=imu_info,
        times=times,
        positions=positions,
        rotations=rotations,
        rows=rows,
        mono=mono,
        binding=binding,
        source_quality=source_quality,
    )


def validate_baseline_trajectory_identity(artifact: Path, state: SimpleNamespace, graph: dict[str, Any]) -> None:
    if graph.get("output_samples") != len(state.rows):
        raise ValueError("baseline graph output sample count does not match bound reference")
    times, positions, rotations, rows = fusion.load_trajectory(
        artifact / "body_trajectory_fused.csv"
    )
    if len(times) != len(state.times) or len(rows) != len(state.rows):
        raise ValueError("baseline estimate sample count does not match bound reference")
    if np.max(np.abs(times - state.times)) > 1e-6:
        raise ValueError("baseline estimate timestamps do not match bound reference")
    rotation_error = (rotations.inv() * state.rotations).magnitude()
    max_rotation_error = float(np.max(rotation_error))
    if max_rotation_error > ROTATION_SERIALIZATION_TOLERANCE_RAD:
        raise ValueError(
            "baseline estimate rotations do not match bound reference "
            f"(max_so3_error_rad={max_rotation_error:.3e})"
        )
    first_position_error = float(np.linalg.norm(positions[0] - state.positions[0]))
    if first_position_error > ANCHOR_POSITION_SERIALIZATION_TOLERANCE_M:
        raise ValueError("baseline estimate first position does not match bound VINS anchor")


def provenance_hashes(
    baseline_candidate: dict[str, Any],
    artifact: Path,
) -> dict[str, str]:
    hashes = dict(baseline_candidate.get("input_sha256", {}))
    for path in (
        artifact / "candidate_manifest.json",
        artifact / "graph_report.json",
        artifact / "local_motion_factors.json",
        artifact / "shared_stereo_observations.json",
        artifact / "body_trajectory_fused.csv",
        Path(__file__),
        ROOT / "ego_vio/vio/learned_segment_reliability.py",
    ):
        hashes[str(path.resolve())] = file_hash(path)
    return hashes


def run_record(record: dict[str, Any], baseline: Path, output: Path, frozen_hashes: dict[str, str]) -> dict[str, Any]:
    record_id = record["id"]
    artifact = baseline_artifact_dir(baseline, record_id)
    result = {"id": record_id, "status": "IN_PROGRESS", "variants": {}}
    variant_dir = output / record_id / VARIANT
    variant_dir.mkdir(parents=True)
    try:
        validate_record_sources(record)
        baseline_candidate, baseline_graph = validate_baseline_artifact(record, artifact)
        state = load_bound_reference(record)
        validate_baseline_trajectory_identity(artifact, state, baseline_graph)
        motion_factors = read_json(artifact / "local_motion_factors.json")
        shared_stereo = read_json(artifact / "shared_stereo_observations.json")
        gated_factors, gate_report = apply_learned_segment_reliability(
            state.times,
            state.rotations.as_matrix(),
            motion_factors,
            shared_stereo,
        )
        write_json(variant_dir / "local_motion_factors.json", gated_factors)
        write_json(variant_dir / "shared_stereo_observations.json", shared_stereo)
        write_json(variant_dir / "learned_segment_reliability_report.json", gate_report)
        estimate = variant_dir / "body_trajectory_fused.csv"
        if int(gate_report.get("zeroed_factor_count", 0)) == 0:
            shutil.copy2(artifact / "body_trajectory_fused.csv", estimate)
            graph_solver = dict(baseline_graph.get("joint_position_solver", {}))
            graph_solver["learned_segment_reliability"] = gate_report
            replay_mode = "copied_baseline_no_gated_factors"
        else:
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
                secondary_visual_factors=gated_factors,
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
            graph_solver["learned_segment_reliability"] = gate_report
            fusion.write_trajectory(estimate, state.rows, refined, state.rotations)
            replay_mode = "refined_with_gated_learned_motion"
        estimate_sha = file_hash(estimate)
        candidate = {
            "schema": "umi_learned_segment_gate_candidate_v1",
            "status": "EXPERIMENTAL_NOT_ACCEPTED",
            "accepted": False,
            "external_ground_truth_used": False,
            "slam_supervision": False,
            "session": str(Path(record["session"]).resolve()),
            "source_baseline_artifact": str(artifact.resolve()),
            "source_candidate_manifest": str((artifact / "candidate_manifest.json").resolve()),
            "input_sha256": provenance_hashes(baseline_candidate, artifact),
            "output_estimate_sha256": estimate_sha,
            "bound_samples": len(state.rows),
            "baseline_policy_arguments": baseline_candidate["policy_arguments"],
            "policy_arguments": dict(POLICY_ARGUMENTS),
            "gate_rule": gate_report.get("rule"),
            "vins_source_validation": state.source_quality,
            "reference_time_binding": state.binding,
            "imu": state.imu_info,
            "replay_mode": replay_mode,
        }
        write_json(variant_dir / "candidate_manifest.json", candidate)
        graph_report = {
            "schema": "umi_learned_segment_gate_graph_diagnostic_v1",
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
            raise StopCodeChanged("STOP_CODE_CHANGED before scoring")
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
            raise StopCodeChanged("STOP_CODE_CHANGED after scoring")
        if file_hash(estimate) != estimate_sha:
            raise RuntimeError("estimate changed during scoring")
        result["variants"][VARIANT] = {
            "artifact_dir": str(variant_dir.resolve()),
            "score": score,
            "estimate_sha256": estimate_sha,
            "replay_mode": replay_mode,
            "gate": {
                "zeroed_factor_count": gate_report.get("zeroed_factor_count"),
                "affected_pair_count": gate_report.get("affected_pair_count"),
                "hit_window_count": gate_report.get("hit_window_count"),
            },
        }
        result["status"] = "COMPLETED"
    except StopCodeChanged:
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
    scores = [
        result.get("variants", {}).get(VARIANT, {}).get("score")
        for result in results
        if result.get("variants", {}).get(VARIANT, {}).get("score") is not None
    ]
    return {
        f"{VARIANT}/none": {
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
    }


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
    records = validate_records(manifest, args.dataset)
    baseline_summary = read_json(args.baseline / "summary.json")
    baseline_by_id = baseline_results(baseline_summary)
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
            except StopCodeChanged:
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
