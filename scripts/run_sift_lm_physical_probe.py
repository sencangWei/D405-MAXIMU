#!/usr/bin/env python3
"""Development-only SIFT-LM physical stereo measurement probe preflight.

This file intentionally does not run the solver or mutate production artifacts.
It checks whether frozen adapter records contain the sources needed to replay
the old SIFT free-LM + gyro validation core as a measurement-only physical
stereo comparison.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any
import csv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import run_learned_segment_probe as base  # noqa: E402
import run_physical_stereo_lever_probe as physical  # noqa: E402
import align_mast3r_scale_with_stereo as stereo_align  # noqa: E402

SCHEMA = "umi_sift_lm_physical_probe_preflight_v1"
BASELINE_POLICY = "both"
LEGACY_REFINER = ROOT / ".planning/stereo_spatial_repeatability_20260927/run_sift_lm_gyro_candidate.py"
POLICY = {
    "name": "sift_free_LM_with_raw_400hz_gyro_5deg_measurement_only_preflight",
    "development_only": True,
    "measurement_only": True,
    "no_gt_selector": True,
    "lk_policy": "unchanged cached LK/global scales/frontends/VINS/timestamps",
    "solver_policy": "not launched by this source probe",
    "candidate_policy": (
        "future candidates must preserve original baseline input_sha256 and put "
        "refined SIFT report hashes in source_override_sha256"
    ),
    "not_full_20260927_restore": True,
    "refinement_source": str(LEGACY_REFINER),
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_paths(paths: list[Path]) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in paths:
        resolved = str(path.resolve())
        if resolved not in snapshot:
            snapshot[resolved] = file_hash(path)
    return snapshot


def compare_snapshot(before: dict[str, str]) -> list[str]:
    failures = []
    for source, expected in before.items():
        path = Path(source)
        if not path.is_file():
            failures.append(f"guarded source disappeared: {source}")
        elif file_hash(path) != expected:
            failures.append(f"guarded source changed: {source}")
    return failures


def load_legacy_refiner():
    spec = importlib.util.spec_from_file_location("sift_lm_gyro_candidate", LEGACY_REFINER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def baseline_artifact_dir(baseline: Path, record_id: str) -> Path:
    return baseline / record_id / BASELINE_POLICY


def validate_source_hashes(candidate: dict[str, Any], paths: list[Path]) -> dict[str, str]:
    hashes = {}
    expected = candidate.get("input_sha256", {})
    if not isinstance(expected, dict) or not expected:
        raise ValueError("baseline candidate input_sha256 missing")
    for path in paths:
        resolved = str(path.resolve())
        if resolved not in expected:
            raise ValueError(f"source path is not hash-bound by baseline candidate: {resolved}")
        digest = file_hash(path)
        if digest != expected[resolved]:
            raise ValueError(f"source hash changed: {resolved}")
        hashes[resolved] = digest
    return hashes


def report_method_counts(report: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for observation in report.get("observations", []):
        if not observation.get("accepted"):
            continue
        method = str(observation.get("method", "unknown"))
        counts[method] = counts.get(method, 0) + 1
    return counts


def inspect_stereo_reports(
    record: dict[str, Any],
    candidate: dict[str, Any],
    eye: str,
) -> tuple[list[dict[str, Any]], list[Path]]:
    paths = physical.eye_report_paths_from_baseline(candidate, eye)
    trajectory = physical.eye_trajectory_path_from_baseline(candidate, eye)
    validate_source_hashes(candidate, [trajectory, *paths])
    rows = []
    for path in paths:
        report = read_json(path)
        if Path(report.get("session", "")).resolve() != Path(record["session"]).resolve():
            raise ValueError(f"{eye} report session mismatch: {path}")
        if report.get("external_ground_truth_used") or report.get("slam_supervision"):
            raise ValueError(f"{eye} report is not onboard-only: {path}")
        frame = f"infrared_{eye}_camera_i"
        if report.get("observation_frame") != frame:
            raise ValueError(f"{eye} report frame mismatch: {path}")
        counts = report_method_counts(report)
        rows.append(
            {
                "path": str(path.resolve()),
                "sha256": file_hash(path),
                "accepted_total": sum(counts.values()),
                "accepted_sift": counts.get("sift", 0),
                "accepted_lk": counts.get("lk", 0),
                "method_counts": counts,
                "prepared_dataset": report.get("prepared_dataset"),
                "has_prepared_dataset": bool(report.get("prepared_dataset")),
                "dataset_or_raw_frame_source": (
                    "prepared_dataset" if report.get("prepared_dataset") else "db3_raw_frames"
                ),
                "trajectory": str(Path(report["trajectory"]).resolve()),
                "trajectory_sha256": file_hash(Path(report["trajectory"])),
                "session_matches_record": Path(report.get("session", "")).resolve() == Path(record["session"]).resolve(),
            }
        )
    return rows, [trajectory, *paths]


def left_legacy_contract_probe(
    record: dict[str, Any],
    candidate: dict[str, Any],
    left_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Check whether old refine_reports can be called for LEFT reports only."""
    left_reports = [Path(row["path"]).resolve() for row in left_rows]
    primary = [path for path in left_reports if path.name == "stereo_scale_bidirectional_report.json"]
    if len(primary) != 1:
        raise ValueError("expected exactly one LEFT primary stereo report")
    graph_path = primary[0].parent / "graph_fusion_report.json"
    missing_graphs = [] if graph_path.is_file() else [str(graph_path.resolve())]
    legacy_graphs = [str(graph_path.resolve())] if graph_path.is_file() else []
    cached_argument = None
    graph_sources_match = False
    session_matches = False
    formal_inputs_match = False
    extrinsic_matches = False
    if graph_path.is_file():
        graph = read_json(graph_path)
        inputs = graph.get("inputs", {})
        graph_sources = [
            Path(inputs.get("stereo_report", "")).resolve(),
            *[Path(path).resolve() for path in inputs.get("additional_stereo_reports", [])],
        ]
        graph_sources_match = set(graph_sources) == set(left_reports)
        session_matches = Path(inputs.get("session", "")).resolve() == Path(record["session"]).resolve()
        formal_inputs_match = (
            Path(inputs.get("imu_calibration", "")).resolve()
            == (ROOT / "config/imu_runtime_accel_calibrated_raw_gyro_20260816.yaml").resolve()
            and Path(inputs.get("vins_spatiotemporal_calibration", "")).name == "vins_config.yaml"
        )
        graph_extrinsic = graph.get("camera_extrinsics", {}).get("effective_body_T_trajectory_camera")
        candidate_extrinsic = candidate.get("eye_reports", {}).get("left", {}).get("effective_body_T_camera")
        extrinsic_matches = graph_extrinsic == candidate_extrinsic
        try:
            cached_argument = str(graph_path.parent.parent.relative_to(ROOT / "reports"))
        except ValueError:
            cached_argument = None
    missing_dataset = []
    for row in left_rows:
        dataset = Path(row["trajectory"]).parent / "dataset"
        if not dataset.is_dir():
            missing_dataset.append(str(dataset.resolve()))
    callable_directly = (
        bool(legacy_graphs)
        and not missing_dataset
        and graph_sources_match
        and session_matches
        and formal_inputs_match
        and extrinsic_matches
        and cached_argument is not None
    )
    return {
        "scope": "left_only",
        "legacy_refine_reports_callable_without_adapter": callable_directly,
        "cached_argument": cached_argument,
        "legacy_graph_fusion_reports_found": legacy_graphs,
        "missing_legacy_graph_fusion_reports": missing_graphs,
        "missing_prepared_dataset_dirs": missing_dataset,
        "graph_inputs_match_baseline_left_raw_reports": graph_sources_match,
        "graph_session_matches_record": session_matches,
        "formal_imu_vins_inputs_match": formal_inputs_match,
        "graph_effective_left_extrinsic_matches_candidate": extrinsic_matches,
        "required_minimal_adapter": (
            "No shim graph needed when callable_directly is true: pass cached_argument "
            "directly to legacy refine_reports and write refined LEFT source overrides."
        ),
    }


