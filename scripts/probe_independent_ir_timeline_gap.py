#!/usr/bin/env python3
"""Source-only native IR geometry probe for D405 timeline gaps.

This development diagnostic samples fixed full-session D405 frame pairs after
both MASt3R raw frontends have ended, then evaluates the existing native
LEFT/RIGHT stereo SIFT+PnP bidirectional geometry.  It does not emit factors,
run a backend, score, use GT/Tracker inputs, or promote measurements to
production.  VINS endpoint poses are used only as the existing native PnP
direction/scale consistency reference, so accepted rows are onboard-supported
but not independent of VINS direction/scale gates.
"""

from __future__ import annotations

import argparse
import csv
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

import align_mast3r_scale_with_stereo as stereo  # noqa: E402
import diagnose_independent_right_stereo as diag  # noqa: E402
import diagnose_stereo_low_excitation as lowdiag  # noqa: E402
import fuse_mast3r_stereo_imu as fusion  # noqa: E402
from ego_vio.vio.cached_ir_correspondences import CachedIrSiftCorrespondences  # noqa: E402
from ego_vio.vio.right_stereo_motion import estimate_right_motion_from_correspondences  # noqa: E402


SCHEMA = "umi_independent_ir_timeline_gap_probe_v1"
READY_STATUS = "TIMELINE_GAP_SOURCE_PROBE_READY"
FAILED_STATUS = "TIMELINE_GAP_SOURCE_PROBE_FAILED"
DEFAULT_MAX_PAIRS = 12
PAIR_SPAN_FRAMES = 30
MAX_VINS_BIND_DELTA_S = 0.010
NUM_DISPARITIES = diag.NUM_DISPARITIES
MIN_DEPTH_M = diag.MIN_DEPTH_M
MAX_DEPTH_M = diag.MAX_DEPTH_M
PNP_PARAMS = diag.PNP_PARAMS


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def path_entry(path: Path) -> dict[str, str]:
    resolved = Path(path).resolve()
    return {"path": str(resolved), "sha256": file_sha256(resolved)}


def snapshot(paths: dict[str, Path]) -> dict[str, dict[str, str]]:
    return {label: path_entry(path) for label, path in paths.items()}


def assert_unchanged(before: dict[str, dict[str, str]], after: dict[str, dict[str, str]] | None = None) -> None:
    changed = []
    for label, item in before.items():
        path = Path(item["path"])
        digest = after[label]["sha256"] if after is not None and label in after else file_sha256(path)
        if digest != item["sha256"]:
            changed.append(f"{label}: {path}")
    if changed:
        raise ValueError(f"consumed source changed during timeline-gap probe: {changed}")


def load_manifest_records(path: Path) -> dict[str, dict[str, Any]]:
    doc = read_json(path)
    rows = doc.get("records")
    if not isinstance(rows, list):
        raise ValueError("manifest records missing")
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        record_id = row.get("id")
        if not isinstance(record_id, str) or not record_id or record_id in by_id:
            raise ValueError("manifest has missing/duplicate record id")
        by_id[record_id] = row
    return by_id


def _input_path(candidate: dict[str, Any], name: str, *, contains: str | None = None) -> Path:
    matches = []
    for key in candidate.get("input_sha256", {}):
        path = Path(key)
        if path.name == name and (contains is None or contains in str(path)):
            matches.append(path.resolve())
    if len(matches) != 1:
        raise ValueError(f"expected exactly one candidate input named {name} contains={contains!r}; found {len(matches)}")
    if file_sha256(matches[0]) != candidate["input_sha256"][str(matches[0])]:
        raise ValueError(f"candidate input hash mismatch: {matches[0]}")
    return matches[0]


