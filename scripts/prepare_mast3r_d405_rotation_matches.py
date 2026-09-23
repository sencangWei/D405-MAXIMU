#!/usr/bin/env python3
"""Build high-turn D405 descriptor pairs without external ground truth."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import cv2
import numpy as np

from prepare_mast3r_d405_temporal_finetune import compose_camera_rotation


def tracked_correspondences(
    first_image: Path,
    second_image: Path,
    maximum_points: int = 512,
    maximum_forward_backward_error_px: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Return deterministic forward/backward-consistent LK tracks."""
    first = cv2.imread(str(first_image), cv2.IMREAD_GRAYSCALE)
    second = cv2.imread(str(second_image), cv2.IMREAD_GRAYSCALE)
    if first is None or second is None:
        raise FileNotFoundError(f"missing rotation-match image: {first_image} / {second_image}")
    features = cv2.goodFeaturesToTrack(
        first,
        maxCorners=max(maximum_points * 3, maximum_points),
        qualityLevel=0.003,
        minDistance=5,
        blockSize=7,
    )
    if features is None:
        return np.empty((0, 2), np.float32), np.empty((0, 2), np.float32)
    forward, forward_status, _ = cv2.calcOpticalFlowPyrLK(
        first,
        second,
        features,
        None,
        winSize=(31, 31),
        maxLevel=4,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, 0.01),
    )
    if forward is None:
        return np.empty((0, 2), np.float32), np.empty((0, 2), np.float32)
    backward, backward_status, _ = cv2.calcOpticalFlowPyrLK(
        second,
        first,
        forward,
        None,
        winSize=(31, 31),
        maxLevel=4,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, 0.01),
    )
    if backward is None:
        return np.empty((0, 2), np.float32), np.empty((0, 2), np.float32)
    first_points = features.reshape(-1, 2)
    second_points = forward.reshape(-1, 2)
    backward_points = backward.reshape(-1, 2)
    valid = forward_status.ravel().astype(bool) & backward_status.ravel().astype(bool)
    valid &= np.linalg.norm(backward_points - first_points, axis=1) <= float(
        maximum_forward_backward_error_px
    )
    valid &= np.isfinite(first_points).all(axis=1) & np.isfinite(second_points).all(axis=1)
    first_points = first_points[valid][:maximum_points].astype(np.float32)
    second_points = second_points[valid][:maximum_points].astype(np.float32)
    return first_points, second_points


def build_rotation_match_manifest(
    temporal_manifest_path: Path,
    output: Path,
    minimum_angular_speed_deg_s: float = 30.0,
    maximum_points: int = 512,
    minimum_points: int = 256,
    maximum_forward_backward_error_px: float = 1.0,
) -> dict:
    temporal = json.loads(temporal_manifest_path.read_text(encoding="utf-8"))
    if temporal.get("external_ground_truth_used") is not False:
        raise ValueError("temporal manifest must exclude external ground truth")
    stereo_path = Path(temporal["source_stereo_manifest"])
    stereo = json.loads(stereo_path.read_text(encoding="utf-8"))
    if stereo.get("external_ground_truth_used") is not False:
        raise ValueError("stereo manifest must exclude external ground truth")
    samples_by_key = {
        (sample["session_id"], int(sample["input_index"])): sample
        for sample in stereo["samples"]
    }
    export_roots = {
        item["session_id"]: Path(item["dataset_manifest"]).parent
        for item in stereo["source_exports"]
    }
    priors_by_session: dict[str, list[dict[str, str]]] = {}
    samples = []
    rejected_counts: dict[str, int] = {}
    for rejected in temporal["rejected"]:
        if rejected.get("reason") != "reprojection_p95_high":
            continue
        session_id = rejected["session_id"]
        first_index = int(rejected["first_input_index"])
        second_index = int(rejected["second_input_index"])
        first = samples_by_key[(session_id, first_index)]
        second = samples_by_key[(session_id, second_index)]
        priors = priors_by_session.get(session_id)
        if priors is None:
            prior_path = export_roots[session_id] / "imu_rotation_priors.csv"
            priors = list(
                csv.DictReader(prior_path.open(newline="", encoding="utf-8"))
            )
            priors_by_session[session_id] = priors
        imu_rotation = compose_camera_rotation(priors, first_index, second_index)
        duration_s = float(second["timestamp_s"] - first["timestamp_s"])
        angle_deg = float(np.degrees(imu_rotation.magnitude()))
        angular_speed = angle_deg / duration_s
        if angular_speed < minimum_angular_speed_deg_s:
            rejected_counts["angular_speed_low"] = (
                rejected_counts.get("angular_speed_low", 0) + 1
            )
            continue
        points_first, points_second = tracked_correspondences(
            Path(first["left_image"]),
            Path(second["left_image"]),
            maximum_points=maximum_points,
            maximum_forward_backward_error_px=maximum_forward_backward_error_px,
        )
        if len(points_first) < minimum_points:
            rejected_counts["insufficient_consistent_tracks"] = (
                rejected_counts.get("insufficient_consistent_tracks", 0) + 1
            )
            continue
        pose = np.eye(4, dtype=np.float32)
        pose[:3, :3] = imu_rotation.as_matrix().astype(np.float32)
        samples.append(
            {
                "session_id": session_id,
                "split": first["split"],
                "first_input_index": first_index,
                "second_input_index": second_index,
                "first_image": first["left_image"],
                "second_image": second["left_image"],
                "depth_first": first["depth_left"],
                "depth_second": second["depth_left"],
                "intrinsics": first["intrinsics"],
                "camera_pose_second": pose.tolist(),
                "duration_s": duration_s,
                "imu_rotation_deg": angle_deg,
                "angular_speed_deg_s": angular_speed,
                "source_reprojection_p95_px": float(
                    rejected["reprojection_p95_px"]
                ),
                "flow_corres_first": points_first.tolist(),
                "flow_corres_second": points_second.tolist(),
                "flow_correspondence_count": int(len(points_first)),
            }
        )
    counts = {
        split: sum(sample["split"] == split for sample in samples)
        for split in ("train", "validation")
    }
    if not counts["train"] or not counts["validation"]:
        raise ValueError(f"empty rotation-match split: {counts}")
    result = {
        "schema": "umi_mast3r_d405_ir_rotation_matches_v1",
        "result": "READY",
        "slam_supervision": False,
        "external_ground_truth_used": False,
        "supervision": [
            "left_ir_forward_backward_optical_flow_matches",
            "onboard_400hz_imu_turn_selection",
        ],
        "source_temporal_manifest": str(temporal_manifest_path.resolve()),
        "source_stereo_manifest": str(stereo_path.resolve()),
        "minimum_angular_speed_deg_s": float(minimum_angular_speed_deg_s),
        "maximum_forward_backward_error_px": float(
            maximum_forward_backward_error_px
        ),
        "counts": counts,
        "rejected_counts": rejected_counts,
        "samples": samples,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--temporal-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-angular-speed-deg-s", type=float, default=30.0)
    parser.add_argument("--maximum-points", type=int, default=512)
    parser.add_argument("--minimum-points", type=int, default=256)
    parser.add_argument("--maximum-forward-backward-error-px", type=float, default=1.0)
    args = parser.parse_args()
    result = build_rotation_match_manifest(
        args.temporal_manifest.resolve(),
        args.output.resolve(),
        args.minimum_angular_speed_deg_s,
        args.maximum_points,
        args.minimum_points,
        args.maximum_forward_backward_error_px,
    )
    print(json.dumps({key: result[key] for key in ("schema", "result", "counts", "rejected_counts")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