def selected_prepared_image_paths(graph_inputs: dict[str, Any]) -> list[Path]:
    """Resolve exact prepared image files legacy refine_reports will read."""
    report_paths = [
        Path(graph_inputs["stereo_report"]).resolve(),
        *[Path(path).resolve() for path in graph_inputs.get("additional_stereo_reports", [])],
    ]
    trajectory = Path(graph_inputs["trajectory"]).resolve()
    session = Path(graph_inputs["session"]).resolve()
    times, _positions, _quaternions, _rows = stereo_align.load_trajectory(trajectory)
    left_numbers, right_numbers, _sync = stereo_align.match_trajectory_to_stereo_frames(
        session / "d405_frames.csv",
        times,
        trajectory_frame="infrared_left",
    )
    indices: set[int] = set()
    for report_path in report_paths:
        report = read_json(report_path)
        for observation in report.get("observations", []):
            if observation.get("accepted") and observation.get("method") == "sift":
                indices.add(int(observation["first_index"]))
                indices.add(int(observation["second_index"]))
    if not indices:
        return []
    dataset = trajectory.parent / "dataset"
    exported = list(csv.DictReader((dataset / "frames.csv").open(newline="", encoding="utf-8")))
    left_paths = {
        int(row["source_frame_number"]): dataset / row["image"]
        for row in exported
    }
    source_rows = list(csv.DictReader((session / "d405_frames.csv").open(newline="", encoding="utf-8")))
    right_to_left = {
        int(row["infrared_right_frame_number"]): int(row["infrared_left_frame_number"])
        for row in source_rows
        if row.get("infrared_left_frame_number") and row.get("infrared_right_frame_number")
    }
    paths: list[Path] = []
    for number in {int(left_numbers[index]) for index in indices}:
        path = left_paths.get(number)
        if path is None or not path.is_file():
            raise FileNotFoundError(f"missing prepared left image for frame {number}")
        paths.append(path)
    for number in {int(right_numbers[index]) for index in indices}:
        left_number = right_to_left.get(number)
        left_path = left_paths.get(left_number) if left_number is not None else None
        path = dataset / "stereo_right" / left_path.name if left_path is not None else None
        if path is None or not path.is_file():
            raise FileNotFoundError(f"missing prepared right image for frame {number}")
        paths.append(path)
    return paths


