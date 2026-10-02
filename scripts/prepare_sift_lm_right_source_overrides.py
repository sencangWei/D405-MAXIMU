#!/usr/bin/env python3
"""Development-only RIGHT source overrides derived from refined LEFT SIFT-LM reports.

This stage does not run MASt3R, the fusion backend, scoring, or any selector.
It consumes the source-only LEFT refinement stage and writes new RIGHT stereo
reports by reusing ``derive_right_ir_stereo_scale`` conversion/scale APIs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import derive_right_ir_stereo_scale as derivation  # noqa: E402
import run_learned_segment_probe as base  # noqa: E402
import run_physical_stereo_lever_probe as physical  # noqa: E402
import evaluate_sift_lm_physical_source_probe as source_eval  # noqa: E402


SCHEMA = "umi_sift_lm_right_source_override_preflight_v1"
BASELINE_POLICY = "both"
RIGHT_POLICY = {
    "name": "derive_right_geometry_from_refined_left_sift_lm",
    "development_only": True,
    "measurement_only": True,
    "external_ground_truth_used": False,
    "slam_supervision": False,
    "right_frontend_policy": "unchanged cached RIGHT trajectory; geometry only is re-expressed from refined LEFT",
    "confidence_policy": "derive_right_ir_stereo_scale native report scale/confidence semantics",
    "shared_factor_policy": "downstream physical dedup keeps one selected geometric factor, not independent double evidence",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def snapshot_paths(paths: list[Path]) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in paths:
        resolved = str(Path(path).resolve())
        if resolved not in snapshot:
            if not Path(path).is_file():
                raise ValueError(f"consumed source path missing: {resolved}")
            snapshot[resolved] = file_hash(Path(path))
    return snapshot


def assert_snapshot_unchanged(before: dict[str, str]) -> None:
    for source, expected in before.items():
        path = Path(source)
        if not path.is_file() or file_hash(path) != expected:
            raise ValueError(f"consumed source changed during derivation: {source}")


def baseline_artifact_dir(baseline: Path, record_id: str) -> Path:
    return baseline / record_id / BASELINE_POLICY


def right_name_for_left(left_name: str) -> str:
    mapping = dict(zip(physical.eye_report_names("left"), physical.eye_report_names("right")))
    try:
        return mapping[left_name]
    except KeyError as error:
        raise ValueError(f"unknown LEFT stereo report name: {left_name}") from error


def ordered_refined_left_paths(stage_record: dict[str, Any]) -> list[Path]:
    by_name = {Path(path).name: Path(path) for path in stage_record["refined_left_sources"]}
    missing = [name for name in physical.eye_report_names("left") if name not in by_name]
    if missing:
        raise ValueError(f"refined LEFT sources missing reports: {missing}")
    return [by_name[name] for name in physical.eye_report_names("left")]


def validate_hash(path: Path, expected: str, label: str) -> str:
    digest = file_hash(path)
    if digest != expected:
        raise ValueError(f"{label} hash changed: {path}")
    return digest


def validate_baseline_bound(candidate: dict[str, Any], paths: list[Path]) -> dict[str, str]:
    bound = candidate.get("input_sha256")
    if not isinstance(bound, dict) or not bound:
        raise ValueError("baseline candidate input_sha256 missing")
    hashes: dict[str, str] = {}
    for path in paths:
        resolved = str(path.resolve())
        if resolved not in bound:
            raise ValueError(f"baseline candidate does not bind source: {resolved}")
        hashes[resolved] = validate_hash(path, bound[resolved], "baseline-bound source")
    return hashes


def validate_refined_left(
    refined_path: Path,
    original_left_path: Path,
    override_sha256: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    resolved = str(refined_path.resolve())
    if resolved not in override_sha256:
        raise ValueError(f"refined LEFT source lacks override hash: {resolved}")
    validate_hash(refined_path, override_sha256[resolved], "refined LEFT source")
    refined = read_json(refined_path)
    original = read_json(original_left_path)
    for key in ("session", "db3", "trajectory", "factory_stereo_calibration"):
        if refined.get(key) != original.get(key):
            raise ValueError(f"refined LEFT report does not preserve {key}: {refined_path}")
    if refined.get("observation_frame") != "infrared_left_camera_i":
        raise ValueError(f"refined source is not LEFT frame: {refined_path}")
    if original.get("observation_frame") != "infrared_left_camera_i":
        raise ValueError(f"original source is not LEFT frame: {original_left_path}")
    return refined, original


def validate_right_factory_calibration(
    refined_left: dict[str, Any],
    original_right: dict[str, Any],
    calibration: dict[str, Any],
    original_right_path: Path,
) -> None:
    left_factory = refined_left.get("factory_stereo_calibration")
    right_factory = original_right.get("factory_stereo_calibration")
    if not isinstance(left_factory, dict) or not isinstance(right_factory, dict):
        raise ValueError(f"factory calibration missing: {original_right_path}")
    for key, value in left_factory.items():
        if right_factory.get(key) != value:
            raise ValueError(
                f"original RIGHT report factory calibration shared key mismatch: {key}"
            )
    extra_keys = set(right_factory) - set(left_factory)
    allowed_extra = {"right_rotation_from_left"}
    unexpected = extra_keys - allowed_extra
    if unexpected:
        raise ValueError(
            "original RIGHT report has unexpected factory calibration keys: "
            f"{sorted(unexpected)}"
        )
    if "right_rotation_from_left" in right_factory:
        reported = np.asarray(right_factory["right_rotation_from_left"], dtype=float)
        factory = np.asarray(calibration["right_rotation_from_left"], dtype=float)
        if reported.shape != (3, 3) or not np.allclose(reported, factory, atol=1e-12, rtol=0.0):
            raise ValueError("original RIGHT report right_rotation_from_left does not match DB3 factory calibration")


def validate_right_raw_geometry_trajectory(
    right_reports: list[dict[str, Any]],
    right_paths: list[Path],
    metric_trajectory: Path,
) -> Path:
    raw_paths: list[Path] = []
    for report, path in zip(right_reports, right_paths):
        raw = Path(report.get("trajectory", "")).resolve()
        if not raw.is_file():
            raise ValueError(f"original RIGHT raw geometry trajectory missing: {path}")
        if raw == metric_trajectory.resolve():
            raise ValueError("RIGHT raw geometry trajectory must be distinct from metric trajectory")
        raw_paths.append(raw)
    unique = set(raw_paths)
    if len(unique) != 1:
        raise ValueError(f"original RIGHT reports do not share one raw geometry trajectory: {sorted(map(str, unique))}")
    return raw_paths[0]


def validate_observations_bind_trajectory(
    report: dict[str, Any],
    times: np.ndarray,
    path: Path,
    label: str,
) -> None:
    for observation in report.get("observations", []):
        if not observation.get("accepted"):
            continue
        for key, time_key in (("first_index", "first_t_sec"), ("second_index", "second_t_sec")):
            index = int(observation[key])
            if index < 0 or index >= len(times):
                raise ValueError(f"RIGHT {label} observation index out of range in {path}")
            if abs(float(times[index]) - float(observation[time_key])) > 0.010:
                raise ValueError(f"RIGHT {label} observation timestamp mismatch in {path}")


def convert_right_report(
    *,
    refined_left_path: Path,
    original_left_path: Path,
    original_right_path: Path,
    raw_geometry_trajectory: Path,
    downstream_metric_trajectory: Path,
    output_path: Path,
    override_sha256: dict[str, str],
    db3_sha256: str,
) -> dict[str, Any]:
    refined_left, original_left = validate_refined_left(
        refined_left_path,
        original_left_path,
        override_sha256,
    )
    original_right = read_json(original_right_path)
    if original_right.get("observation_frame") != "infrared_right_camera_i":
        raise ValueError(f"original RIGHT report frame mismatch: {original_right_path}")
    if original_right.get("session") != refined_left.get("session"):
        raise ValueError(f"original RIGHT report session mismatch: {original_right_path}")
    if Path(original_right.get("trajectory", "")).resolve() != raw_geometry_trajectory.resolve():
        raise ValueError(f"original RIGHT report raw trajectory mismatch: {original_right_path}")
    derived_from = original_right.get("derived_from_left_stereo_report")
    if Path(str(derived_from)).resolve() != original_left_path.resolve():
        raise ValueError(
            "original RIGHT report is not derived from corresponding original LEFT report: "
            f"{original_right_path}"
        )
    calibration = derivation.load_stereo_calibration(Path(refined_left["db3"]))
    validate_right_factory_calibration(
        refined_left,
        original_right,
        calibration,
        original_right_path,
    )
    baseline_report = float(refined_left["factory_stereo_calibration"]["baseline_m"])
    if abs(float(calibration["baseline_m"]) - baseline_report) > 1e-9:
        raise ValueError("factory baseline changed since refined LEFT source report")

    times, positions, quaternions = derivation.load_trajectory(raw_geometry_trajectory)
    validate_observations_bind_trajectory(original_right, times, original_right_path, "raw")
    rotations = Rotation.from_quat(quaternions)
    observations = [
        derivation.convert_observation(
            observation,
            times,
            positions,
            rotations,
            Rotation.from_matrix(np.asarray(calibration["right_rotation_from_left"], dtype=float)),
            np.asarray(calibration["right_translation_from_left_m"], dtype=float),
        )
        for observation in refined_left["observations"]
    ]
    failures = []
    try:
        scale, quality = derivation.robust_scale(observations, min_observations=4)
    except ValueError as error:
        scale, quality = None, {"error": str(error)}
        failures.append("right_stereo_scale_unobservable")
    metric_times, _metric_positions, _metric_quaternions = derivation.load_trajectory(downstream_metric_trajectory)
    validate_observations_bind_trajectory(
        {"observations": observations},
        metric_times,
        output_path,
        "metric",
    )

    report = dict(refined_left)
    report.update(
        result="FAIL" if failures else "PASS",
        failures=failures,
        inputs="right-IR raw frontend trajectory + refined LEFT SIFT-LM geometry + factory D405 stereo extrinsic only",
        trajectory=str(raw_geometry_trajectory.resolve()),
        downstream_metric_trajectory=str(downstream_metric_trajectory.resolve()),
        right_geometry_trajectory=str(raw_geometry_trajectory.resolve()),
        observation_frame="infrared_right_camera_i",
        observations=observations,
        scale_m_per_mast3r_unit=scale,
        quality=quality,
        trajectory_continuity=(
            derivation.trajectory_step_continuity(positions, scale, times)
            if scale
            else None
        ),
        derived_from_left_stereo_report=str(refined_left_path.resolve()),
        derived_from_original_left_stereo_report=str(original_left_path.resolve()),
        derived_right_source_report_replaces_original_right_report=str(original_right_path.resolve()),
        right_geometry_source_policy=RIGHT_POLICY["right_frontend_policy"],
        factory_stereo_calibration=original_right["factory_stereo_calibration"],
        output=str(output_path.resolve()),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return {
        "right_report": str(output_path.resolve()),
        "right_report_sha256": file_hash(output_path),
        "result": report["result"],
        "failures": failures,
        "accepted_observations": sum(1 for item in observations if item.get("accepted")),
        "original_left_sha256": file_hash(original_left_path),
        "refined_left_sha256": file_hash(refined_left_path),
        "original_right_sha256": file_hash(original_right_path),
        "right_raw_geometry_trajectory_sha256": file_hash(raw_geometry_trajectory),
        "right_downstream_metric_trajectory_sha256": file_hash(downstream_metric_trajectory),
        "db3_factory_calibration_sha256": db3_sha256,
        "original_left_result": original_left.get("result"),
        "original_right_result": original_right.get("result"),
    }


def prepare_record(
    record: dict[str, Any],
    baseline: Path,
    source_stage: dict[str, Any],
    output: Path,
) -> dict[str, Any]:
    record_id = record["id"]
    artifact = baseline_artifact_dir(baseline, record_id)
    candidate, graph = base.validate_baseline_artifact(record, artifact)
    base.require_onboard_report(candidate, "baseline candidate")
    base.require_onboard_report(graph, "baseline graph")
    stage_record = source_eval.validate_source_stage_record(record_id, source_stage)
    refined_left_paths = ordered_refined_left_paths(stage_record)
    left_paths = physical.eye_report_paths_from_baseline(candidate, "left")
    right_paths = physical.eye_report_paths_from_baseline(candidate, "right")
    right_metric_trajectory = physical.eye_trajectory_path_from_baseline(candidate, "right")
    validate_baseline_bound(candidate, [*left_paths, *right_paths, right_metric_trajectory])
    override_hashes = dict(stage_record["source_override_sha256"])
    for path in refined_left_paths:
        validate_hash(path, override_hashes[str(path.resolve())], "refined LEFT source")
    for refined_path, original_path in zip(refined_left_paths, left_paths):
        validate_refined_left(refined_path, original_path, override_hashes)

    consumed = [
        artifact / "candidate_manifest.json",
        artifact / "graph_report.json",
        Path(source_stage["path"]),
        ROOT / "scripts/derive_right_ir_stereo_scale.py",
        ROOT / "scripts/align_mast3r_scale_with_stereo.py",
        ROOT / "scripts/fuse_mast3r_dual_stream.py",
        ROOT / "scripts/evaluate_sift_lm_physical_source_probe.py",
        ROOT / "scripts/run_physical_stereo_lever_probe.py",
        ROOT / "scripts/run_learned_segment_probe.py",
        Path(__file__),
        Path(record["session"]) / "d405_frames.csv",
        *left_paths,
        *right_paths,
        right_metric_trajectory,
        *refined_left_paths,
    ]
    refined_left_reports = {path: read_json(path) for path in refined_left_paths}
    original_right_reports = [read_json(path) for path in right_paths]
    right_raw_trajectory = validate_right_raw_geometry_trajectory(
        original_right_reports,
        right_paths,
        right_metric_trajectory,
    )
    consumed.append(right_raw_trajectory)
    for report in refined_left_reports.values():
        consumed.append(Path(report["db3"]))
        consumed.append(Path(report["trajectory"]))
    before = snapshot_paths(consumed)
    db3_paths = sorted(
        {Path(report["db3"]).resolve() for report in refined_left_reports.values()},
        key=str,
    )
    db3_hashes = {str(path): file_hash(path) for path in db3_paths}

    out_dir = output / record_id / "refined_right_sources"
    if out_dir.exists() or out_dir.is_symlink():
        raise FileExistsError(f"refined RIGHT target already exists: {out_dir}")
    out_dir.mkdir(parents=True)
    converted = []
    for refined_left_path, original_left_path, original_right_path in zip(
        refined_left_paths,
        left_paths,
        right_paths,
    ):
        converted.append(
            convert_right_report(
                refined_left_path=refined_left_path,
                original_left_path=original_left_path,
                original_right_path=original_right_path,
                raw_geometry_trajectory=right_raw_trajectory,
                downstream_metric_trajectory=right_metric_trajectory,
                output_path=out_dir / right_name_for_left(refined_left_path.name),
                override_sha256=override_hashes,
                db3_sha256=db3_hashes[str(Path(refined_left_reports[refined_left_path]["db3"]).resolve())],
            )
        )
    assert_snapshot_unchanged(before)
    failed_reports = [item for item in converted if item["result"] != "PASS"]
    if failed_reports:
        raise ValueError(
            "derived RIGHT report failed scale/quality validation: "
            + ", ".join(Path(item["right_report"]).name for item in failed_reports)
        )
    refined_right_sources = [item["right_report"] for item in converted]
    right_hashes = {item["right_report"]: item["right_report_sha256"] for item in converted}
    source_override = {**override_hashes, **right_hashes}
    left_mapping = {
        str(Path(item["right_report"]).resolve()): {
            "left_source_path": str(refined_left_paths[index].resolve()),
            "left_source_sha256": item["refined_left_sha256"],
            "original_left_source": str(left_paths[index].resolve()),
            "original_left_sha256": item["original_left_sha256"],
            "original_right_source": str(right_paths[index].resolve()),
            "original_right_sha256": item["original_right_sha256"],
        }
        for index, item in enumerate(converted)
    }
    return {
        "id": record_id,
        "status": "RIGHT_SOURCE_OVERRIDES_READY",
        "baseline_artifact": str(artifact.resolve()),
        "original_baseline_input_sha256_preserved": True,
        "baseline_input_sha256": dict(candidate.get("input_sha256", {})),
        "refined_left_sources": [str(path.resolve()) for path in refined_left_paths],
        "refined_right_sources": refined_right_sources,
        "source_override_sha256": source_override,
        "right_derivation_left_source_sha256": left_mapping,
        "right_raw_geometry_trajectory": str(right_raw_trajectory.resolve()),
        "right_raw_geometry_trajectory_sha256": file_hash(right_raw_trajectory),
        "right_downstream_metric_trajectory": str(right_metric_trajectory.resolve()),
        "right_downstream_metric_trajectory_sha256": file_hash(right_metric_trajectory),
        "consumed_source_guard": {
            "schema": "sift_lm_right_source_derivation_guard_v1",
            "guarded_path_count": len(before),
            "guarded_before_sha256": before,
            "guarded_after_verified": True,
        },
        "right_reports": converted,
        "factor_output_count": 0,
        "accepted_as_candidate": False,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError(f"output must be new: {args.output}")
    manifest = read_json(args.manifest)
    records = base.validate_records(manifest, args.dataset)
    try:
        source_stage = source_eval.load_source_stage(args.source_stage)
    except Exception as error:
        output = {
            "schema": SCHEMA,
            "status": "PREFLIGHT_WITH_FAILURES",
            "development_only": True,
            "external_ground_truth_used": False,
            "slam_supervision": False,
            "policy": {
                **RIGHT_POLICY,
                "source_stage_root": str(args.source_stage.resolve()),
                "derive_right_ir_stereo_scale_sha256": file_hash(ROOT / "scripts/derive_right_ir_stereo_scale.py"),
                "this_script_sha256": file_hash(Path(__file__)),
            },
            "manifest": str(args.manifest.resolve()),
            "baseline": str(args.baseline.resolve()),
            "source_stage_root": str(args.source_stage.resolve()),
            "record_count": len(records),
            "ready_record_count": 0,
            "refined_right_source_record_count": 0,
            "refined_source_count": 0,
            "factor_output_count": 0,
            "adapter_not_launched": True,
            "solver_not_launched": True,
            "scoring_not_launched": True,
            "records": [],
            "refined_sources": [],
            "failures": [
                {
                    "id": None,
                    "stage": "load_source_stage",
                    "error": f"{type(error).__name__}: {error}",
                }
            ],
        }
        write_json(args.output / "preflight_report.json", output)
        return output
    results = []
    failures = []
    for record in records:
        try:
            results.append(prepare_record(record, args.baseline, source_stage, args.output))
        except Exception as error:
            failures.append(
                {
                    "id": record.get("id"),
                    "stage": "prepare_right_sources",
                    "error": f"{type(error).__name__}: {error}",
                }
            )
    output = {
        "schema": SCHEMA,
        "status": "PREFLIGHT_COMPLETE" if not failures else "PREFLIGHT_WITH_FAILURES",
        "development_only": True,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "policy": {
            **RIGHT_POLICY,
            "source_stage": str((args.source_stage / "preflight_report.json").resolve()),
            "source_stage_sha256": file_hash(args.source_stage / "preflight_report.json"),
            "derive_right_ir_stereo_scale_sha256": file_hash(ROOT / "scripts/derive_right_ir_stereo_scale.py"),
            "this_script_sha256": file_hash(Path(__file__)),
        },
        "manifest": str(args.manifest.resolve()),
        "baseline": str(args.baseline.resolve()),
        "source_stage_root": str(args.source_stage.resolve()),
        "record_count": len(records),
        "ready_record_count": len(results),
        "refined_right_source_record_count": len(results),
        "refined_source_count": len(results),
        "factor_output_count": 0,
        "adapter_not_launched": True,
        "solver_not_launched": True,
        "scoring_not_launched": True,
        "records": results,
        "refined_sources": results,
        "failures": failures,
    }
    write_json(args.output / "preflight_report.json", output)
    return output


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--source-stage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = argument_parser().parse_args(argv)
    report = run(args)
    print(
        json.dumps(
            {
                "status": report["status"],
                "record_count": report["record_count"],
                "refined_source_count": report.get("refined_source_count", 0),
                "failure_count": len(report.get("failures", [])),
                "output": str(args.output.resolve()),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "PREFLIGHT_COMPLETE" else 3


if __name__ == "__main__":
    raise SystemExit(main())
