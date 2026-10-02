#!/usr/bin/env python3
"""Source-only diagnostic for independent RIGHT temporal stereo SIFT/PnP."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import align_mast3r_scale_with_stereo as stereo  # noqa: E402
import diagnose_stereo_low_excitation as lowdiag  # noqa: E402
from ego_vio.vio.right_stereo_motion import estimate_right_motion_from_correspondences  # noqa: E402

SCHEMA = "umi_independent_right_stereo_diagnostic_v1"
PRIMARY_LEFT_REPORT = "stereo_scale_bidirectional_report.json"
RIGHT_PRIMARY_REPORT = "stereo_scale_right_report.json"
SIFT_PARAMS = {"nfeatures": 4000, "contrastThreshold": 0.01, "edgeThreshold": 15, "ratio": 0.75}
PNP_PARAMS = {"pnp_iterations": 1000, "pnp_reprojection_error_px": 4.0, "refine_pnp": True, "pnp_rotation_mode": "free"}
NUM_DISPARITIES, MIN_DEPTH_M, MAX_DEPTH_M = 128, 0.07, 0.6


@dataclass(frozen=True)
class Pair:
    first_index: int
    second_index: int
    source_class: str
    source_observation: dict[str, Any]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(paths: dict[str, Path]) -> dict[str, dict[str, str]]:
    out = {}
    for label, path in paths.items():
        path = Path(path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"missing consumed source {label}: {path}")
        out[label] = {"path": str(path), "sha256": file_sha256(path)}
    return out


def compare_snapshot(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return [f"hash changed: {label}: {entry.get('path')}" for label, entry in before.items() if after.get(label) != entry]


def _uniform(items: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if count <= 0 or not items:
        return []
    if len(items) <= count:
        return list(items)
    return [items[int(i)] for i in np.rint(np.linspace(0, len(items) - 1, count)).astype(int)]


def select_balanced_pairs(report: dict[str, Any], *, max_pairs: int) -> tuple[list[Pair], dict[str, Any]]:
    if max_pairs < 1:
        raise ValueError("max_pairs must be positive")
    ordered = [o for o in report.get("observations", []) if "first_index" in o and "second_index" in o]
    accepted = [o for o in ordered if o.get("accepted") is True]
    rejected = [o for o in ordered if o.get("accepted") is not True]
    target = max_pairs // 2
    chosen_a, chosen_r = _uniform(accepted, min(target, len(accepted))), _uniform(rejected, min(target, len(rejected)))
    used = {id(o) for o in [*chosen_a, *chosen_r]}
    remaining = max_pairs - len(chosen_a) - len(chosen_r)
    if remaining:
        pool = [o for o in (accepted if len(chosen_a) < target else rejected) if id(o) not in used]
        if len(pool) < remaining:
            pool = [o for o in ordered if id(o) not in used]
        for obs in _uniform(pool, min(remaining, len(pool))):
            (chosen_a if obs.get("accepted") is True else chosen_r).append(obs)
    pairs = [
        Pair(int(o["first_index"]), int(o["second_index"]), label, o)
        for label, rows in (("accepted_left_source", chosen_a), ("rejected_left_source", chosen_r))
        for o in rows
    ]
    pairs.sort(key=lambda p: (p.first_index, p.second_index, p.source_class))
    return pairs, {
        "rule": "uniform_6_accepted_left_plus_6_rejected_left_fill_missing_uniform_other_class",
        "max_pairs": max_pairs,
        "source_accepted_available": len(accepted),
        "source_rejected_available": len(rejected),
        "selected_accepted": sum(p.source_class == "accepted_left_source" for p in pairs),
        "selected_rejected": sum(p.source_class == "rejected_left_source" for p in pairs),
        "selected_total": len(pairs),
    }


def validate_pair_source_times(pair: Pair) -> None:
    for key in ("first_t_sec", "second_t_sec"):
        if key not in pair.source_observation:
            raise ValueError(f"source LEFT observation missing {key}: {pair.first_index}->{pair.second_index}")
        if not np.isfinite(float(pair.source_observation[key])):
            raise ValueError(f"source LEFT observation has non-finite {key}: {pair.first_index}->{pair.second_index}")


def load_manifest_records(path: Path, datasets: list[str]) -> list[dict[str, Any]]:
    records = read_json(path).get("records")
    if not isinstance(records, list):
        raise ValueError("manifest records missing")
    wanted = set(datasets)
    selected = [r for r in records if not wanted or r.get("id") in wanted]
    missing = sorted(wanted - {r.get("id") for r in selected})
    if missing:
        raise ValueError(f"manifest missing requested datasets: {missing}")
    return selected


def load_consistent_stage(path: Path) -> dict[str, Any]:
    report_path = path / "preflight_report.json"
    report = read_json(report_path)
    if report.get("external_ground_truth_used") is not False or report.get("slam_supervision") is not False:
        raise ValueError("source stage is not onboard-only")
    rows = report.get("records")
    if not isinstance(rows, list):
        raise ValueError("source stage records missing")
    by_id = {}
    for row in rows:
        rid = row.get("id")
        if not rid or rid in by_id:
            raise ValueError("source stage duplicate/missing id")
        by_id[str(rid)] = row
    return {"path": report_path.resolve(), "sha256": file_sha256(report_path), "by_id": by_id}


def primary_left_report(stage_row: dict[str, Any], record_id: str) -> Path:
    matches = [Path(p).resolve() for p in stage_row.get("refined_left_sources", []) if Path(p).name == PRIMARY_LEFT_REPORT]
    if len(matches) != 1:
        raise ValueError(f"{record_id} does not bind one primary refined LEFT report")
    return matches[0]


def original_right_report(stage_row: dict[str, Any], record_id: str) -> Path:
    mapping = stage_row.get("right_derivation_left_source_sha256")
    if not isinstance(mapping, dict):
        raise ValueError(f"{record_id} lacks RIGHT derivation provenance")
    matches = [Path(v["original_right_source"]).resolve() for k, v in mapping.items() if Path(k).name == RIGHT_PRIMARY_REPORT and isinstance(v, dict)]
    if len(matches) != 1:
        raise ValueError(f"{record_id} does not bind one original RIGHT primary report")
    return matches[0]


def validate_stage_hash_bindings(stage_row: dict[str, Any], left_path: Path, right_path: Path, right_traj: Path) -> None:
    overrides = stage_row.get("source_override_sha256")
    mapping = stage_row.get("right_derivation_left_source_sha256")
    refined_right = {str(Path(p).resolve()) for p in stage_row.get("refined_right_sources", [])}
    if not isinstance(overrides, dict) or not isinstance(mapping, dict):
        raise ValueError("source stage lacks override/right provenance hashes")
    left_key, right_match = str(left_path.resolve()), None
    if overrides.get(left_key) != file_sha256(left_path):
        raise ValueError(f"refined LEFT source_override_sha256 mismatch: {left_path}")
    for key, value in mapping.items():
        if Path(key).name == RIGHT_PRIMARY_REPORT:
            right_match = (str(Path(key).resolve()), value)
            break
    if right_match is None or not refined_right or right_match[0] not in refined_right:
        raise ValueError("RIGHT mapping key is not one of refined_right_sources")
    right_key, value = right_match
    if overrides.get(right_key) != file_sha256(Path(right_key)):
        raise ValueError(f"refined RIGHT source_override_sha256 mismatch: {right_key}")
    if Path(value.get("left_source_path", "")).resolve() != left_path.resolve():
        raise ValueError("RIGHT mapping left_source_path does not match selected LEFT")
    if value.get("left_source_sha256") != file_sha256(left_path):
        raise ValueError("RIGHT mapping left_source_sha256 mismatch")
    if Path(value.get("original_right_source", "")).resolve() != right_path.resolve():
        raise ValueError("RIGHT mapping original_right_source mismatch")
    if value.get("original_right_sha256") != file_sha256(right_path):
        raise ValueError("RIGHT mapping original_right_sha256 mismatch")
    if stage_row.get("right_raw_geometry_trajectory_sha256") != file_sha256(right_traj):
        raise ValueError("RIGHT raw geometry trajectory sha256 mismatch")


def validate_report(report: dict[str, Any], path: Path, record: dict[str, Any], frame: str) -> None:
    if report.get("external_ground_truth_used") is not False:
        raise ValueError(f"report used GT: {path}")
    if report.get("slam_supervision") is not False:
        raise ValueError(f"report used SLAM supervision: {path}")
    if report.get("observation_frame") != frame:
        raise ValueError(f"report frame mismatch: {path}")
    if Path(report.get("session", "")).resolve() != Path(record["session"]).resolve():
        raise ValueError(f"report session mismatch: {path}")
    if report.get("correspondence_estimator", "classical") != "classical":
        raise ValueError("diagnostic refuses MASt3R/GPU correspondence reports")


def sift_correspondences(image_i: np.ndarray, image_j: np.ndarray, mask_i: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    detector = cv2.SIFT_create(**{k: SIFT_PARAMS[k] for k in ("nfeatures", "contrastThreshold", "edgeThreshold")})
    k_i, d_i = detector.detectAndCompute(image_i, mask_i.astype(np.uint8) * 255)
    k_j, d_j = detector.detectAndCompute(image_j, None)
    if d_i is None or d_j is None:
        empty = np.empty((0, 2), dtype=np.float32)
        return empty, empty, np.zeros(0, dtype=bool)
    good = [
        row[0]
        for row in cv2.BFMatcher(cv2.NORM_L2).knnMatch(d_i, d_j, k=2)
        if len(row) == 2 and row[0].distance < SIFT_PARAMS["ratio"] * row[1].distance
    ]
    return (
        np.asarray([k_i[m.queryIdx].pt for m in good], dtype=np.float32).reshape(-1, 2),
        np.asarray([k_j[m.trainIdx].pt for m in good], dtype=np.float32).reshape(-1, 2),
        np.ones(len(good), dtype=bool),
    )


def estimate_left_motion(left_i, left_j, disp_l, disp_r, positions, rotations, pair: Pair, calibration):
    pts_i, pts_j, valid = sift_correspondences(left_i, left_j, disp_l > 0.5)
    return stereo.estimate_motion_from_correspondences(
        pts_i, pts_j, valid, disp_l, disp_r, positions[pair.first_index], positions[pair.second_index],
        rotations[pair.first_index], rotations[pair.second_index], calibration, MIN_DEPTH_M, MAX_DEPTH_M,
        "sift", trajectory_frame="infrared_left", **PNP_PARAMS,
    )


def estimate_right_motion(right_i, right_j, disp_l, disp_r, positions, rotations, pair: Pair, calibration):
    pts_i, pts_j, valid = sift_correspondences(right_i, right_j, (disp_r < -0.5) & (disp_r > -float(NUM_DISPARITIES)))
    return estimate_right_motion_from_correspondences(
        pts_i, pts_j, valid, disp_l, disp_r, positions[pair.first_index], positions[pair.second_index],
        rotations[pair.first_index], rotations[pair.second_index], calibration, MIN_DEPTH_M, MAX_DEPTH_M, "sift", **PNP_PARAMS,
    )


def _result_summary(result: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "accepted", "reason", "method", "tracked_points", "pnp_inliers", "pnp_inlier_ratio",
        "pnp_refined", "pnp_reprojection_median_px", "pnp_reprojection_p95_px",
        "metric_distance_m", "direction_cosine", "rotation_error_deg", "metric_displacement_frame",
        "right_centric_motion_source",
    )
    return {key: result.get(key) for key in keys if key in result}


def factory_closure(left: dict[str, Any], right: dict[str, Any], calibration: dict[str, Any]) -> dict[str, Any] | None:
    if not left.get("accepted") or not right.get("accepted"):
        return None
    left_r = Rotation.from_quat(left["pnp_rotation_quaternion_xyzw"])
    right_r = Rotation.from_quat(right["pnp_rotation_quaternion_xyzw"])
    right_t = -right_r.apply(np.asarray(right["metric_displacement_camera_i_m"], dtype=float))
    right_from_left = Rotation.from_matrix(np.asarray(calibration["right_rotation_from_left"], dtype=float))
    left_from_right = right_from_left.inv()
    left_t_right = -left_from_right.apply(np.asarray(calibration["right_translation_from_left_m"], dtype=float))
    right_as_left_r, right_as_left_t = stereo.change_relative_pose_frame(right_r, right_t, left_from_right, left_t_right)
    right_as_left_d = -right_as_left_r.inv().apply(right_as_left_t)
    left_d = np.asarray(left["metric_displacement_camera_i_m"], dtype=float)
    return {
        "right_converted_to_left_frame_by_factory": True,
        "vector_closure_m": float(np.linalg.norm(left_d - right_as_left_d)),
        "vector_closure_mm": float(1000.0 * np.linalg.norm(left_d - right_as_left_d)),
        "rotation_closure_deg": float(np.degrees((left_r.inv() * right_as_left_r).magnitude())),
        "left_metric_displacement_camera_i_m": left_d.tolist(),
        "right_as_left_metric_displacement_camera_i_m": right_as_left_d.tolist(),
    }


def diagnose_pair(pair: Pair, *, left_numbers, right_numbers, left_images, right_images, positions_left, rotations_left, positions_right, rotations_right, calibration, left_times, right_times):
    first, second = pair.first_index, pair.second_index
    left_i, left_j = left_images[int(left_numbers[first])], left_images[int(left_numbers[second])]
    right_i, right_j = right_images[int(right_numbers[first])], right_images[int(right_numbers[second])]
    disp_l, disp_r = stereo.stereo_disparity(left_i, right_i, NUM_DISPARITIES)
    left_result = estimate_left_motion(left_i, left_j, disp_l, disp_r, positions_left, rotations_left, pair, calibration)
    right_result = estimate_right_motion(right_i, right_j, disp_l, disp_r, positions_right, rotations_right, pair, calibration)
    return {
        "first_index": first, "second_index": second, "source_class": pair.source_class,
        "source_left_accepted": pair.source_observation.get("accepted") is True,
        "source_left_reason": pair.source_observation.get("reason"),
        "first_t_sec": float(left_times[first]), "second_t_sec": float(left_times[second]),
        "right_first_t_sec": float(right_times[first]), "right_second_t_sec": float(right_times[second]),
        "first_left_frame_number": int(left_numbers[first]), "second_left_frame_number": int(left_numbers[second]),
        "first_right_frame_number": int(right_numbers[first]), "second_right_frame_number": int(right_numbers[second]),
        "fresh_left": _result_summary(left_result), "fresh_right": _result_summary(right_result),
        "cross_class": ("L" if left_result.get("accepted") else "notL") + "_" + ("R" if right_result.get("accepted") else "notR"),
        "factory_frame_closure": factory_closure(left_result, right_result, calibration),
    }


def cross_matrix(rows: list[dict[str, Any]]) -> dict[str, int]:
    matrix = {"both": 0, "left_only": 0, "right_only": 0, "neither": 0}
    for row in rows:
        left, right = row["fresh_left"].get("accepted") is True, row["fresh_right"].get("accepted") is True
        matrix["both" if left and right else "left_only" if left else "right_only" if right else "neither"] += 1
    return matrix


def _record_sources(record, stage_row, baseline):
    left_path, right_path = primary_left_report(stage_row, record["id"]), original_right_report(stage_row, record["id"])
    left_report, right_report = read_json(left_path), read_json(right_path)
    validate_report(left_report, left_path, record, "infrared_left_camera_i")
    validate_report(right_report, right_path, record, "infrared_right_camera_i")
    if "derived_from_left_stereo_report" not in right_report:
        raise ValueError("RIGHT provenance must be explicit before independent replay")
    left_traj = lowdiag._path_from_report(left_report, "trajectory")
    right_traj = Path(stage_row.get("right_raw_geometry_trajectory", "")).resolve()
    db3 = lowdiag._path_from_report(left_report, "db3")
    if left_traj is None or db3 is None or not right_traj.is_file():
        raise ValueError(f"missing raw sources for {record['id']}")
    if Path(right_report.get("trajectory", "")).resolve() != right_traj:
        raise ValueError(f"RIGHT report/raw trajectory mismatch: {record['id']}")
    validate_stage_hash_bindings(stage_row, left_path, right_path, right_traj)
    consumed = {
        "manifest_record_session_frames": Path(record["session"]).resolve() / "d405_frames.csv",
        "left_source_report": left_path, "right_binding_report": right_path,
        "left_raw_trajectory": left_traj, "right_raw_trajectory": right_traj, "db3": db3,
        "baseline_candidate": baseline / record["id"] / "both" / "candidate_manifest.json",
        "this_script": Path(__file__).resolve(), "alignment_module": Path(stereo.__file__).resolve(),
        "lowdiag_module": Path(lowdiag.__file__).resolve(), "right_helper_module": ROOT / "ego_vio/vio/right_stereo_motion.py",
    }
    return left_path, right_path, left_report, left_traj, right_traj, db3, consumed


def run_record(record: dict[str, Any], stage: dict[str, Any], baseline: Path, output: Path, max_pairs: int, manifest: Path | None = None) -> dict[str, Any]:
    record_id = record["id"]
    stage_row = stage["by_id"].get(record_id)
    if stage_row is None:
        raise ValueError(f"source stage missing record: {record_id}")
    left_path, right_path, left_report, left_traj, right_traj, db3, consumed = _record_sources(record, stage_row, baseline)
    consumed["source_stage_preflight"] = stage["path"]
    if manifest is not None:
        consumed["manifest"] = manifest
    pairs, selection = select_balanced_pairs(left_report, max_pairs=max_pairs)
    if selection["selected_total"] != max_pairs:
        raise ValueError(f"selected pair count {selection['selected_total']} != max_pairs {max_pairs}")
    before = snapshot(consumed)
    left_times, left_pos, left_quat, _ = stereo.load_trajectory(left_traj)
    right_times, right_pos, right_quat, _ = stereo.load_trajectory(right_traj)
    if len(left_times) != len(right_times) or np.max(np.abs(left_times - right_times)) > 0.010:
        raise ValueError(f"LEFT/RIGHT raw trajectories do not time-bind within 10ms: {record_id}")
    selected = {i for pair in pairs for i in (pair.first_index, pair.second_index)}
    for pair in pairs:
        validate_pair_source_times(pair)
        lowdiag.validate_pair_time_binding(lowdiag.Pair(pair.first_index, pair.second_index, None, pair.source_observation), left_times)
    left_nums, right_nums, sync, calib, left_imgs, right_imgs = lowdiag._load_images(
        left_report, Path(record["session"]).resolve(), db3, "infrared_left", left_times, selected
    )
    rotations_left, rotations_right = Rotation.from_quat(left_quat), Rotation.from_quat(right_quat)
    diagnostics = [
        diagnose_pair(
            pair, left_numbers=left_nums, right_numbers=right_nums, left_images=left_imgs, right_images=right_imgs,
            positions_left=left_pos, rotations_left=rotations_left, positions_right=right_pos, rotations_right=rotations_right,
            calibration=calib, left_times=left_times, right_times=right_times,
        )
        for pair in pairs
    ]
    after = snapshot(consumed)
    hash_failures = compare_snapshot(before, after)
    return {
        "id": record_id, "status": "PASS" if not hash_failures else "FAIL",
        "baseline_artifact": str((baseline / record_id / "both").resolve()),
        "source_left_report": str(left_path), "source_right_binding_report": str(right_path),
        "right_report_note": "used only for raw RIGHT trajectory/provenance binding; measurements are fresh independent RIGHT pixels",
        "left_raw_trajectory": str(left_traj), "right_raw_trajectory": str(right_traj),
        "selection": selection, "cross_matrix": cross_matrix(diagnostics), "synchronization": sync,
        "factory_stereo_calibration_summary": {"baseline_m": calib.get("baseline_m"), "factory_topics": calib.get("factory_topics")},
        "provenance_sha256": {"guarded_before": before, "guarded_after": after},
        "hash_failures": hash_failures, "diagnostics": diagnostics,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError(f"output must be new: {args.output}")
    records, stage = load_manifest_records(args.manifest, args.dataset), load_consistent_stage(args.source_stage)
    results, failures = [], []
    for record in records:
        try:
            results.append(run_record(record, stage, args.baseline, args.output, args.max_pairs, args.manifest))
        except Exception as error:
            failures.append({"id": record.get("id"), "error": f"{type(error).__name__}: {error}"})
    output = {
        "schema": SCHEMA, "status": "PASS" if not failures and all(r["status"] == "PASS" for r in results) else "FAIL",
        "development_only": True, "external_ground_truth_used": False, "slam_supervision": False,
        "factors_emitted": 0, "backend_launched": False, "scoring_launched": False, "gpu_model_used": False,
        "policy": {
            "pair_sampling": "6 accepted + 6 rejected LEFT primary source rows, fill missing class uniformly",
            "num_disparities": NUM_DISPARITIES, "min_depth_m": MIN_DEPTH_M, "max_depth_m": MAX_DEPTH_M,
            "sift": SIFT_PARAMS, "pnp": PNP_PARAMS,
            "right_measurement": "fresh independent RIGHT pixels via mirrored native estimator",
        },
        "manifest": str(args.manifest.resolve()), "baseline": str(args.baseline.resolve()),
        "source_stage": str(args.source_stage.resolve()), "record_count": len(records),
        "records": results, "failures": failures,
    }
    write_json(args.output / "independent_right_stereo_diagnostic.json", output)
    return output


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--source-stage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", action="append", default=[])
    parser.add_argument("--max-pairs", type=int, default=12)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = argument_parser().parse_args(argv)
    if args.max_pairs < 1 or args.max_pairs > 64:
        raise SystemExit("--max-pairs must be in [1, 64]")
    result = run(args)
    print(json.dumps({"status": result["status"], "records": len(result["records"]), "failures": result["failures"]}, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