def run_guard_paths(record_result: dict[str, Any]) -> tuple[list[Path], dict[str, Any]]:
    artifact = Path(record_result["baseline_artifact"])
    candidate = read_json(artifact / "candidate_manifest.json")
    graph_path = Path(record_result["legacy_contract"]["legacy_graph_fusion_reports_found"][0])
    legacy_graph = read_json(graph_path)
    inputs = legacy_graph["inputs"]
    dataset = Path(inputs["trajectory"]).resolve().parent / "dataset"
    image_paths = selected_prepared_image_paths(inputs)
    paths = [
        artifact / "candidate_manifest.json",
        artifact / "graph_report.json",
        graph_path,
        Path(inputs["session"]) / "d405_frames.csv",
        Path(inputs["session"]) / "external_imu/imu.bin",
        Path(inputs["imu_calibration"]),
        Path(inputs["vins_spatiotemporal_calibration"]),
        Path(inputs["trajectory"]),
        dataset / "dataset_manifest.json",
        dataset / "frames.csv",
        LEGACY_REFINER,
        Path(__file__),
        Path(stereo_align.__file__),
        ROOT / "scripts/fuse_mast3r_stereo_imu.py",
        *[Path(path) for path in candidate.get("input_sha256", {})],
        *image_paths,
    ]
    evidence = {
        "schema": "sift_lm_source_replay_guard_v1",
        "baseline_input_path_count": len(candidate.get("input_sha256", {})),
        "prepared_image_path_count": len(image_paths),
        "prepared_dataset_manifest": str((dataset / "dataset_manifest.json").resolve()),
        "prepared_dataset_frames_csv": str((dataset / "frames.csv").resolve()),
        "legacy_graph": str(graph_path.resolve()),
        "legacy_refiner": str(LEGACY_REFINER.resolve()),
        "align_module": str(Path(stereo_align.__file__).resolve()),
        "fusion_module": str((ROOT / "scripts/fuse_mast3r_stereo_imu.py").resolve()),
    }
    return paths, evidence


