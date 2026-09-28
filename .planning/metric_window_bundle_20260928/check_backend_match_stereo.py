#!/usr/bin/env python3
"""Read-only D405 stereo check of sampled, already-accepted backend edges."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from mast3r_slam.stereo_depth import StereoDepthProvider, solve_metric_keyframe_pnp


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_direction(source_ids, target_ids, source_depth, source_shape, target_shape, K):
    source_h, source_w = source_shape
    target_h, target_w = target_shape
    source_ids = np.asarray(source_ids, dtype=np.int64)
    target_ids = np.asarray(target_ids, dtype=np.int64)
    if np.any((source_ids < 0) | (source_ids >= source_h * source_w)) or np.any(
        (target_ids < 0) | (target_ids >= target_h * target_w)
    ):
        raise ValueError("sampled match pixel outside frame")
    depth = source_depth.reshape(-1)[source_ids]
    valid = np.isfinite(depth) & (depth > 0)
    ids = source_ids[valid]
    targets = target_ids[valid]
    z = depth[valid]
    points = np.column_stack(((ids % source_w - K[0, 2]) * z / K[0, 0],
                              (ids // source_w - K[1, 2]) * z / K[1, 1], z))
    pixels = np.column_stack((targets % target_w, targets // target_w))
    transform, report = solve_metric_keyframe_pnp(
        points, pixels, K, minimum_points=100, minimum_inlier_ratio=0.5,
        reprojection_error_px=2.0)
    report["sampled_matches"] = int(len(source_ids))
    report["metric_depth_matches"] = int(len(z))
    if transform is not None and report["reprojection_p95_px"] > 4.0:
        report.update(accepted=False, reason="metric_pnp_reprojection_p95_high")
        transform = None
    return transform, report


def check_edge(sample, provider, dataset, cache):
    first = int(sample["first_raw"])
    second = int(sample["second_raw"])
    shape_i = tuple(int(v) for v in sample["first_shape"])
    shape_j = tuple(int(v) for v in sample["second_shape"])
    K = np.asarray(sample["K"], dtype=np.float64)
    if shape_i != shape_j or K.shape != (3, 3):
        raise ValueError("unsupported image geometry")

    def depth(frame, shape):
        key = (frame, shape)
        if key not in cache:
            cache[key] = provider.get_depth(dataset / f"{frame:010d}.png", shape)
        return cache[key]

    forward, forward_report = check_direction(
        sample["forward_source"], sample["forward_target"],
        depth(first, shape_i), shape_i, shape_j, K)
    reverse, reverse_report = check_direction(
        sample["reverse_source"], sample["reverse_target"],
        depth(second, shape_j), shape_j, shape_i, K)
    result = dict(first_raw=first, second_raw=second, gap_frames=second-first,
                  forward=forward_report, reverse=reverse_report, accepted=False)
    if forward is None or reverse is None:
        result["reason"] = "forward_metric_pnp_failed" if forward is None else "reverse_metric_pnp_failed"
        return result
    cycle = reverse @ forward
    result["cycle_translation_mm"] = float(np.linalg.norm(cycle[:3, 3]) * 1000)
    cosine = np.clip((np.trace(cycle[:3, :3]) - 1) / 2, -1, 1)
    result["cycle_rotation_deg"] = float(np.degrees(np.arccos(cosine)))
    if result["cycle_translation_mm"] > 10:
        result["reason"] = "metric_pnp_cycle_translation_high"
    elif result["cycle_rotation_deg"] > 2:
        result["reason"] = "metric_pnp_cycle_rotation_high"
    else:
        result.update(accepted=True, reason="accepted")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--matches", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest-name", default="match_manifest.jsonl")
    parser.add_argument("--first", type=int, default=900)
    parser.add_argument("--last", type=int, default=1120)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("refusing to overwrite existing report")
    provider = StereoDepthProvider.from_dataset(args.dataset)
    if provider is None:
        parser.error("dataset has no independent stereo depth source")
    manifest = args.matches / args.manifest_name
    records = [json.loads(line) for line in manifest.read_text().splitlines()]
    cache = {}
    results = []
    for record in records:
        if not args.first <= record["second_raw"] <= args.last:
            continue
        sample_path = Path(record["npz"])
        if digest(sample_path) != record["sha256"]:
            raise ValueError(f"captured match hash changed: {sample_path}")
        with np.load(sample_path, allow_pickle=False) as sample:
            outcome = check_edge(sample, provider, args.dataset, cache)
        outcome["sample_sha256"] = record["sha256"]
        results.append(outcome)
    args.output.write_text(json.dumps(dict(
        schema="accepted_backend_edge_stereo_check_v1",
        meaning="offline diagnostic; does not change SLAM or use GT",
        dataset=str(args.dataset), manifest_sha256=digest(manifest),
        raw_second_frame_range=[args.first, args.last], checked_edges=len(results),
        results=results), indent=2) + "\n")


if __name__ == "__main__":
    main()
