#!/usr/bin/env python3
"""Read-only bidirectional metric PnP audit on frozen MASt3R matches.

No Lighthouse or candidate trajectory is read. This tests whether D405 depth
attached to the learned matches supports a physically consistent relative pose.
"""

import argparse
import csv
import json
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

from audit_same_matches import camera_poses


def backproject(pixel, depth, intrinsics):
    return np.column_stack(((pixel[:, 0] - intrinsics[0, 2]) * depth / intrinsics[0, 0],
                            (pixel[:, 1] - intrinsics[1, 2]) * depth / intrinsics[1, 1], depth))


def metric_pnp(source_xyz, target_pixel, intrinsics):
    cv2.setRNGSeed(0)
    success, rotation_vector, translation, inliers = cv2.solvePnPRansac(
        source_xyz.astype(np.float32), target_pixel.astype(np.float32),
        intrinsics.astype(np.float64), None, iterationsCount=100,
        reprojectionError=2.0, confidence=0.999, flags=cv2.SOLVEPNP_EPNP)
    indices = np.empty(0, dtype=np.int64) if inliers is None else inliers[:, 0]
    result = {"count": int(len(source_xyz)), "inliers": int(len(indices)),
              "ratio": float(len(indices) / max(len(source_xyz), 1))}
    if not success or len(indices) < 100 or result["ratio"] < 0.4:
        return None, result
    rotation_vector, translation = cv2.solvePnPRefineLM(
        source_xyz[indices].astype(np.float32), target_pixel[indices].astype(np.float32),
        intrinsics.astype(np.float64), None, rotation_vector, translation)
    rotation, _ = cv2.Rodrigues(rotation_vector)
    transform = np.eye(4)
    transform[:3, :3] = rotation
    transform[:3, 3] = translation[:, 0]
    projected, _ = cv2.projectPoints(source_xyz[indices].astype(np.float32),
                                     rotation_vector, translation, intrinsics.astype(np.float64), None)
    residual = np.linalg.norm(projected[:, 0] - target_pixel[indices], axis=1)
    result["pixel_median"] = float(np.median(residual))
    result["pixel_p95"] = float(np.percentile(residual, 95))
    return transform, result


def pose_difference(estimated, reference):
    return {"translation_mm": float(1000 * np.linalg.norm(estimated[:3, 3] - reference[:3, 3])),
            "rotation_deg": float(np.degrees(Rotation.from_matrix(reference[:3, :3].T
                                                                   @ estimated[:3, :3]).magnitude()))}


def score_sample(path, anchor, positions, rotations):
    with np.load(path) as sample:
        frame = int(path.stem)
        current_pixel = sample["pixel_current"]
        keyframe_pixel = sample["pixel_keyframe"]
        current_depth = sample["depth_current_m"]
        keyframe_depth = sample["depth_keyframe_m"]
        intrinsics = sample["K"]
        valid = (sample["valid"] & np.isfinite(current_depth) & np.isfinite(keyframe_depth)
                 & (current_depth > 0) & (keyframe_depth > 0))
        if valid.sum() < 100:
            return {"frame": frame, "keyframe": anchor, "valid": int(valid.sum())}
        cf = backproject(current_pixel[valid], current_depth[valid], intrinsics)
        ck = backproject(keyframe_pixel[valid], keyframe_depth[valid], intrinsics)
        current_to_keyframe, forward = metric_pnp(cf, keyframe_pixel[valid], intrinsics)
        keyframe_to_current, backward = metric_pnp(ck, current_pixel[valid], intrinsics)
        vins_rotation = rotations[anchor].inv() * rotations[frame]
        vins_translation = rotations[anchor].inv().apply(positions[frame] - positions[anchor])
        vins = np.eye(4)
        vins[:3, :3] = vins_rotation.as_matrix()
        vins[:3, 3] = vins_translation
        result = {"frame": frame, "keyframe": anchor, "valid": int(valid.sum()),
                  "current_to_keyframe": forward, "keyframe_to_current": backward}
        keyframe_pointmap = sample["Xk"][valid]
        scale_valid = np.isfinite(keyframe_pointmap[:, 2]) & (keyframe_pointmap[:, 2] > 0)
        if scale_valid.sum() >= 100:
            map_scale = float(np.median(keyframe_depth[valid][scale_valid]
                                        / keyframe_pointmap[scale_valid, 2]))
            visual_pose = sample["T_post"]
            visual = np.eye(4)
            visual[:3, :3] = Rotation.from_quat(visual_pose[3:7]).as_matrix()
            visual[:3, 3] = visual_pose[:3] * map_scale
            result["keyframe_m_per_native"] = map_scale
            result["visual_vs_vins"] = pose_difference(visual, vins)
            if current_to_keyframe is not None:
                result["visual_vs_metric_pnp"] = pose_difference(visual, current_to_keyframe)
        if current_to_keyframe is not None:
            result["forward_vs_vins"] = pose_difference(current_to_keyframe, vins)
        if keyframe_to_current is not None:
            result["backward_vs_vins"] = pose_difference(np.linalg.inv(keyframe_to_current), vins)
        if current_to_keyframe is not None and keyframe_to_current is not None:
            result["cycle_translation_mm"] = float(1000 * np.linalg.norm(
                (keyframe_to_current @ current_to_keyframe)[:3, 3]))
            result["cycle_rotation_deg"] = float(np.degrees(Rotation.from_matrix(
                (keyframe_to_current @ current_to_keyframe)[:3, :3]).magnitude()))
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    trace = json.loads((args.probe / "geometry_trace.json").read_text())
    frozen = Path(trace["frozen"])
    with (frozen / "trajectory_frames.csv").open(newline="") as stream:
        times = np.array([float(row["t_sec"]) for row in csv.DictReader(stream)])
    vins = frozen.parents[2] / "vins/vio_corrected_stream.csv"
    positions, rotations, coverage = camera_poses(times, vins, args.config)
    rows = []
    for row in trace["rows"]:
        if not row.get("captured"):
            continue
        frame, anchor = int(row["frame_id"]), int(row["keyframe_id"])
        if coverage[frame] and coverage[anchor]:
            rows.append(score_sample(args.probe / "samples" / f"{frame:04d}.npz",
                                     anchor, positions, rotations))
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