def preflight_record(record: dict[str, Any], baseline: Path) -> dict[str, Any]:
    artifact = baseline_artifact_dir(baseline, record["id"])
    candidate_path = artifact / "candidate_manifest.json"
    graph_path = artifact / "graph_report.json"
    if not candidate_path.is_file() or not graph_path.is_file():
        raise ValueError(f"missing baseline candidate/graph under {artifact}")
    candidate = read_json(candidate_path)
    graph = read_json(graph_path)
    base.require_onboard_report(candidate, "baseline candidate")
    base.require_onboard_report(graph, "baseline graph")
    if Path(candidate.get("session", "")).resolve() != Path(record["session"]).resolve():
        raise ValueError("baseline candidate session mismatch")
    if candidate.get("policy_arguments") != base.BASELINE_POLICY_ARGUMENTS:
        raise ValueError("baseline policy arguments are not adapter-v2 frozen policy")
    if graph.get("policy_arguments") not in (None, candidate.get("policy_arguments")):
        raise ValueError("baseline graph/candidate policy mismatch")
    report_rows = []
    rows_by_eye: dict[str, list[dict[str, Any]]] = {}
    bound_paths = [candidate_path, graph_path]
    for eye in ("left", "right"):
        rows, paths = inspect_stereo_reports(record, candidate, eye)
        rows_by_eye[eye] = rows
        report_rows.extend(dict(row, eye=eye) for row in rows)
        bound_paths.extend(paths)
    validate_source_hashes(candidate, list(dict.fromkeys(bound_paths[2:])))
    sift_total = sum(row["accepted_sift"] for row in report_rows)
    lk_total = sum(row["accepted_lk"] for row in report_rows)
    return {
        "id": record["id"],
        "status": "READY_FOR_ADAPTER_DESIGN" if sift_total > 0 else "NO_SIFT_ACCEPTED_OBSERVATIONS",
        "session": str(Path(record["session"]).resolve()),
        "baseline_artifact": str(artifact.resolve()),
        "baseline_candidate_sha256": file_hash(artifact / "candidate_manifest.json"),
        "baseline_graph_sha256": file_hash(artifact / "graph_report.json"),
        "original_baseline_input_sha256_preserved": True,
        "baseline_input_sha256": dict(candidate.get("input_sha256", {})),
        "source_override_sha256": {},
        "source_override_sha256_note": (
            "preflight writes no refined reports; future SIFT-LM reports must be "
            "recorded here without replacing baseline input_sha256"
        ),
        "fixed_replay_contract": {
            "pnp_refine_enabled": True,
            "pnp_rotation_mode": "free",
            "gyro_gate_deg": 5.0,
            "imu_td_s": -0.009109323,
            "stereo_max_depth_m": 0.6,
            "stereo_min_depth_m": 0.07,
            "formal_extrinsics_required": True,
        },
        "factor_output_count": 0,
        "accepted_as_candidate": False,
        "accepted_sift_total": sift_total,
        "accepted_lk_total": lk_total,
        "reports": report_rows,
        "legacy_contract": left_legacy_contract_probe(record, candidate, rows_by_eye["left"]),
    }


