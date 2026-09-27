#!/usr/bin/env python3
"""Generate Lighthouse camera/body ground truth at requested timestamps."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

from calibrate_lighthouse_aprilgrid import map_camera_times
from calibrate_lighthouse_umi import interpolate_tracker, load_tracker

ROOT = Path(__file__).resolve().parents[1]


def validate_official_reference(
    reference_path: Path, capture_path: Path, calibration_path: Path,
    tracker_path: Path, frames_path: Path, body_config: Path,
) -> dict[str, object]:
    """Pin the frozen candidate and reject a different source/body/clock."""
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    if (reference.get("schema") != "steamvr_aprilgrid_reference_frozen_v1"
            or reference.get("result") != "PASS_RELATIVE_MOTION_REFERENCE"
            or reference.get("reference_backend") != "steamvr_official"
            or reference.get("reference_pose_frame") != "steamvr_standing_tracker"
            or reference.get("target_frame") != "docker2_vins_body"
            or reference.get("slam_supervision") is not False):
        raise ValueError("invalid official frozen reference identity/frame")
    artifacts = reference["artifacts"]
    required = {"calibration", "original_validated_candidate", "heldout_validation",
                "calibration_capture", "heldout_capture"}
    if not required.issubset(artifacts):
        raise ValueError("reference is missing independent acceptance artifacts")
    for artifact in artifacts.values():
        path = ROOT / artifact["path"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != artifact["sha256"]:
            raise ValueError(f"frozen reference artifact changed: {path}")
    if hashlib.sha256(calibration_path.read_bytes()).hexdigest() != artifacts["calibration"]["sha256"]:
        raise ValueError("requested calibration is not the frozen candidate")
    if hashlib.sha256(body_config.read_bytes()).hexdigest() != reference["formal_configuration_sha256"]:
        raise ValueError("formal body/camera/timing configuration changed")
    calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
    original = json.loads((ROOT / artifacts["original_validated_candidate"]["path"]).read_text())
    validation = json.loads((ROOT / artifacts["heldout_validation"]["path"]).read_text())
    if (validation.get("result") != "PASS_FIXED_CANDIDATE_VALIDATION"
            or validation.get("transform_refit") is not False
            or validation.get("time_offset_refit") is not False
            or validation.get("calibration_sha256") != artifacts["original_validated_candidate"]["sha256"]
            or not np.array_equal(calibration["tracker_T_body"], original["tracker_T_body"])
            or abs(calibration["tracker_query_offset_ms"] - original["tracker_query_offset_ms"]) > 1e-12):
        raise ValueError("reference has no matching fixed heldout validation")
    capture = json.loads(capture_path.read_text(encoding="utf-8"))
    if (capture.get("status") != "PASS_CAPTURE_ONLY_NOT_CALIBRATED"
            or capture.get("reference_backend") != reference["reference_backend"]
            or capture.get("reference_pose_frame") != reference["reference_pose_frame"]
            or capture.get("serial") != reference["tracker_serial"]
            or calibration["tracker_serial"] != reference["tracker_serial"]
            or Path(capture.get("d405_session", "")).resolve() != frames_path.parent.resolve()):
        raise ValueError("current capture backend/frame/device/session mismatch")
    integrity = capture.get("tracker_integrity", {})
    if (integrity.get("status") != "PASS"
            or integrity.get("hashes", {}).get("output_sha256")
            != hashlib.sha256(tracker_path.read_bytes()).hexdigest()):
        raise ValueError("current Tracker CSV does not match its accepted capture")
    with frames_path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if not rows or not all(row.get("sensor_event_wall") and row.get("sensor_event_mono") for row in rows):
        raise ValueError("official scoring requires authoritative camera exposure clock pairs")
    return {"reference_backend": reference["reference_backend"],
            "reference_pose_frame": reference["reference_pose_frame"],
            "reference_manifest_sha256": hashlib.sha256(reference_path.read_bytes()).hexdigest(),
            "capture_manifest_sha256": hashlib.sha256(capture_path.read_bytes()).hexdigest(),
            "tracker_csv_sha256": hashlib.sha256(tracker_path.read_bytes()).hexdigest()}


def load_query_times(path: Path) -> np.ndarray:
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    if not rows or "t_sec" not in rows[0]:
        raise ValueError(f"query timestamp CSV must contain t_sec: {path}")
    times = np.asarray([float(row["t_sec"]) for row in rows], dtype=float)
    if not np.all(np.isfinite(times)) or np.any(np.diff(times) <= 0.0):
        raise ValueError(f"query timestamps must be finite and increasing: {path}")
    return times


def load_body_T_cam0(path: Path) -> np.ndarray:
    calibration = cv2.FileStorage(str(path), cv2.FileStorage_READ)
    if not calibration.isOpened():
        raise ValueError(f"cannot open body-camera calibration: {path}")
    body_T_camera = calibration.getNode("body_T_cam0").mat()
    calibration.release()
    if body_T_camera is None or body_T_camera.shape != (4, 4):
        raise ValueError(f"body_T_cam0 missing or invalid in {path}")
    return np.asarray(body_T_camera, dtype=float)


def write_ground_truth(
    query_times_path: Path,
    tracker_path: Path,
    calibration_path: Path,
    d405_frames_path: Path,
    body_camera_config: Path,
    output: Path,
    target: str,
    max_gap_s: float,
    reference_manifest_path: Path | None = None,
    capture_manifest_path: Path | None = None,
    query_time_domain: str = "camera",
) -> dict[str, object]:
    if query_time_domain not in {"camera", "imu"}:
        raise ValueError("query time domain must be camera or imu, not arrival time")
    if capture_manifest_path is not None and reference_manifest_path is None:
        raise ValueError("capture provenance requires a frozen reference manifest")
    official_provenance = {}
    if reference_manifest_path is not None:
        if not np.isfinite(max_gap_s) or not 0 < max_gap_s <= 0.03:
            raise ValueError("official reference interpolation gap must be <=30ms")
        official_provenance = validate_official_reference(
            reference_manifest_path, capture_manifest_path or tracker_path.parent / "capture_manifest.json",
            calibration_path, tracker_path, d405_frames_path, body_camera_config,
        )
    calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
    if calibration.get("schema") not in {
        "lighthouse_d405_aprilgrid_handeye_v1",
        "lighthouse_d405_aprilgrid_joint_handeye_v1",
    }:
        raise ValueError("not an independent AprilGrid Lighthouse calibration")
    if calibration.get("result") != "PASS_CANDIDATE":
        raise ValueError("refusing to apply a calibration that did not pass")
    if calibration.get("slam_supervision") is not False or calibration.get(
        "slam_inputs"
    ) != []:
        raise ValueError("calibration independence provenance is invalid")

    query_times = load_query_times(query_times_path)
    aligned_times, clock_mapping = map_camera_times(
        query_times, calibration["tracker_time_source"], d405_frames_path
    )
    tracker_times, tracker_positions, tracker_quaternions, serial = load_tracker(
        tracker_path, calibration["tracker_time_source"]
    )
    if serial != calibration["tracker_serial"]:
        raise ValueError(
            f"tracker serial mismatch: {serial} != {calibration['tracker_serial']}"
        )
    offset_ms = float(calibration["tracker_query_offset_ms"])
    time_alignment = calibration.get("time_alignment", {})
    if official_provenance or query_time_domain == "imu":
        if calibration.get("time_offset_policy") != "fixed_imu_tracker_sync_composed_with_camera_imu_td":
            raise ValueError("explicit camera/IMU/Tracker time composition is required")
        components = [float(time_alignment[key]) for key in
                      ("camera_imu_td_ms", "imu_tracker_query_offset_ms")]
        if not np.isfinite(components).all() or not np.isclose(sum(components), offset_ms, atol=1e-12, rtol=0):
            raise ValueError("calibration time components do not match effective offset")
    if query_time_domain == "imu":
        offset_ms = float(time_alignment["imu_tracker_query_offset_ms"])
    offset_s = offset_ms / 1000.0
    tracker_poses, valid = interpolate_tracker(
        aligned_times + offset_s,
        tracker_times,
        tracker_positions,
        tracker_quaternions,
        max_gap_s,
    )
    if target == "camera":
        if "tracker_T_d405_left_camera" in calibration:
            tracker_T_target = np.asarray(
                calibration["tracker_T_d405_left_camera"], dtype=float
            )
            target_transform_source = "tracker_T_d405_left_camera"
        elif "tracker_T_body" in calibration:
            body_T_camera = load_body_T_cam0(body_camera_config)
            tracker_T_target = (
                np.asarray(calibration["tracker_T_body"], dtype=float)
                @ body_T_camera
            )
            target_transform_source = "tracker_T_body * body_T_cam0"
        else:
            raise ValueError("calibration has neither camera nor body transform")
    elif target == "body":
        if "tracker_T_body" in calibration:
            tracker_T_target = np.asarray(
                calibration["tracker_T_body"], dtype=float
            )
            target_transform_source = "tracker_T_body"
        elif "tracker_T_d405_left_camera" in calibration:
            body_T_camera = load_body_T_cam0(body_camera_config)
            tracker_T_target = (
                np.asarray(
                    calibration["tracker_T_d405_left_camera"], dtype=float
                )
                @ np.linalg.inv(body_T_camera)
            )
            target_transform_source = (
                "tracker_T_d405_left_camera * inverse(body_T_cam0)"
            )
        else:
            raise ValueError("calibration has neither camera nor body transform")
    else:
        raise ValueError(f"unsupported target: {target}")
    if tracker_T_target.shape != (4, 4):
        raise ValueError(f"{target_transform_source} must produce a 4x4 transform")
    if official_provenance:
        rotation = tracker_T_target[:3, :3]
        if (not np.isfinite(tracker_T_target).all()
                or not np.allclose(tracker_T_target[3], [0, 0, 0, 1], atol=1e-8)
                or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6)
                or not np.isclose(np.linalg.det(rotation), 1, atol=1e-6)):
            raise ValueError("official reference transform is not a rigid SE3 pose")
        if len(query_times) < 1 or valid.mean() < 0.98:
            raise ValueError("official reference supports fewer than98% of requested timestamps")

    ground_truth_poses = tracker_poses @ tracker_T_target
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["t_sec", "x", "y", "z", "qw", "qx", "qy", "qz"])
        for timestamp, pose in zip(query_times[valid], ground_truth_poses):
            quaternion = Rotation.from_matrix(pose[:3, :3]).as_quat()
            writer.writerow(
                [
                    f"{timestamp:.9f}",
                    *[f"{value:.9f}" for value in pose[:3, 3]],
                    f"{quaternion[3]:.9f}",
                    f"{quaternion[0]:.9f}",
                    f"{quaternion[1]:.9f}",
                    f"{quaternion[2]:.9f}",
                ]
            )
    return {
        "schema": "lighthouse_aprilgrid_ground_truth_provenance_v1",
        "result": "PASS",
        "target": target,
        "target_transform_source": target_transform_source,
        "samples_written": int(valid.sum()),
        "query_samples": len(query_times),
        "timestamp_overlap_ratio": float(valid.mean()),
        "query_columns_used": ["t_sec"],
        "pose_columns_from_query_used": [],
        "slam_supervision": False,
        **official_provenance,
        "query_time_domain": query_time_domain,
        "clock_mapping": clock_mapping,
        "tracker_query_offset_ms": offset_ms,
        "offset_semantics": (
            f"interpolate Tracker at mapped {query_time_domain} timestamp + tracker_query_offset_ms"
        ),
        "tracker_T_target": tracker_T_target.tolist(),
        "inputs": {
            "query_timestamps": str(query_times_path.resolve()),
            "tracker": str(tracker_path.resolve()),
            "calibration": str(calibration_path.resolve()),
            "body_camera_config": str(body_camera_config.resolve()),
            "d405_frames": str(d405_frames_path.resolve()),
        },
        "sha256": {
            "calibration": hashlib.sha256(calibration_path.read_bytes()).hexdigest(),
            "body_camera_config": hashlib.sha256(
                body_camera_config.read_bytes()
            ).hexdigest(),
        },
        "output": str(output.resolve()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query-times", type=Path, required=True)
    parser.add_argument("--tracker", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--d405-frames", type=Path, required=True)
    parser.add_argument("--body-camera-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--target", choices=("body", "camera"), default="body")
    parser.add_argument("--max-gap-ms", type=float, default=30.0)
    parser.add_argument("--reference-manifest", type=Path)
    parser.add_argument("--capture-manifest", type=Path)
    parser.add_argument("--query-time-domain", choices=("camera", "imu"), default="camera")
    args = parser.parse_args()

    report = write_ground_truth(
        args.query_times,
        args.tracker,
        args.calibration,
        args.d405_frames,
        args.body_camera_config,
        args.output,
        args.target,
        args.max_gap_ms / 1000.0,
        args.reference_manifest,
        args.capture_manifest,
        args.query_time_domain,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