def load_frame_timeline(frame_csv: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rows = list(csv.DictReader(Path(frame_csv).open(newline="", encoding="utf-8")))
    required = {"infrared_left_device_ms", "infrared_right_device_ms", "infrared_left_frame_number", "infrared_right_frame_number"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"invalid d405 frame csv: {frame_csv}")
    left_times = np.asarray([float(row["infrared_left_device_ms"]) / 1000.0 for row in rows], dtype=float)
    right_times = np.asarray([float(row["infrared_right_device_ms"]) / 1000.0 for row in rows], dtype=float)
    left_numbers = np.asarray([int(row["infrared_left_frame_number"]) for row in rows], dtype=int)
    right_numbers = np.asarray([int(row["infrared_right_frame_number"]) for row in rows], dtype=int)
    if len(left_times) != len(right_times) or np.any(np.diff(left_times) <= 0) or np.any(np.diff(right_times) <= 0):
        raise ValueError(f"invalid/non-monotonic D405 timestamps: {frame_csv}")
    if float(np.max(np.abs(left_times - right_times))) > 0.010:
        raise ValueError(f"left/right D405 frame timestamps diverge >10ms: {frame_csv}")
    if len(set(left_numbers.tolist())) != len(left_numbers) or len(set(right_numbers.tolist())) != len(right_numbers):
        raise ValueError(f"D405 frame numbers are not unique: {frame_csv}")
    return left_times, left_numbers, right_numbers


def _last_time(path: Path) -> dict[str, Any]:
    times, _positions, _quats, _rows = stereo.load_trajectory(path)
    return {"path": str(path.resolve()), "row_count": int(len(times)), "last_t_sec": float(times[-1])}


def propose_gap_pairs(
    frame_times: np.ndarray,
    *,
    left_raw_end_t_sec: float,
    right_raw_end_t_sec: float,
    max_pairs: int = DEFAULT_MAX_PAIRS,
    span_frames: int = PAIR_SPAN_FRAMES,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if isinstance(max_pairs, bool) or max_pairs <= 0:
        raise ValueError("max_pairs must be positive")
    if isinstance(span_frames, bool) or span_frames <= 0:
        raise ValueError("span_frames must be positive")
    times = np.asarray(frame_times, dtype=float)
    if times.ndim != 1 or len(times) <= span_frames or not np.all(np.isfinite(times)) or np.any(np.diff(times) <= 0):
        raise ValueError("frame_times must be a finite increasing vector")
    gap_start = max(float(left_raw_end_t_sec), float(right_raw_end_t_sec))
    candidates = np.nonzero(times[:-span_frames] > gap_start)[0]
    pairs: list[dict[str, Any]] = []
    if len(candidates):
        chosen = np.linspace(0, len(candidates) - 1, num=min(max_pairs, len(candidates)), dtype=int)
        seen = set()
        for pos in chosen.tolist():
            first = int(candidates[pos])
            second = first + span_frames
            if (first, second) in seen:
                continue
            seen.add((first, second))
            if not (times[first] > gap_start and times[second] > gap_start):
                raise AssertionError("gap pair does not extend beyond both raw frontend ends")
            pairs.append(
                {
                    "first_index": first,
                    "second_index": second,
                    "first_t_sec": float(times[first]),
                    "second_t_sec": float(times[second]),
                    "duration_s": float(times[second] - times[first]),
                    "time_source": "d405_frames.infrared_left_device_ms/1000",
                    "source_class": "fixed_full_session_tail_gap_pair",
                }
            )
    return pairs, {
        "left_raw_end_t_sec": float(left_raw_end_t_sec),
        "right_raw_end_t_sec": float(right_raw_end_t_sec),
        "both_raw_frontend_end_t_sec": gap_start,
        "span_frames": int(span_frames),
        "requested_max_pairs": int(max_pairs),
        "candidate_first_index_count_after_both_raw_end": int(len(candidates)),
        "selected_pair_count": int(len(pairs)),
        "selection": "uniform_fixed_span_over_full_d405_timeline_tail",
    }


def bind_vins_to_frame_times(
    vins_path: Path,
    frame_times: np.ndarray,
    *,
    selected_indices: set[int] | None = None,
) -> tuple[np.ndarray, Rotation, dict[str, Any]]:
    vins_times, positions, quats, _rows = stereo.load_trajectory(vins_path)
    frame_times = np.asarray(frame_times, dtype=float)
    indices = np.searchsorted(vins_times, frame_times)
    indices = np.clip(indices, 0, len(vins_times) - 1)
    previous = np.maximum(indices - 1, 0)
    choose_previous = np.abs(vins_times[previous] - frame_times) < np.abs(vins_times[indices] - frame_times)
    indices[choose_previous] = previous[choose_previous]
    deltas = np.abs(vins_times[indices] - frame_times)
    checked = np.asarray(sorted(selected_indices), dtype=int) if selected_indices is not None else np.arange(len(frame_times))
    if checked.size == 0:
        raise ValueError("selected_indices must not be empty")
    if np.any(checked < 0) or np.any(checked >= len(frame_times)):
        raise ValueError("selected_indices out of frame timeline range")
    checked_deltas = deltas[checked]
    if np.any(checked_deltas > MAX_VINS_BIND_DELTA_S):
        first = int(checked[np.nonzero(checked_deltas > MAX_VINS_BIND_DELTA_S)[0][0]])
        raise ValueError(
            f"VINS/frame binding exceeds {MAX_VINS_BIND_DELTA_S}s at frame index {first}: {deltas[first]:.6f}s"
        )
    return positions[indices], Rotation.from_quat(quats[indices]), {
        "vins_path": str(vins_path.resolve()),
        "selected_frame_count": int(len(checked)),
        "max_abs_time_delta_s_selected": float(np.max(checked_deltas)),
        "unselected_frames_may_precede_vins_start": True,
        "uses_vins_endpoint_positions_and_rotations_for_native_direction_scale_gates": True,
        "external_ground_truth_used": False,
    }


def body_to_camera_poses(
    body_positions: np.ndarray,
    body_rotations: Rotation,
    body_t_camera: np.ndarray,
) -> tuple[np.ndarray, Rotation]:
    extrinsic = np.asarray(body_t_camera, dtype=float)
    if extrinsic.shape != (4, 4) or not np.all(np.isfinite(extrinsic)):
        raise ValueError("body_t_camera must be a finite 4x4 matrix")
    body_r_camera = Rotation.from_matrix(extrinsic[:3, :3])
    body_p_camera = extrinsic[:3, 3]
    camera_positions = np.asarray(body_positions, dtype=float) + body_rotations.apply(body_p_camera)
    camera_rotations = body_rotations * body_r_camera
    return camera_positions, camera_rotations


def body_t_right_from_left(body_t_left_ir: np.ndarray, calibration: dict[str, Any]) -> np.ndarray:
    translation = np.asarray(calibration.get("right_translation_from_left_m"), dtype=float)
    if "right_rotation_from_left" not in calibration:
        raise ValueError("native stereo calibration lacks right_rotation_from_left")
    rotation = np.asarray(calibration["right_rotation_from_left"], dtype=float)
    if translation.shape != (3,) or rotation.shape != (3, 3):
        raise ValueError("factory calibration lacks finite left-IR to right-IR transform")
    if not np.all(np.isfinite(translation)) or not np.all(np.isfinite(rotation)):
        raise ValueError("factory calibration left-IR to right-IR transform is non-finite")
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-7) or np.linalg.det(rotation) <= 0.0:
        raise ValueError("factory right_rotation_from_left is not a proper rotation")
    right_t_left = np.eye(4)
    right_t_left[:3, :3] = rotation
    right_t_left[:3, 3] = translation
    return np.asarray(body_t_left_ir, dtype=float) @ np.linalg.inv(right_t_left)


def load_selected_db3_images_direct(
    *,
    frame_csv: Path,
    db3: Path,
    times: np.ndarray,
    selected_indices: set[int],
) -> tuple[np.ndarray, np.ndarray, dict[str, Any], dict[int, Any], dict[int, Any], dict[str, Any]]:
    left_numbers, right_numbers, sync = stereo.match_trajectory_to_stereo_frames(
        frame_csv,
        times,
        trajectory_frame="infrared_left",
    )
    selected_left = {int(left_numbers[index]) for index in selected_indices}
    selected_right = {int(right_numbers[index]) for index in selected_indices}
    calibration = stereo.load_stereo_calibration(db3)
    left_images, right_images = stereo.load_selected_stereo_images(db3, selected_left, selected_right)
    return left_numbers, right_numbers, sync, calibration, left_images, right_images


def _summary(result: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "accepted", "reason", "method", "tracked_points", "pnp_inliers", "pnp_inlier_ratio",
        "pnp_refined", "pnp_reprojection_median_px", "pnp_reprojection_p95_px",
        "metric_distance_m", "metric_displacement_camera_i_m", "metric_displacement_frame",
        "pnp_rotation_quaternion_xyzw",
        "direction_cosine", "rotation_error_deg", "scale", "scale_estimator",
        "forward_scale", "reverse_scale", "bidirectional_relative_disagreement",
        "reverse_failure_reason", "right_centric_motion_source",
    )
    return {key: result.get(key) for key in keys if key in result}


def _left_motion(cache: CachedIrSiftCorrespondences, first: int, second: int, left_i, left_j, disp_l, disp_r, positions, rotations, calibration):
    pts_i, pts_j, valid = cache.correspondences(first, left_i, disp_l > 0.5, second, left_j)
    return stereo.estimate_motion_from_correspondences(
        pts_i, pts_j, valid, disp_l, disp_r,
        positions[first], positions[second], rotations[first], rotations[second],
        calibration, MIN_DEPTH_M, MAX_DEPTH_M, "sift",
        trajectory_frame="infrared_left", **PNP_PARAMS,
    )


def _right_motion(cache: CachedIrSiftCorrespondences, first: int, second: int, right_i, right_j, disp_l, disp_r, positions, rotations, calibration):
    pts_i, pts_j, valid = cache.correspondences(first, right_i, (disp_r < -0.5) & (disp_r > -float(NUM_DISPARITIES)), second, right_j)
    return estimate_right_motion_from_correspondences(
        pts_i, pts_j, valid, disp_l, disp_r,
        positions[first], positions[second], rotations[first], rotations[second],
        calibration, MIN_DEPTH_M, MAX_DEPTH_M, "sift", **PNP_PARAMS,
    )


def evaluate_pairs(
    pairs: list[dict[str, Any]],
    *,
    left_numbers: np.ndarray,
    right_numbers: np.ndarray,
    left_images: dict[int, Any],
    right_images: dict[int, Any],
    positions_left: np.ndarray,
    rotations_left: Rotation,
    positions_right: np.ndarray,
    rotations_right: Rotation,
    calibration: dict[str, Any],
) -> list[dict[str, Any]]:
    left_cache = CachedIrSiftCorrespondences(max_feature_entries=max(4, 4 * len(pairs)))
    right_cache = CachedIrSiftCorrespondences(max_feature_entries=max(4, 4 * len(pairs)))
    rows = []
    for pair in pairs:
        first = int(pair["first_index"])
        second = int(pair["second_index"])
        left_i, left_j = left_images[int(left_numbers[first])], left_images[int(left_numbers[second])]
        right_i, right_j = right_images[int(right_numbers[first])], right_images[int(right_numbers[second])]
        disp_i_l, disp_i_r = stereo.stereo_disparity(left_i, right_i, NUM_DISPARITIES)
        left_f = _left_motion(left_cache, first, second, left_i, left_j, disp_i_l, disp_i_r, positions_left, rotations_left, calibration)
        right_f = _right_motion(right_cache, first, second, right_i, right_j, disp_i_l, disp_i_r, positions_right, rotations_right, calibration)
        disp_j_l, disp_j_r = stereo.stereo_disparity(left_j, right_j, NUM_DISPARITIES)
        left_r = _left_motion(left_cache, second, first, left_j, left_i, disp_j_l, disp_j_r, positions_left, rotations_left, calibration)
        right_r = _right_motion(right_cache, second, first, right_j, right_i, disp_j_l, disp_j_r, positions_right, rotations_right, calibration)
        left_c = stereo.combine_bidirectional_scale(left_f, left_r)
        right_c = stereo.combine_bidirectional_scale(right_f, right_r)
        rows.append(
            {
                **pair,
                "first_left_frame_number": int(left_numbers[first]),
                "second_left_frame_number": int(left_numbers[second]),
                "first_right_frame_number": int(right_numbers[first]),
                "second_right_frame_number": int(right_numbers[second]),
                "raw_forward_left": _summary(left_f),
                "raw_reverse_left": _summary(left_r),
                "native_combined_left": _summary(left_c),
                "raw_forward_right": _summary(right_f),
                "raw_reverse_right": _summary(right_r),
                "native_combined_right": _summary(right_c),
                "native_bidirectional_cross_class": ("L" if left_c.get("accepted") else "notL")
                + "_"
                + ("R" if right_c.get("accepted") else "notR"),
                "factory_frame_closure": diag.factory_closure(left_c, right_c, calibration),
            }
        )
    return rows


def _paths_from_candidate(candidate_path: Path, record: dict[str, Any]) -> dict[str, Path]:
    candidate = read_json(candidate_path)
    if candidate.get("schema") != "umi_dual_ir_symmetric_experiment_v1":
        raise ValueError("baseline candidate schema mismatch")
    if Path(candidate.get("session", "")).resolve() != Path(record["session"]).resolve():
        raise ValueError("baseline candidate session mismatch")
    return {
        "candidate_manifest": candidate_path.resolve(),
        "left_metric_trajectory": _input_path(candidate, "trajectory_imu_metric.csv"),
        "right_metric_trajectory": _input_path(candidate, "imu_metric_trajectory.csv"),
        "vins_trajectory": _input_path(candidate, "vio_corrected_stream.csv"),
        "left_bidirectional_report": _input_path(candidate, "stereo_scale_bidirectional_report.json", contains="/mast3r/"),
        "d405_frames": _input_path(candidate, "d405_frames.csv"),
        "vins_config": _input_path(candidate, "vins_config.yaml"),
    }


def run_record(record: dict[str, Any], *, baseline: Path, output: Path, max_pairs: int) -> dict[str, Any]:
    record_id = record["id"]
    record_output = output / record_id
    if record_output.exists() or record_output.is_symlink():
        raise ValueError(f"record output must be new: {record_output}")
    candidate_path = baseline / record_id / "both" / "candidate_manifest.json"
    paths = _paths_from_candidate(candidate_path, record)
    paths["this_script"] = Path(__file__).resolve()
    paths["alignment_module"] = Path(stereo.__file__).resolve()
    paths["right_helper_module"] = ROOT / "ego_vio/vio/right_stereo_motion.py"
    paths["cached_correspondence_module"] = ROOT / "ego_vio/vio/cached_ir_correspondences.py"
    left_report = read_json(paths["left_bidirectional_report"])
    db3 = lowdiag._path_from_report(left_report, "db3")
    if db3 is None:
        raise ValueError("left report lacks db3 image source")
    paths["db3"] = db3.resolve()
    before = snapshot(paths)

    frame_times, frame_left_numbers, frame_right_numbers = load_frame_timeline(paths["d405_frames"])
    left_end = _last_time(paths["left_metric_trajectory"])
    right_end = _last_time(paths["right_metric_trajectory"])
    pairs, pair_policy = propose_gap_pairs(
        frame_times,
        left_raw_end_t_sec=left_end["last_t_sec"],
        right_raw_end_t_sec=right_end["last_t_sec"],
        max_pairs=max_pairs,
    )
    if not pairs:
        raise ValueError(f"no full-session D405 tail pairs after both raw frontend ends for {record_id}")

    selected = {int(row["first_index"]) for row in pairs} | {int(row["second_index"]) for row in pairs}
    body_positions, body_rotations, vins_binding = bind_vins_to_frame_times(
        paths["vins_trajectory"],
        frame_times,
        selected_indices=selected,
    )
    image_times = frame_times
    _left_numbers, _right_numbers, sync, calibration, left_images, right_images = load_selected_db3_images_direct(
        frame_csv=paths["d405_frames"],
        db3=db3,
        times=image_times,
        selected_indices=selected,
    )
    if not np.array_equal(_left_numbers, frame_left_numbers) or not np.array_equal(_right_numbers, frame_right_numbers):
        raise ValueError("full D405 timeline frame-number binding changed during image load")
    config = fusion.load_vins_config(paths["vins_config"], -0.009109323)
    body_t_left = config["body_T_camera"]
    left_cam_positions, left_cam_rotations = body_to_camera_poses(body_positions, body_rotations, body_t_left)
    body_t_right = body_t_right_from_left(body_t_left, calibration)
    right_cam_positions, right_cam_rotations = body_to_camera_poses(body_positions, body_rotations, body_t_right)
    observations = evaluate_pairs(
        pairs,
        left_numbers=frame_left_numbers,
        right_numbers=frame_right_numbers,
        left_images=left_images,
        right_images=right_images,
        positions_left=left_cam_positions,
        rotations_left=left_cam_rotations,
        positions_right=right_cam_positions,
        rotations_right=right_cam_rotations,
        calibration=calibration,
    )
    after_check = snapshot(paths)
    assert_unchanged(before, after_check)
    counts = {}
    for row in observations:
        key = row["native_bidirectional_cross_class"]
        counts[key] = counts.get(key, 0) + 1
    report = {
        "schema": SCHEMA,
        "status": READY_STATUS,
        "id": record_id,
        "session": str(Path(record["session"]).resolve()),
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "tracker_reference_used": False,
        "backend_launched": False,
        "scorer_launched": False,
        "gpu_model_used": False,
        "production_promoted": False,
        "not_rejected_row_recovery": True,
        "source_policy": "fixed_full_d405_timeline_pairs_after_both_raw_frontend_end",
        "native_geometry_policy": {
            "sift": diag.SIFT_PARAMS,
            "pnp": PNP_PARAMS,
            "num_disparities": NUM_DISPARITIES,
            "min_depth_m": MIN_DEPTH_M,
            "max_depth_m": MAX_DEPTH_M,
            "bidirectional_scale_gate": "align_mast3r_scale_with_stereo.combine_bidirectional_scale default",
            "uses_vins_endpoint_positions_and_rotations_for_direction_scale_gates": True,
        },
        "frontend_coverage": {"left": left_end, "right": right_end, "full_d405_frame_count": int(len(frame_times))},
        "pair_policy": pair_policy,
        "vins_frame_binding": vins_binding,
        "camera_pose_conversion": {
            "source": "VINS body pose converted to LEFT/RIGHT camera poses before native PnP consistency gates",
            "body_T_left_ir": body_t_left.tolist(),
            "body_T_right_ir": body_t_right.tolist(),
            "uses_vins_endpoint_positions_and_rotations_for_direction_scale_gates": True,
        },
        "image_frame_binding": sync,
        "image_source_lineage": {
            "selected_frames_loaded_from_db3_directly": True,
            "prepared_dataset_ignored_for_full_timeline_tail_probe": left_report.get("prepared_dataset") is not None,
            "db3": str(db3.resolve()),
        },
        "consumed_source_guard": {"guarded_before_sha256": before, "guarded_after_sha256": after_check, "guarded_after_verified": True},
        "observations": observations,
        "summary": {"pair_count": len(observations), "native_bidirectional_cross_matrix": counts},
    }
    write_json(record_output / "timeline_gap_probe.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", action="append", required=True)
    parser.add_argument("--max-pairs", type=int, default=DEFAULT_MAX_PAIRS)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        raise ValueError(f"output must be a new directory: {output}")
    records_by_id = load_manifest_records(args.manifest)
    output.mkdir(parents=True)
    results = []
    failures = []
    for record_id in args.dataset:
        row = records_by_id.get(record_id)
        if row is None:
            raise ValueError(f"manifest missing requested dataset: {record_id}")
        try:
            result = run_record(row, baseline=args.baseline.resolve(), output=output, max_pairs=args.max_pairs)
            results.append({"id": record_id, "status": READY_STATUS, "path": str((output / record_id / "timeline_gap_probe.json").resolve()), "summary": result["summary"]})
            print(f"{record_id}: {READY_STATUS} {result['summary']}", flush=True)
        except Exception as exc:  # keep source failures visible per-record
            failures.append({"id": record_id, "status": FAILED_STATUS, "error": str(exc)})
            fail_path = output / record_id / "timeline_gap_probe_failed.json"
            fail_path.parent.mkdir(parents=True, exist_ok=True)
            fail_path.write_text(json.dumps(failures[-1], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"{record_id}: {FAILED_STATUS}: {exc}", flush=True)
    summary = {
        "schema": SCHEMA,
        "status": "TIMELINE_GAP_SOURCE_PROBE_COMPLETE" if not failures else "TIMELINE_GAP_SOURCE_PROBE_WITH_FAILURES",
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "backend_launched": False,
        "scorer_launched": False,
        "not_rejected_row_recovery": True,
        "manifest": path_entry(args.manifest),
        "baseline": str(args.baseline.resolve()),
        "records": results,
        "failures": failures,
    }
    (output / "preflight_report.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return 0 if not failures else 3


if __name__ == "__main__":
    raise SystemExit(main())
