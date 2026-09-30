#!/usr/bin/env python3
"""Read-only D405 metric geometry audit of saved MASt3R distant matches."""

import argparse
import csv
import json
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

from align_mast3r_scale_with_stereo import left_right_consistent, stereo_disparity
from audit_learned_metric_pnp import backproject, metric_pnp, pose_difference
from audit_same_matches import camera_poses


def load_depth(dataset, frame, shape, cache):
    if frame in cache:
        return cache[frame]
    height, width = shape
    name = f"{frame:010d}.png"
    left = cv2.imread(str(dataset / name), cv2.IMREAD_GRAYSCALE)
    right = cv2.imread(str(dataset / "stereo_right" / name), cv2.IMREAD_GRAYSCALE)
    if left is None or right is None:
        raise FileNotFoundError(name)
    left = cv2.resize(left, (width, height), interpolation=cv2.INTER_AREA)
    right = cv2.resize(right, (width, height), interpolation=cv2.INTER_AREA)
    disparity, reverse_disparity = stereo_disparity(left, right, 64)
    cache[frame] = (disparity, reverse_disparity)
    return cache[frame]


def pixels(pixel_ids, shape):
    height, width = shape
    ids = np.asarray(pixel_ids, dtype=np.int64)
    if np.any(ids < 0) or np.any(ids >= height * width):
        raise ValueError("saved match pixel outside image")
    return np.column_stack((ids % width, ids // width))


def metric_correspondence_pose(source_pixels, target_pixels, source_disparity,
                               target_disparity, intrinsics, baseline):
    source_ok, source_d = left_right_consistent(
        source_pixels, *source_disparity, tolerance_px=1.5)
    target_ok, target_d = left_right_consistent(
        target_pixels, *target_disparity, tolerance_px=1.5)
    source_depth = intrinsics[0, 0] * baseline / np.maximum(source_d, 1e-6)
    target_depth = intrinsics[0, 0] * baseline / np.maximum(target_d, 1e-6)
    valid = (source_ok & target_ok & np.isfinite(source_depth) & np.isfinite(target_depth)
             & (source_depth >= 0.08) & (source_depth <= 1.5)
             & (target_depth >= 0.08) & (target_depth <= 1.5))
    report = {"learned_pairs": len(source_pixels), "both_depth_valid": int(valid.sum())}
    if valid.sum() < 100:
        report["reason"] = "fewer_than_100_double_depth_matches"
        return None, report
    xyz = backproject(source_pixels[valid], source_depth[valid], intrinsics)
    transform, pnp_report = metric_pnp(xyz, target_pixels[valid], intrinsics)
    report.update(pnp_report)
    return transform, report


def audit(matches_dir, dataset, vins, config):
    manifest = json.loads((dataset / "dataset_manifest.json").read_text())
    info = manifest["camera_info"]
    baseline = float(manifest["stereo_depth_source"]["baseline_m"])
    with (dataset / "frames.csv").open(newline="") as stream:
        times = np.asarray([float(row["t_sec"]) for row in csv.DictReader(stream)])
    positions, rotations, coverage = camera_poses(times, vins, config)
    cache = {}
    rows = []
    for match_file in sorted(matches_dir.glob("*.npz")):
        with np.load(match_file) as match:
            first, second = int(match["first_frame"]), int(match["second_frame"])
            if not (coverage[first] and coverage[second]):
                continue
            shape = tuple(int(x) for x in match["image_shape"])
            height, width = shape
            intrinsics = np.array(((info["fx"] * width / info["width"], 0,
                                    info["ppx"] * width / info["width"]),
                                   (0, info["fy"] * height / info["height"],
                                    info["ppy"] * height / info["height"]),
                                   (0, 0, 1.0)))
            depth_first = load_depth(dataset, first, shape, cache)
            depth_second = load_depth(dataset, second, shape, cache)
            forward, forward_report = metric_correspondence_pose(
                pixels(match["forward_i"], shape), pixels(match["forward_j"], shape),
                depth_first, depth_second, intrinsics, baseline)
            reverse, reverse_report = metric_correspondence_pose(
                pixels(match["reverse_j"], shape), pixels(match["reverse_i"], shape),
                depth_second, depth_first, intrinsics, baseline)
            row = {"first": first, "second": second,
                   "forward": forward_report, "reverse": reverse_report}
            if forward is not None and reverse is not None:
                cycle = reverse @ forward
                row["cycle_translation_mm"] = float(np.linalg.norm(cycle[:3, 3]) * 1000)
                row["cycle_rotation_deg"] = float(np.degrees(
                    np.arccos(np.clip((np.trace(cycle[:3, :3]) - 1) / 2, -1.0, 1.0))))
                reference = np.eye(4)
                reference[:3, :3] = (rotations[second].inv() * rotations[first]).as_matrix()
                reference[:3, 3] = rotations[second].inv().apply(
                    positions[first] - positions[second])
                row["forward_vs_vins"] = pose_difference(forward, reference)
                row["accepted"] = (row["cycle_translation_mm"] < 10.0
                                   and row["cycle_rotation_deg"] < 2.0
                                   and forward_report["ratio"] >= 0.5
                                   and reverse_report["ratio"] >= 0.5
                                   and forward_report["pixel_p95"] < 2.0
                                   and reverse_report["pixel_p95"] < 2.0)
                if row["accepted"]:
                    row["first_index"], row["second_index"] = first, second
                    row["metric_displacement_camera_i_m"] = np.linalg.inv(forward)[:3, 3].tolist()
                    row["pnp_rotation_quaternion_xyzw"] = Rotation.from_matrix(
                        forward[:3, :3]).as_quat().tolist()
            rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--matches-dir", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--vins", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.matches_dir, args.dataset, args.vins, args.config), indent=2))


if __name__ == "__main__":
    main()
