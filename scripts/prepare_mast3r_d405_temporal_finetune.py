#!/usr/bin/env python3
"""Build temporal D405 IR pairs supervised only by stereo depth and onboard IMU."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from align_mast3r_scale_with_stereo import solve_translation_with_fixed_rotation


def load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def compose_camera_rotation(
    priors: list[dict[str, str]], first_index: int, second_index: int
) -> Rotation:
    if second_index <= first_index:
        raise ValueError("temporal pair must be forward in time")
    if second_index >= len(priors):
        raise IndexError("IMU prior does not cover temporal pair")
    camera_j_from_camera_i = Rotation.identity()
    for index in range(first_index + 1, second_index + 1):
        row = priors[index]
        delta = Rotation.from_quat(
            [float(row[name]) for name in ("qx", "qy", "qz", "qw")]
        )
        # Each delta is expressed in the preceding camera frame.
        camera_j_from_camera_i = camera_j_from_camera_i * delta
    return camera_j_from_camera_i


def temporal_motion_bin(angle_deg: float, duration_s: float) -> str:
    if duration_s <= 0.0:
        raise ValueError("temporal pair duration must be positive")
    angular_speed_deg_s = angle_deg / duration_s
    if angular_speed_deg_s >= 75.0:
        return "very_fast"
    if angular_speed_deg_s >= 36.0:
        return "fast"
    if angular_speed_deg_s >= 21.0:
        return "moderate"
    return "slow"


def estimate_temporal_pose(
    first: dict,
    second: dict,
    imu_camera_rotation: Rotation,
    maximum_rotation_disagreement_deg: float = 3.0,
    maximum_reprojection_p95_px: float = 2.5,
) -> tuple[np.ndarray | None, dict]:
    image_i = cv2.imread(first["left_image"], cv2.IMREAD_GRAYSCALE)
    image_j = cv2.imread(second["left_image"], cv2.IMREAD_GRAYSCALE)
    depth_u16 = cv2.imread(first["depth_left"], cv2.IMREAD_UNCHANGED)
    if image_i is None or image_j is None or depth_u16 is None:
        return None, {"reason": "missing_image_or_depth"}
    features = cv2.goodFeaturesToTrack(
        image_i, maxCorners=1400, qualityLevel=0.005, minDistance=7, blockSize=7
    )
    if features is None or len(features) < 50:
        return None, {"reason": "insufficient_features"}
    points_i = features.reshape(-1, 2)
    points_j_raw, status, _ = cv2.calcOpticalFlowPyrLK(
        image_i,
        image_j,
        features,
        None,
        winSize=(31, 31),
        maxLevel=4,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, 0.01),
    )
    if points_j_raw is None:
        return None, {"reason": "forward_flow_failed"}
    points_back_raw, backward_status, _ = cv2.calcOpticalFlowPyrLK(
        image_j,
        image_i,
        points_j_raw,
        None,
        winSize=(31, 31),
        maxLevel=4,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, 0.01),
    )
    if points_back_raw is None:
        return None, {"reason": "backward_flow_failed"}
    points_j = points_j_raw.reshape(-1, 2)
    points_back = points_back_raw.reshape(-1, 2)
    valid = status.ravel().astype(bool) & backward_status.ravel().astype(bool)
    valid &= np.linalg.norm(points_back - points_i, axis=1) <= 1.5
    rounded = np.rint(points_i).astype(np.int32)
    height, width = image_i.shape
    valid &= (
        (rounded[:, 0] >= 0)
        & (rounded[:, 0] < width)
        & (rounded[:, 1] >= 0)
        & (rounded[:, 1] < height)
    )
    depth_m = depth_u16.astype(np.float32) * 0.0001
    sampled_depth = np.zeros(len(points_i), dtype=np.float32)
    sampled_depth[valid] = depth_m[rounded[valid, 1], rounded[valid, 0]]
    valid &= sampled_depth > 0.0
    if int(np.count_nonzero(valid)) < 40:
        return None, {"reason": "insufficient_depth_tracks"}

    intrinsics = np.asarray(first["intrinsics"], dtype=np.float64)
    uv = points_i[valid]
    z = sampled_depth[valid]
    object_points = np.column_stack(
        (
            (uv[:, 0] - intrinsics[0, 2]) * z / intrinsics[0, 0],
            (uv[:, 1] - intrinsics[1, 2]) * z / intrinsics[1, 1],
            z,
        )
    ).astype(np.float32)
    image_points = points_j[valid].astype(np.float32)
    solved, rvec, _tvec, inliers = cv2.solvePnPRansac(
        object_points,
        image_points,
        intrinsics,
        None,
        iterationsCount=300,
        reprojectionError=2.0,
        confidence=0.999,
        flags=cv2.SOLVEPNP_EPNP,
    )
    if not solved or inliers is None or len(inliers) < 30:
        return None, {"reason": "pnp_failed"}
    inlier_ratio = float(len(inliers) / len(object_points))
    if inlier_ratio < 0.35:
        return None, {"reason": "pnp_inlier_ratio_low", "pnp_inlier_ratio": inlier_ratio}

    # PnP maps points from camera i to camera j.  The accumulated gyro prior is
    # the camera-j pose in camera-i coordinates, so its inverse is the fixed PnP
    # rotation used to solve translation.
    free_rotation = Rotation.from_rotvec(rvec.ravel())
    fixed_rotation = imu_camera_rotation.inv()
    rotation_disagreement_deg = float(
        np.degrees((free_rotation.inv() * fixed_rotation).magnitude())
    )
    if rotation_disagreement_deg > maximum_rotation_disagreement_deg:
        return None, {
            "reason": "visual_imu_rotation_disagrees",
            "rotation_disagreement_deg": rotation_disagreement_deg,
        }
    selected = inliers.ravel()
    translation, reprojection = solve_translation_with_fixed_rotation(
        object_points[selected], image_points[selected], intrinsics, fixed_rotation
    )
    finite = np.isfinite(reprojection)
    if not np.any(finite):
        return None, {"reason": "fixed_rotation_translation_failed"}
    reprojection_p95_px = float(np.percentile(reprojection[finite], 95))
    if reprojection_p95_px > maximum_reprojection_p95_px:
        return None, {
            "reason": "reprojection_p95_high",
            "reprojection_p95_px": reprojection_p95_px,
        }
    camera_displacement_i = -fixed_rotation.inv().apply(translation)
    translation_m = float(np.linalg.norm(camera_displacement_i))
    if not 0.002 <= translation_m <= 0.20:
        return None, {"reason": "translation_excitation_invalid", "translation_m": translation_m}

    camera_pose_j = np.eye(4, dtype=np.float32)
    camera_pose_j[:3, :3] = imu_camera_rotation.as_matrix().astype(np.float32)
    camera_pose_j[:3, 3] = camera_displacement_i.astype(np.float32)
    return camera_pose_j, {
        "tracked_depth_points": int(len(object_points)),
        "pnp_inliers": int(len(inliers)),
        "pnp_inlier_ratio": inlier_ratio,
        "rotation_disagreement_deg": rotation_disagreement_deg,
        "reprojection_median_px": float(np.median(reprojection[finite])),
        "reprojection_p95_px": reprojection_p95_px,
        "translation_m": translation_m,
    }


def build_temporal_manifest(
    stereo_manifest_path: Path,
    output: Path,
    pair_hops: tuple[int, ...],
    maximum_pairs_per_session: int,
    minimum_pair_duration_s: float = 0.08,
    maximum_pair_duration_s: float = 0.75,
) -> dict:
    source = load_json(stereo_manifest_path)
    if source.get("external_ground_truth_used") is not False:
        raise ValueError("source manifest must exclude external ground truth")
    export_roots = {
        item["session_id"]: Path(item["dataset_manifest"]).parent
        for item in source["source_exports"]
    }
    grouped: dict[str, list[dict]] = {}
    for sample in source["samples"]:
        grouped.setdefault(sample["session_id"], []).append(sample)

    samples: list[dict] = []
    rejected: list[dict] = []
    for session_id, session_samples in grouped.items():
        ordered = sorted(session_samples, key=lambda item: item["input_index"])
        prior_path = export_roots[session_id] / "imu_rotation_priors.csv"
        priors = list(csv.DictReader(prior_path.open(newline="", encoding="utf-8")))
        accepted_for_session = 0
        for position, first in enumerate(ordered):
            for hop in pair_hops:
                target = position + hop
                if target >= len(ordered):
                    continue
                second = ordered[target]
                frame_gap = int(second["input_index"] - first["input_index"])
                duration_s = float(second["timestamp_s"] - first["timestamp_s"])
                if frame_gap <= 0 or not (
                    minimum_pair_duration_s
                    <= duration_s
                    <= maximum_pair_duration_s
                ):
                    continue
                imu_rotation = compose_camera_rotation(
                    priors, int(first["input_index"]), int(second["input_index"])
                )
                pose, quality = estimate_temporal_pose(first, second, imu_rotation)
                if pose is None:
                    rejected.append(
                        {
                            "session_id": session_id,
                            "first_input_index": first["input_index"],
                            "second_input_index": second["input_index"],
                            **quality,
                        }
                    )
                    continue
                angle_deg = float(np.degrees(imu_rotation.magnitude()))
                samples.append(
                    {
                        "session_id": session_id,
                        "split": first["split"],
                        "first_input_index": first["input_index"],
                        "second_input_index": second["input_index"],
                        "first_image": first["left_image"],
                        "second_image": second["left_image"],
                        "depth_first": first["depth_left"],
                        "depth_second": second["depth_left"],
                        "intrinsics": first["intrinsics"],
                        "camera_pose_second": pose.tolist(),
                        "duration_s": duration_s,
                        "frame_gap": frame_gap,
                        "imu_rotation_deg": angle_deg,
                        "angular_speed_deg_s": angle_deg / duration_s,
                        "motion_bin": temporal_motion_bin(angle_deg, duration_s),
                        **quality,
                    }
                )
                accepted_for_session += 1
                if 0 < maximum_pairs_per_session <= accepted_for_session:
                    break
            if 0 < maximum_pairs_per_session <= accepted_for_session:
                break

    counts = {
        split: sum(sample["split"] == split for sample in samples)
        for split in ("train", "validation")
    }
    if not counts["train"] or not counts["validation"]:
        raise ValueError(f"empty temporal split: {counts}")
    result = {
        "schema": "umi_mast3r_d405_ir_temporal_finetune_v1",
        "result": "READY",
        "slam_supervision": False,
        "external_ground_truth_used": False,
        "supervision": [
            "temporal_left_ir_pairs",
            "synchronized_stereo_metric_depth",
            "onboard_400hz_imu_relative_rotation",
            "stereo_depth_optical_flow_robust_translation",
        ],
        "source_stereo_manifest": str(stereo_manifest_path.resolve()),
        "pair_hops": list(pair_hops),
        "pair_duration_s": {
            "minimum": minimum_pair_duration_s,
            "maximum": maximum_pair_duration_s,
        },
        "counts": counts,
        "motion_bin_counts": {
            name: sum(sample["motion_bin"] == name for sample in samples)
            for name in ("slow", "moderate", "fast", "very_fast")
        },
        "rejected_count": len(rejected),
        "rejected": rejected,
        "samples": samples,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stereo-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pair-hop", type=int, action="append", default=[])
    parser.add_argument("--maximum-pairs-per-session", type=int, default=0)
    parser.add_argument("--minimum-pair-duration-s", type=float, default=0.08)
    parser.add_argument("--maximum-pair-duration-s", type=float, default=0.75)
    args = parser.parse_args()
    pair_hops = tuple(args.pair_hop or (1, 2))
    if any(hop < 1 for hop in pair_hops):
        raise ValueError("pair hops must be positive")
    if not 0.0 < args.minimum_pair_duration_s <= args.maximum_pair_duration_s:
        raise ValueError("temporal pair duration bounds are invalid")
    report = build_temporal_manifest(
        args.stereo_manifest.resolve(),
        args.output.resolve(),
        pair_hops,
        args.maximum_pairs_per_session,
        args.minimum_pair_duration_s,
        args.maximum_pair_duration_s,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