def run_refine_sources(record_result: dict[str, Any], output: Path, legacy) -> dict[str, Any]:
    contract = record_result["legacy_contract"]
    if not contract.get("legacy_refine_reports_callable_without_adapter"):
        raise ValueError("LEFT legacy refine_reports contract is not callable")
    target = output / record_result["id"] / "refined_left_sources"
    if target.exists():
        raise FileExistsError(f"refined source target already exists: {target}")
    guard_paths, guard_evidence = run_guard_paths(record_result)
    before = snapshot_paths(guard_paths)
    rewritten = legacy.refine_reports(
        stereo_align,
        contract["cached_argument"],
        target,
    )
    guard_failures = compare_snapshot(before)
    if guard_failures:
        raise RuntimeError("source replay guard failed: " + "; ".join(guard_failures[:5]))
    override_hashes = {str(Path(path).resolve()): file_hash(Path(path)) for path in rewritten}
    return {
        "id": record_result["id"],
        "refined_left_sources": [str(Path(path).resolve()) for path in rewritten],
        "source_override_sha256": override_hashes,
        "source_guard": {
            **guard_evidence,
            "guarded_path_count": len(before),
            "guarded_before_sha256": before,
            "guarded_after_verified": True,
        },
        "factor_output_count": 0,
        "accepted_as_candidate": False,
        "measurement_only": True,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError(f"output must be new: {args.output}")
    manifest = read_json(args.manifest)
    records = base.validate_records(manifest, args.dataset)
    legacy = load_legacy_refiner()
    results = []
    failures = []
    for record in records:
        try:
            results.append(preflight_record(record, args.baseline))
        except Exception as error:
            failures.append(
                {
                    "id": record.get("id"),
                    "error": f"{type(error).__name__}: {error}",
                }
            )
    ready = [row for row in results if row["status"] == "READY_FOR_ADAPTER_DESIGN"]
    refined_sources = []
    if args.run:
        for record_result in results:
            if record_result["status"] != "READY_FOR_ADAPTER_DESIGN":
                continue
            try:
                refined_sources.append(run_refine_sources(record_result, args.output, legacy))
            except Exception as error:
                failures.append(
                    {
                        "id": record_result.get("id"),
                        "stage": "run_refine_sources",
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
            **POLICY,
            "legacy_refiner_sha256": file_hash(LEGACY_REFINER),
            "legacy_refine_reports_present": hasattr(legacy, "refine_reports"),
            "physical_runner_sha256": file_hash(ROOT / "scripts/run_physical_stereo_lever_probe.py"),
            "constant_runner_sha256": file_hash(ROOT / "scripts/run_constant_ir_gauge_probe.py"),
            "this_script_sha256": file_hash(Path(__file__)),
        },
        "manifest": str(args.manifest.resolve()),
        "baseline": str(args.baseline.resolve()),
        "record_count": len(records),
        "ready_record_count": len(ready),
        "total_accepted_sift": sum(row["accepted_sift_total"] for row in results),
        "total_accepted_lk": sum(row["accepted_lk_total"] for row in results),
        "run_refine_sources_requested": bool(args.run),
        "source_refine_replay_launched": bool(args.run),
        "adapter_not_launched": True,
        "solver_not_launched": True,
        "scoring_not_launched": True,
        "refined_source_count": len(refined_sources),
        "refined_sources": refined_sources,
        "blocking_notes": (
            [
                "Source refine replay launched only for refined LEFT source overrides.",
                "No adapter factors, physical transform, solver, scoring, or candidate acceptance was run.",
            ]
            if args.run
            else [
                "Current pass is preflight only; no LM replay, solver, scoring, or report mutation was run.",
                "Old refine_reports is callable only when LEFT legacy graph_fusion_report contract is present.",
            ]
        ),
        "records": results,
        "failures": failures,
    }
    write_json(args.output / "preflight_report.json", output)
    return output


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", action="append", default=[])
    parser.add_argument("--run", action="store_true", help="write refined LEFT source overrides only; no solver/scoring")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = argument_parser().parse_args(argv)
    report = run(args)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PREFLIGHT_COMPLETE" else 3


if __name__ == "__main__":
    raise SystemExit(main())
