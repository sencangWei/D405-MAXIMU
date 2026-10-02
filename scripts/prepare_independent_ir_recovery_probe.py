#!/usr/bin/env python3
"""Source-only appendix producer for symmetric independent IR missing-pair recovery."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import diagnose_independent_right_stereo as diag  # noqa: E402
import fuse_mast3r_stereo_imu as fusion  # noqa: E402
import run_physical_stereo_lever_probe as physical  # noqa: E402
from ego_vio.vio.cached_ir_correspondences import CachedIrSiftCorrespondences  # noqa: E402
from prepare_independent_right_geometry_probe import DisparityCache, accepted_row_indices  # noqa: E402


SCHEMA = "umi_independent_ir_recovery_preflight_v1"
APPENDIX_SCHEMA = "umi_independent_ir_recovery_appendix_v1"
LOW_EXCITATION_REASON = "translation_excitation_low"


def read_json(path: Path) -> Any:
    return diag.read_json(path)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def file_hash(path: Path) -> str:
    return diag.file_sha256(path)


def snapshot(paths: dict[str, Path]) -> dict[str, dict[str, str]]:
    return diag.snapshot(paths)


def assert_unchanged(before: dict[str, dict[str, str]]) -> None:
    changed = diag.compare_snapshot(before, snapshot({label: Path(item["path"]) for label, item in before.items()}))
    if changed:
        raise ValueError("; ".join(changed))


def validate_consumed_subset(eye: str, produced: dict[str, Path], guarded: dict[str, Path]) -> None:
    guarded_paths = {str(path.resolve()) for path in guarded.values()}
    missing = sorted(str(path.resolve()) for path in produced.values() if str(path.resolve()) not in guarded_paths)
    if missing:
        raise ValueError(f"{eye} consumed paths missing from source guard: {missing}")


def load_source_stage(path: Path) -> dict[str, Any]:
    report_path = path / "preflight_report.json"
    report = read_json(report_path)
    if report.get("schema") != "umi_independent_right_geometry_refresh_preflight_v1":
        raise ValueError("source stage schema mismatch")
    if report.get("status") != "PREFLIGHT_COMPLETE":
        raise ValueError("source stage is not complete")
    if report.get("external_ground_truth_used") is not False or report.get("slam_supervision") is not False:
        raise ValueError("source stage is not onboard-only")
    rows = report.get("records")
    if not isinstance(rows, list):
        raise ValueError("source stage records missing")
    by_id = {str(row["id"]): row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError("source stage duplicate id")
    parent = report.get("source_stage")
    if not parent:
        raise ValueError("source stage does not declare parent source_stage")
    if report.get("ready_record_count") != len(rows):
        raise ValueError("source stage ready_record_count mismatch")
    return {"path": report_path.resolve(), "sha256": file_hash(report_path), "raw": report, "by_id": by_id, "parent": Path(parent)}


def report_paths(row: dict[str, Any], eye: str) -> list[Path]:
    key = "refined_left_sources" if eye == "left" else "refined_right_sources"
    names = physical.eye_report_names(eye)
    by_name = {Path(path).name: Path(path).resolve() for path in row.get(key, [])}
    missing = [name for name in names if name not in by_name]
    if missing:
        raise ValueError(f"{eye} reports missing: {missing}")
    return [by_name[name] for name in names]


def validate_stage_overrides(row: dict[str, Any], paths: list[Path]) -> None:
    overrides = row.get("source_override_sha256")
    if not isinstance(overrides, dict) or not overrides:
        raise ValueError("source stage source_override_sha256 missing")
    for path in paths:
        resolved = str(path.resolve())
        if overrides.get(resolved) != file_hash(path):
            raise ValueError(f"source_override_sha256 mismatch: {resolved}")


def load_normal_merged_reports(paths: list[Path], eye: str, record: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    reports = []
    for path in paths:
        report = read_json(path)
        diag.validate_report(report, path, record, f"infrared_{eye}_camera_i")
        report["report_path"] = str(path.resolve())
        reports.append(report)
    merged = fusion.merge_stereo_reports(reports[0], reports[1:], optional_policy="reject_window")
    accepted_paths = set(merged.get("merged_report_paths", []))
    return merged, [report for report in reports if report["report_path"] in accepted_paths]


def candidate_rows(reports: list[dict[str, Any]], paths_by_report: dict[str, Path], times: np.ndarray) -> list[dict[str, Any]]:
    rows = []
    for report in reports:
        path = paths_by_report[report["report_path"]]
        for index, row in enumerate(report.get("observations", [])):
            if row.get("accepted") is True or row.get("reason") == LOW_EXCITATION_REASON:
                continue
            first, second = accepted_row_indices(row, times, path)
            rows.append({"report": report, "path": path, "index": index, "row": row, "first": first, "second": second})
    return rows


def preflight_eye(
    *,
    eye: str,
    record: dict[str, Any],
    paths: list[Path],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], np.ndarray, np.ndarray, np.ndarray, dict[str, Path]]:
    merged, normal_reports = load_normal_merged_reports(paths, eye, record)
    trajectory = Path(merged["trajectory"]).resolve()
    times, positions, quaternions, _ = diag.stereo.load_trajectory(trajectory)
    path_by_report = {report["report_path"]: Path(report["report_path"]) for report in normal_reports}
    rows = candidate_rows(normal_reports, path_by_report, times)
    return merged, normal_reports, rows, times, positions, quaternions, path_by_report


def estimate_eye(
    *,
    eye: str,
    row: dict[str, Any],
    left_numbers: np.ndarray,
    right_numbers: np.ndarray,
    left_images: dict[int, np.ndarray],
    right_images: dict[int, np.ndarray],
    positions: np.ndarray,
    rotations: Rotation,
    calibration: dict[str, Any],
    disparity_cache: DisparityCache,
    sift_cache: CachedIrSiftCorrespondences,
) -> dict[str, Any]:
    first, second = int(row["first_index"]), int(row["second_index"])
    left_i, right_i = left_images[int(left_numbers[first])], right_images[int(right_numbers[first])]
    left_j, right_j = left_images[int(left_numbers[second])], right_images[int(right_numbers[second])]
    disp_l_i, disp_r_i = disparity_cache.get(first, left_i, right_i)
    disp_l_j, disp_r_j = disparity_cache.get(second, left_j, right_j)
    if eye == "left":
        f_i, f_j, f_valid = sift_cache.correspondences(first, left_i, disp_l_i > 0.5, second, left_j)
        r_i, r_j, r_valid = sift_cache.correspondences(second, left_j, disp_l_j > 0.5, first, left_i)
        forward = diag.stereo.estimate_motion_from_correspondences(
            f_i, f_j, f_valid, disp_l_i, disp_r_i, positions[first], positions[second], rotations[first], rotations[second],
            calibration, diag.MIN_DEPTH_M, diag.MAX_DEPTH_M, "sift", trajectory_frame="infrared_left", **diag.PNP_PARAMS,
        )
        reverse = diag.stereo.estimate_motion_from_correspondences(
            r_i, r_j, r_valid, disp_l_j, disp_r_j, positions[second], positions[first], rotations[second], rotations[first],
            calibration, diag.MIN_DEPTH_M, diag.MAX_DEPTH_M, "sift", trajectory_frame="infrared_left", **diag.PNP_PARAMS,
        )
    else:
        mask_i = (disp_r_i < -0.5) & (disp_r_i > -float(diag.NUM_DISPARITIES))
        mask_j = (disp_r_j < -0.5) & (disp_r_j > -float(diag.NUM_DISPARITIES))
        f_i, f_j, f_valid = sift_cache.correspondences(first, right_i, mask_i, second, right_j)
        r_i, r_j, r_valid = sift_cache.correspondences(second, right_j, mask_j, first, right_i)
        forward = diag.estimate_right_motion_from_correspondences(
            f_i, f_j, f_valid, disp_l_i, disp_r_i, positions[first], positions[second], rotations[first], rotations[second],
            calibration, diag.MIN_DEPTH_M, diag.MAX_DEPTH_M, "sift", **diag.PNP_PARAMS,
        )
        reverse = diag.estimate_right_motion_from_correspondences(
            r_i, r_j, r_valid, disp_l_j, disp_r_j, positions[second], positions[first], rotations[second], rotations[first],
            calibration, diag.MIN_DEPTH_M, diag.MAX_DEPTH_M, "sift", **diag.PNP_PARAMS,
        )
    combined = diag.combine_bidirectional_native(forward, reverse)
    return {
        "combined": combined,
        "raw_forward_summary": diag._result_summary(forward),
        "raw_reverse_summary": diag._result_summary(reverse),
    }


def build_eye_appendix(
    *,
    eye: str,
    record: dict[str, Any],
    paths: list[Path],
    left_report_for_images: dict[str, Any],
    preflight: tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], np.ndarray, np.ndarray, np.ndarray, dict[str, Path]],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Path]]:
    merged, _normal_reports, rows, times, positions, quaternions, _path_by_report = preflight
    reference_scale = float(merged["scale_m_per_mast3r_unit"])
    trajectory = Path(merged["trajectory"]).resolve()
    selected = {index for item in rows for index in (item["first"], item["second"])}
    left_numbers, right_numbers, sync, calibration, left_images, right_images = diag.lowdiag._load_images(
        left_report_for_images, Path(record["session"]).resolve(), Path(left_report_for_images["db3"]).resolve(),
        "infrared_left", times, selected,
    )
    disparity_cache = DisparityCache()
    sift_cache = CachedIrSiftCorrespondences(max_feature_entries=max(1, 2 * len(selected)))
    rotations = Rotation.from_quat(quaternions)
    native_cache: dict[tuple[int, int], dict[str, Any]] = {}
    observations = []
    for item in rows:
        key = (item["first"], item["second"])
        if key not in native_cache:
            native_cache[key] = estimate_eye(
                eye=eye,
                row=item["row"],
                left_numbers=left_numbers,
                right_numbers=right_numbers,
                left_images=left_images,
                right_images=right_images,
                positions=positions,
                rotations=rotations,
                calibration=calibration,
                disparity_cache=disparity_cache,
                sift_cache=sift_cache,
            )
        native = native_cache[key]
        observations.append(
            {
                "eye": eye,
                "source_report_path": str(item["path"].resolve()),
                "source_report_sha256": file_hash(item["path"]),
                "source_report_result": item["report"].get("result"),
                "source_observation_index": item["index"],
                "original_observation": item["row"],
                "native_observation": {
                    **native["combined"],
                    "first_index": item["first"],
                    "second_index": item["second"],
                    "first_t_sec": item["row"]["first_t_sec"],
                    "second_t_sec": item["row"]["second_t_sec"],
                },
                "raw_forward_summary": native["raw_forward_summary"],
                "raw_reverse_summary": native["raw_reverse_summary"],
            }
        )
    context = {
        "reference_scale": reference_scale,
        "reference_trajectory_path": str(trajectory),
        "reference_trajectory_sha256": file_hash(trajectory),
        "merged_report_paths": [str(Path(path).resolve()) for path in merged.get("merged_report_paths", [])],
        "report_sha256": {str(path.resolve()): file_hash(path) for path in paths},
        "synchronization": sync,
    }
    consumed = {f"{eye}_report_{index}_{path.name}": path for index, path in enumerate(paths)}
    consumed[f"{eye}_trajectory"] = trajectory
    return context, observations, consumed


def prepare_record(record: dict[str, Any], source_stage: dict[str, Any], parent_stage: dict[str, Any], baseline: Path, output: Path) -> dict[str, Any]:
    record_id = record["id"]
    row = source_stage["by_id"].get(record_id)
    if row is None:
        raise ValueError(f"source stage missing record: {record_id}")
    _left_path, _right_path, left_report, left_traj, right_traj, db3, parent_consumed = diag._record_sources(
        record, parent_stage["by_id"][record_id], baseline
    )
    left_paths, right_paths = report_paths(row, "left"), report_paths(row, "right")
    validate_stage_overrides(row, [*left_paths, *right_paths])
    left_preflight = preflight_eye(eye="left", record=record, paths=left_paths)
    right_preflight = preflight_eye(eye="right", record=record, paths=right_paths)
    consumed = {
        **parent_consumed,
        "source_stage_preflight": source_stage["path"],
        "parent_source_stage_preflight": parent_stage["path"],
        "recovery_producer_module": Path(__file__).resolve(),
        "cache_module": ROOT / "ego_vio/vio/cached_ir_correspondences.py",
        "right_helper_module": ROOT / "ego_vio/vio/right_stereo_motion.py",
        "diagnostic_module": Path(diag.__file__).resolve(),
        "db3": db3,
        "left_parent_trajectory": left_traj,
        "right_parent_trajectory": right_traj,
    }
    for index, path in enumerate([*left_paths, *right_paths]):
        consumed[f"stage_report_{index:02d}_{path.name}"] = path
    for eye, preflight in (("left", left_preflight), ("right", right_preflight)):
        consumed[f"{eye}_reference_trajectory"] = Path(preflight[0]["trajectory"]).resolve()
    before = snapshot(consumed)
    left_context, left_observations, left_consumed = build_eye_appendix(
        eye="left", record=record, paths=left_paths, left_report_for_images=left_report, preflight=left_preflight
    )
    right_context, right_observations, right_consumed = build_eye_appendix(
        eye="right", record=record, paths=right_paths, left_report_for_images=left_report, preflight=right_preflight
    )
    validate_consumed_subset("left", left_consumed, consumed)
    validate_consumed_subset("right", right_consumed, consumed)
    appendix = {
        "schema": APPENDIX_SCHEMA,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "id": record_id,
        "session": str(Path(record["session"]).resolve()),
        "source_stage_preflight": str(source_stage["path"]),
        "source_stage_preflight_sha256": source_stage["sha256"],
        "eye_contexts": {"left": left_context, "right": right_context},
        "observations": left_observations + right_observations,
        "native_observation_count": len(left_observations) + len(right_observations),
        "native_accepted_count": sum(1 for obs in left_observations + right_observations if obs["native_observation"].get("accepted") is True),
        "consumed_source_guard": {
            "schema": "independent_ir_recovery_guard_v1",
            "guarded_path_count": len(before),
            "guarded_before_sha256": before,
            "guarded_after_verified": True,
        },
    }
    assert_unchanged(before)
    out_path = output / record_id / "independent_ir_recovery_appendix.json"
    write_json(out_path, appendix)
    out_row = dict(row)
    out_row.update(
        status="INDEPENDENT_IR_RECOVERY_APPENDIX_READY",
        recovery_appendix_path=str(out_path.resolve()),
        recovery_appendix_sha256=file_hash(out_path),
        recovery_native_observation_count=appendix["native_observation_count"],
        recovery_native_accepted_count=appendix["native_accepted_count"],
        consumed_source_guard=appendix["consumed_source_guard"],
    )
    return out_row


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError(f"output must be new: {args.output}")
    records = diag.load_manifest_records(args.manifest, args.dataset)
    source_stage = load_source_stage(args.source_stage)
    parent_stage = diag.load_consistent_stage(source_stage["parent"])
    results, failures = [], []
    for record in records:
        try:
            results.append(prepare_record(record, source_stage, parent_stage, args.baseline, args.output))
        except Exception as error:
            failures.append({"id": record.get("id"), "stage": "prepare_record", "error": f"{type(error).__name__}: {error}"})
    report = {
        "schema": SCHEMA,
        "status": "PREFLIGHT_COMPLETE" if not failures else "PREFLIGHT_WITH_FAILURES",
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "backend_launched": False,
        "scoring_launched": False,
        "gpu_model_used": False,
        "manifest": str(args.manifest.resolve()),
        "baseline": str(args.baseline.resolve()),
        "source_stage": str(args.source_stage.resolve()),
        "source_stage_preflight_sha256": source_stage["sha256"],
        "parent_source_stage": str(source_stage["parent"].resolve()),
        "record_count": len(records),
        "ready_record_count": len(results),
        "records": results,
        "refined_sources": results,
        "failures": failures,
    }
    write_json(args.output / "preflight_report.json", report)
    return report


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
    print(json.dumps({"status": report["status"], "ready": report["ready_record_count"], "failures": report["failures"]}, ensure_ascii=False))
    return 0 if report["status"] == "PREFLIGHT_COMPLETE" else 3


if __name__ == "__main__":
    raise SystemExit(main())
