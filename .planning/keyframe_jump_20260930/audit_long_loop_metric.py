#!/usr/bin/env python3
"""Read-only classical-feature check of frozen long MASt3R loop candidates.

SIFT is used only as an independent observability probe. Its matches are not
substituted for MASt3R matches and no candidate SLAM trajectory is produced.
"""

import argparse
import csv
import json
from pathlib import Path

import cv2
import numpy as np

from align_mast3r_scale_with_stereo import left_right_consistent, stereo_disparity
from audit_learned_metric_pnp import backproject, metric_pnp, pose_difference
from audit_same_matches import camera_poses


def long_pairs(events_path, minimum_gap=100, maximum_pairs=12):
    pairs = {}
    for line in events_path.read_text().splitlines():
        event = json.loads(line)
        if event.get("event") != "add_factors":
            continue
        for edge in event.get("accepted", []):
            if "source_frames" not in edge:
                continue
            first, second = map(int, edge["source_frames"])
            if abs(first - second) <= minimum_gap:
                continue
            score = min(float(edge["valid_match_fraction_i"]),
                        float(edge["valid_match_fraction_j"]))
            pairs[(first, second)] = max(score, pairs.get((first, second), 0.0))
    return sorted(pairs.items(), key=lambda item: item[1], reverse=True)[:maximum_pairs]


def load_frame(dataset, frame_id, detector, cache):
    if frame_id in cache:
        return cache[frame_id]
    name = f"{frame_id:010d}.png"
    left = cv2.imread(str(dataset / name), cv2.IMREAD_GRAYSCALE)
    right = cv2.imread(str(dataset / "stereo_right" / name), cv2.IMREAD_GRAYSCALE)
    if left is None or right is None:
        raise FileNotFoundError(name)
    left = cv2.resize(left, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    right = cv2.resize(right, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    disparity, disparity_right = stereo_disparity(left, right, 96)
    keypoints, descriptors = detector.detectAndCompute(left, None)
    cache[frame_id] = (keypoints, descriptors, disparity, disparity_right)
    return cache[frame_id]


def pnp_direction(source, target, matcher, intrinsics, baseline):
    keypoints, descriptors, disparity, disparity_right = source
    target_keypoints, target_descriptors = target[:2]
    if descriptors is None or target_descriptors is None:
        return None, {"reason": "descriptors_missing"}
    raw = matcher.knnMatch(descriptors, target_descriptors, k=2)
    matches = [a for pair in raw if len(pair) == 2
               for a, b in [pair] if a.distance < 0.75 * b.distance]
    if not matches:
        return None, {"reason": "no_ratio_matches"}
    source_pixel = np.asarray([keypoints[m.queryIdx].pt for m in matches])
    target_pixel = np.asarray([target_keypoints[m.trainIdx].pt for m in matches])
    valid, disparity_values = left_right_consistent(
        source_pixel, disparity, disparity_right, tolerance_px=1.5)
    depth = intrinsics[0, 0] * baseline / np.maximum(disparity_values, 1e-6)
    valid &= np.isfinite(depth) & (depth >= 0.08) & (depth <= 1.5)
    report = {"sift_ratio_matches": len(matches), "valid_stereo_depth": int(valid.sum())}
    if valid.sum() < 100:
        report["reason"] = "fewer_than_100_metric_matches"
        return None, report
    object_points = backproject(source_pixel[valid], depth[valid], intrinsics)
    transform, pnp_report = metric_pnp(object_points, target_pixel[valid], intrinsics)
    report.update(pnp_report)
    return transform, report


def audit(dataset, events, vins, config, maximum_pairs):
    manifest = json.loads((dataset / "dataset_manifest.json").read_text())
    info = manifest["camera_info"]
    intrinsics = np.array(((info["fx"] / 2, 0, info["ppx"] / 2),
                           (0, info["fy"] / 2, info["ppy"] / 2), (0, 0, 1.0)))
    baseline = float(manifest["stereo_depth_source"]["baseline_m"])
    with (dataset / "frames.csv").open(newline="") as stream:
        frames = list(csv.DictReader(stream))
    times = np.asarray([float(row["t_sec"]) for row in frames])
    positions, rotations, coverage = camera_poses(times, vins, config)
    detector = cv2.SIFT_create(nfeatures=4000, contrastThreshold=0.01, edgeThreshold=15)
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    cache = {}
    rows = []
    for (first, second), learned_match_fraction in long_pairs(events, maximum_pairs=maximum_pairs):
        if not (coverage[first] and coverage[second]):
            continue
        frame_first = load_frame(dataset, first, detector, cache)
        frame_second = load_frame(dataset, second, detector, cache)
        forward, forward_report = pnp_direction(
            frame_first, frame_second, matcher, intrinsics, baseline)
        reverse, reverse_report = pnp_direction(
            frame_second, frame_first, matcher, intrinsics, baseline)
        row = {"first": first, "second": second,
               "learned_match_fraction": learned_match_fraction,
               "forward": forward_report, "reverse": reverse_report}
        if forward is not None and reverse is not None:
            cycle = reverse @ forward
            row["cycle_translation_mm"] = float(np.linalg.norm(cycle[:3, 3]) * 1000)
            vins_rotation = rotations[second].inv() * rotations[first]
            vins_translation = rotations[second].inv().apply(positions[first] - positions[second])
            reference = np.eye(4)
            reference[:3, :3] = vins_rotation.as_matrix()
            reference[:3, 3] = vins_translation
            row["forward_vs_vins"] = pose_difference(forward, reference)
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--vins", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--maximum-pairs", type=int, default=12)
    args = parser.parse_args()
    print(json.dumps(audit(args.dataset, args.events, args.vins,
                           args.config, args.maximum_pairs), indent=2))


if __name__ == "__main__":
    main()
