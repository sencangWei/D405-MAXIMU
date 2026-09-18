#!/usr/bin/env python3
"""Independently calibrate a Vive Tracker to the Docker2 VINS body.

The AprilGrid is fixed in the room.  The D405 observes the grid while the
Lighthouse system observes the rigidly attached Tracker.  Relative motions
remove the unknown Lighthouse-to-grid transform, leaving the standard
hand-eye equation A X = X B.  The known Docker2 camera-to-body calibration can
move the AprilGrid observations into the VINS body/IMU origin before solving.
No VIO or SLAM trajectory is accepted as input.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import cv2
import numpy as np
import yaml
from scipy.optimize import minimize_scalar
from scipy.spatial.transform import Rotation

from calibrate_lighthouse_umi import (
    clean_poses,
    evaluate_offset,
    load_tracker,
    pose_matrices,
    split_half_stability,
)


def load_camera_poses(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    required = {"t_sec", "x", "y", "z", "qw", "qx", "qy", "qz"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"invalid AprilGrid camera-pose schema: {path}")
    times = np.asarray([float(row["t_sec"]) for row in rows])
    positions = np.asarray(
        [[float(row[key]) for key in ("x", "y", "z")] for row in rows]
    )
    quaternions = np.asarray(
        [
            [float(row["qx"]), float(row["qy"]), float(row["qz"]), float(row["qw"])]
            for row in rows
        ]
    )
    return clean_poses(times, positions, quaternions, path)


def map_camera_times(
    camera_times: np.ndarray,
    time_source: str,
    d405_frames_path: Path | None,
) -> tuple[np.ndarray, dict[str, object]]:
    if time_source == "host_realtime":
        return camera_times, {
            "camera_input_domain": "d405_global_time_unix_epoch_s",
            "alignment_domain": time_source,
            "d405_frames": None,
        }
    if time_source != "host_monotonic":
        raise ValueError(f"unsupported Tracker time source: {time_source}")
    if d405_frames_path is None:
        raise ValueError("--d405-frames is required for host_monotonic alignment")
    rows = list(csv.DictReader(d405_frames_path.open(newline="", encoding="utf-8")))
    offsets = []
    for row in rows:
        wall = row.get("sensor_event_wall") or row.get("arrival_wall")
        monotonic = row.get("sensor_event_mono") or row.get("arrival_mono")
        if wall and monotonic:
            offsets.append(float(wall) - float(monotonic))
    if len(offsets) < 10:
        raise ValueError(f"too few D405 wall/monotonic clock pairs: {len(offsets)}")
    offsets_array = np.asarray(offsets)
    epoch_minus_monotonic_s = float(np.median(offsets_array))
    span_us = float(np.ptp(offsets_array) * 1.0e6)
    if span_us > 1000.0:
        raise ValueError(f"D405 wall/monotonic mapping changed by {span_us:.3f} us")
    return camera_times - epoch_minus_monotonic_s, {
        "camera_input_domain": "d405_global_time_unix_epoch_s",
        "alignment_domain": "host_monotonic_s",
        "d405_frames": str(d405_frames_path.resolve()),
        "epoch_minus_monotonic_s": epoch_minus_monotonic_s,
        "mapping_samples": len(offsets),
        "mapping_span_us": span_us,
    }


def load_aprilgrid_metadata(path: Path) -> dict[str, object]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    required = {"target_type", "tagCols", "tagRows", "tagSize", "tagSpacing"}
    if not isinstance(raw, dict) or not required.issubset(raw):
        raise ValueError(f"invalid AprilGrid config: {path}")
    if raw["target_type"] != "aprilgrid":
        raise ValueError(f"expected target_type=aprilgrid in {path}")
    return {
        "family": "t36h11",
        "tag_columns": int(raw["tagCols"]),
        "tag_rows": int(raw["tagRows"]),
        "tag_size_m": float(raw["tagSize"]),
        "tag_spacing_ratio": float(raw["tagSpacing"]),
        "config": str(path.resolve()),
    }


def load_body_T_cam0(path: Path) -> np.ndarray:
    calibration = cv2.FileStorage(str(path), cv2.FileStorage_READ)
    if not calibration.isOpened():
        raise ValueError(f"cannot open body-camera calibration: {path}")
    body_T_camera = calibration.getNode("body_T_cam0").mat()
    calibration.release()
    if body_T_camera is None or body_T_camera.shape != (4, 4):
        raise ValueError(f"body_T_cam0 missing or invalid in {path}")
    body_T_camera = np.asarray(body_T_camera, dtype=float)
    if not np.all(np.isfinite(body_T_camera)):
        raise ValueError(f"body_T_cam0 contains non-finite values: {path}")
    return body_T_camera


def calibrate(
    camera_pose_path: Path,
    tracker_path: Path,
    aprilgrid_path: Path,
    time_source: str,
    d405_frames_path: Path | None,
    search_ms: float,
    max_gap_s: float,
    body_camera_config_path: Path | None = None,
    tracker_query_offset_ms: float | None = None,
) -> dict[str, object]:
    camera_times, camera_positions, camera_quaternions = load_camera_poses(
        camera_pose_path
    )
    aligned_camera_times, clock_mapping = map_camera_times(
        camera_times, time_source, d405_frames_path
    )
    tracker_times, tracker_positions, tracker_quaternions, serial = load_tracker(
        tracker_path, time_source
    )
    camera_poses = pose_matrices(camera_positions, camera_quaternions)
    body_T_camera = None
    target_poses = camera_poses
    target_frame = "d405_left_camera"
    if body_camera_config_path is not None:
        body_T_camera = load_body_T_cam0(body_camera_config_path)
        target_poses = camera_poses @ np.linalg.inv(body_T_camera)[None, :, :]
        target_frame = "docker2_vins_body"

    candidates = []
    if tracker_query_offset_ms is None:
        for offset_ms in np.arange(-search_ms, search_ms + 2.5, 5.0):
            try:
                score, transform, profile = evaluate_offset(
                    offset_ms / 1000.0,
                    aligned_camera_times,
                    target_poses,
                    tracker_times,
                    tracker_positions,
                    tracker_quaternions,
                    max_gap_s,
                )
                candidates.append((score, float(offset_ms), transform, profile))
            except ValueError:
                continue
        if not candidates:
            raise ValueError("no valid camera/Tracker clock-offset candidate")
        candidates.sort(key=lambda item: item[0])
        best_ms = candidates[0][1]

        def objective(candidate_offset_ms: float) -> float:
            try:
                return evaluate_offset(
                    candidate_offset_ms / 1000.0,
                    aligned_camera_times,
                    target_poses,
                    tracker_times,
                    tracker_positions,
                    tracker_quaternions,
                    max_gap_s,
                )[0]
            except ValueError:
                return 1.0e6

        refined = minimize_scalar(
            objective,
            bounds=(max(-search_ms, best_ms - 7.5), min(search_ms, best_ms + 7.5)),
            method="bounded",
            options={"xatol": 0.01},
        )
        offset_ms = float(refined.x)
        offset_boundary_margin_ms = search_ms - abs(offset_ms)
        time_offset_policy = "joint_camera_tracker_search_legacy"
    else:
        offset_ms = float(tracker_query_offset_ms)
        offset_boundary_margin_ms = None
        time_offset_policy = "fixed_from_independent_imu_tracker_sync"
    _, tracker_T_target, profile = evaluate_offset(
        offset_ms / 1000.0,
        aligned_camera_times,
        target_poses,
        tracker_times,
        tracker_positions,
        tracker_quaternions,
        max_gap_s,
    )
    stability = split_half_stability(
        offset_ms / 1000.0,
        aligned_camera_times,
        target_poses,
        tracker_times,
        tracker_positions,
        tracker_quaternions,
        max_gap_s,
    )
    excitation = profile["excitation"]
    residuals = profile["residuals"]
    passed = (
        profile["overlap_samples"] >= 100
        and profile["overlap_ratio"] >= 0.90
        and excitation["translation_p95_m"] >= 0.03
        and excitation["rotation_p95_deg"] >= 10.0
        and excitation["observability"]["result"] == "PASS"
        and residuals["combined_residual_mm"]["p95"] <= 10.0
        and residuals["rotation_residual_deg"]["p95"] <= 3.0
        and stability["result"] == "PASS"
        and (
            offset_boundary_margin_ms is None
            or offset_boundary_margin_ms >= 5.0
        )
    )
    tracker_T_body = tracker_T_target if body_T_camera is not None else None
    tracker_T_camera = (
        tracker_T_body @ body_T_camera
        if tracker_T_body is not None
        else tracker_T_target
    )
    result = {
        "schema": "lighthouse_d405_aprilgrid_handeye_v1",
        "result": "PASS_CANDIDATE" if passed else "DIAGNOSTIC_CANDIDATE",
        "auto_apply": False,
        "method": (
            "fixed AprilGrid body poses + Lighthouse relative-motion AX=XB"
            if tracker_T_body is not None
            else "fixed AprilGrid camera poses + Lighthouse relative-motion AX=XB"
        ),
        "calibration_target_frame": target_frame,
        "independent_ground_truth": True,
        "slam_supervision": False,
        "slam_inputs": [],
        "tracker_serial": serial,
        "tracker_time_source": time_source,
        "clock_mapping": clock_mapping,
        "tracker_query_offset_ms": offset_ms,
        "time_offset_policy": time_offset_policy,
        "offset_semantics": (
            "interpolate Tracker at AprilGrid camera timestamp + "
            "tracker_query_offset_ms"
        ),
        "offset_search": {
            "range_ms": [-search_ms, search_ms],
            "boundary_margin_ms": offset_boundary_margin_ms,
            "minimum_boundary_margin_ms": 5.0,
            "coarse_best_candidates": [
                {"offset_ms": item[1], "combined_residual_p95_mm": item[0]}
                for item in candidates[:5]
            ],
        },
        "tracker_T_d405_left_camera": tracker_T_camera.tolist(),
        "translation_norm_mm": float(
            np.linalg.norm(tracker_T_target[:3, 3]) * 1000.0
        ),
        "rotation_angle_deg": float(
            np.degrees(Rotation.from_matrix(tracker_T_target[:3, :3]).magnitude())
        ),
        "aprilgrid": load_aprilgrid_metadata(aprilgrid_path),
        "profile": profile,
        "split_half_stability": stability,
        "inputs": {
            "aprilgrid_camera_poses": str(camera_pose_path.resolve()),
            "tracker": str(tracker_path.resolve()),
            "d405_frames": (
                str(d405_frames_path.resolve())
                if d405_frames_path is not None
                else None
            ),
            "body_camera_config": (
                str(body_camera_config_path.resolve())
                if body_camera_config_path is not None
                else None
            ),
        },
        "acceptance_note": (
            "Freeze only a PASS_CANDIDATE, then evaluate SLAM on separate recordings."
        ),
    }
    if tracker_T_body is not None:
        result["tracker_T_body"] = tracker_T_body.tolist()
        result["body_T_cam0"] = body_T_camera.tolist()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera-poses", type=Path, required=True)
    parser.add_argument("--tracker", type=Path, required=True)
    parser.add_argument("--aprilgrid", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--d405-frames", type=Path)
    parser.add_argument("--body-camera-config", type=Path)
    parser.add_argument("--tracker-query-offset-ms", type=float)
    parser.add_argument(
        "--tracker-time-source",
        choices=("host_monotonic", "host_realtime"),
        default="host_monotonic",
    )
    parser.add_argument("--search-ms", type=float, default=50.0)
    parser.add_argument("--max-gap-ms", type=float, default=30.0)
    args = parser.parse_args()

    result = calibrate(
        args.camera_poses,
        args.tracker,
        args.aprilgrid,
        args.tracker_time_source,
        args.d405_frames,
        args.search_ms,
        args.max_gap_ms / 1000.0,
        args.body_camera_config,
        args.tracker_query_offset_ms,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["result"] == "PASS_CANDIDATE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
