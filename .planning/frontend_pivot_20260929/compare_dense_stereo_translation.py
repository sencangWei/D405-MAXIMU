#!/usr/bin/env python3
"""Read-only full-correspondence D405 translation check of frozen frontend poses."""
import argparse
import json
from pathlib import Path
import sys

import numpy as np
from scipy.spatial.transform import Rotation


def stereo_imu_rotation_witness(visual_imu_deg, pnp_imu_deg, pnp_visual_deg,
                                inlier_ratio, reprojection_p95_px, stereo_points,
                                spatial_translation_spread_mm):
    """Conservative diagnostic witness, not an estimator or ATE selector."""
    return (visual_imu_deg > 1.0 and pnp_imu_deg < 0.6 and pnp_visual_deg > 0.8
            and inlier_ratio > 0.8 and reprojection_p95_px < 1.5
            and stereo_points >= 10000 and spatial_translation_spread_mm is not None
            and spatial_translation_spread_mm < 2.0)


def backproject(ids, depth, K, width):
    x = ids % width
    y = ids // width
    return np.column_stack(((x-K[0, 2])*depth/K[0, 0],
                            (y-K[1, 2])*depth/K[1, 1], depth))


def translation_observation(ids_k, ids_f, depth_k, depth_f, K, rotation):
    h, w = depth_k.shape
    if depth_f.shape != (h, w) or K.shape != (3, 3) or ids_k.shape != ids_f.shape:
        raise ValueError("dense stereo geometry shape mismatch")
    if np.any(ids_k < 0) or np.any(ids_k >= h*w) or np.any(ids_f < 0) or np.any(ids_f >= h*w):
        raise ValueError("correspondence outside image")
    dk = depth_k.reshape(-1)[ids_k]
    df = depth_f.reshape(-1)[ids_f]
    valid = np.isfinite(dk) & np.isfinite(df) & (dk > 0) & (df > 0)
    k, f = ids_k[valid], ids_f[valid]
    if len(k) < 100:
        return dict(status="UNKNOWN", points=len(k), reason="too_few_stereo_pairs")
    candidates = backproject(k, dk[valid], K, w) - rotation.apply(
        backproject(f, df[valid], K, w))
    center = np.median(candidates, axis=0)
    residual = np.linalg.norm(candidates-center, axis=1)
    # Distinct image quadrants expose spatially structured depth/matching bias.
    quadrants = (k // w >= h//2).astype(int)*2 + (k % w >= w//2).astype(int)
    tile_centers = [np.median(candidates[quadrants == tile], axis=0)
                    for tile in range(4) if np.count_nonzero(quadrants == tile) >= 100]
    tile_spread = max(np.linalg.norm(np.asarray(tile_centers)-center, axis=1)) if tile_centers else None
    return dict(status="OK", points=len(k), translation_m=center.tolist(),
                residual_median_mm=float(np.median(residual)*1000),
                residual_p95_mm=float(np.percentile(residual, 95)*1000),
                tile_count=len(tile_centers),
                tile_spread_max_mm=float(tile_spread*1000) if tile_spread is not None else None)


def summarize(path):
    trace = json.loads((path/"geometry_trace.json").read_text())
    if trace["errors"] or not all(x["byte_identical"] for x in trace["trajectory_identity"].values()):
        raise ValueError("probe replay was not a clean byte-identical reproduction")
    manifest = json.loads((Path(trace["frozen"])/"run_manifest.json").read_text())
    sys.path.insert(0, manifest["toolchain"])
    from mast3r_slam.stereo_depth import StereoDepthProvider, solve_metric_keyframe_pnp
    dataset = Path(trace["dataset"])
    provider = StereoDepthProvider.from_dataset(dataset)
    if provider is None:
        raise ValueError("independent D405 stereo depth missing")
    cache = {}
    rows = []
    for row in trace["rows"]:
        if not row["captured"]:
            raise ValueError(f"missing capture at {row['frame_id']}")
        frame_id, keyframe_id = row["frame_id"], row["keyframe_id"]
        with np.load(path/"samples"/f"{frame_id:04d}.npz") as sample:
            ids_k, ids_f = sample["dense_keyframe_pixel_ids"], sample["dense_current_pixel_ids"]
            K, shape = sample["K"], tuple(sample["image_shape"].astype(int))
            if len(ids_k) != row["full_optimize_valid_count"]:
                raise ValueError("incomplete native correspondence coverage")
            for index in (keyframe_id, frame_id):
                if index not in cache:
                    cache[index] = provider.get_depth(dataset/f"{index:010d}.png", shape)
            rotation = (Rotation.from_quat(sample["quat_keyframe_xyzw"]).inv()
                        * Rotation.from_quat(sample["quat_current_xyzw"]))
            metric = translation_observation(ids_k, ids_f, cache[keyframe_id],
                                             cache[frame_id], K, rotation)
            learned = sample["T_final"]
            visual_rotation = Rotation.from_quat(learned[3:7])
            metric_visual_rotation = translation_observation(
                ids_k, ids_f, cache[keyframe_id], cache[frame_id], K, visual_rotation)
            valid_scale = (np.isfinite(sample["depth_keyframe_m"])
                           & (sample["depth_keyframe_m"] > 0)
                           & np.isfinite(sample["Xk"][:, 2])
                           & (sample["Xk"][:, 2] > 0))
            scale = float(np.median(sample["depth_keyframe_m"][valid_scale]
                                    / sample["Xk"][valid_scale, 2])) if np.any(valid_scale) else np.nan
            entry = dict(frame_id=frame_id, keyframe_id=keyframe_id,
                         stereo=metric, stereo_with_visual_rotation=metric_visual_rotation,
                         visual_vs_imu_rotation_deg=float((rotation.inv()*visual_rotation).magnitude()*180/np.pi),
                         learned_to_metric_scale=scale)
            anchor_depth = cache[keyframe_id].reshape(-1)[ids_k]
            valid_anchor = np.flatnonzero(np.isfinite(anchor_depth) & (anchor_depth > 0))
            selected = valid_anchor[np.linspace(0, len(valid_anchor)-1,
                                                  min(5000, len(valid_anchor))).astype(int)] if len(valid_anchor) else valid_anchor
            pnp_pose = None
            if len(selected) >= 100:
                h, w = shape
                object_points = backproject(ids_k[selected], anchor_depth[selected], K, w)
                image_points = np.column_stack((ids_f[selected] % w, ids_f[selected] // w)).astype(np.float32)
                pnp_pose, pnp_report = solve_metric_keyframe_pnp(
                    object_points, image_points, K, 100, 0.3, 2.0)
                entry["stereo_pnp"] = pnp_report
            if pnp_pose is not None:
                pnp_rotation = Rotation.from_matrix(pnp_pose[:3, :3]).inv()
                pnp_imu = float((pnp_rotation.inv()*rotation).magnitude()*180/np.pi)
                pnp_visual = float((pnp_rotation.inv()*visual_rotation).magnitude()*180/np.pi)
                entry["stereo_pnp"].update(pnp_vs_imu_deg=pnp_imu,
                                           pnp_vs_visual_deg=pnp_visual)
                entry["rotation_witness"] = stereo_imu_rotation_witness(
                    entry["visual_vs_imu_rotation_deg"], pnp_imu, pnp_visual,
                    pnp_report["inlier_ratio"], pnp_report["reprojection_p95_px"],
                    metric["points"], metric["tile_spread_max_mm"])
            if metric["status"] == "OK" and np.isfinite(scale):
                visual = scale*learned[:3]
                delta = visual-np.asarray(metric["translation_m"])
                entry.update(visual_translation_m=visual.tolist(),
                             visual_minus_stereo_m=delta.tolist(),
                             disagreement_mm=float(np.linalg.norm(delta)*1000))
                if metric_visual_rotation["status"] == "OK":
                    visual_delta = visual-np.asarray(metric_visual_rotation["translation_m"])
                    entry["disagreement_with_visual_rotation_mm"] = float(np.linalg.norm(visual_delta)*1000)
            rows.append(entry)
    deltas = [row["disagreement_mm"] for row in rows if "disagreement_mm" in row]
    visual_deltas = [row["disagreement_with_visual_rotation_mm"] for row in rows
                     if "disagreement_with_visual_rotation_mm" in row]
    witnesses = [row["frame_id"] for row in rows if row.get("rotation_witness", False)]
    return dict(probe=str(path), frame_scope=trace["fixed_input_scope"],
                trajectory_identity=trace["trajectory_identity"],
                producer_difference_from_frozen=trace["producer_difference_from_frozen"],
                stereo_observed_frames=len(deltas),
                disagreement_median_mm=float(np.median(deltas)) if deltas else None,
                disagreement_p95_mm=float(np.percentile(deltas, 95)) if deltas else None,
                disagreement_max_mm=float(max(deltas)) if deltas else None,
                visual_rotation_disagreement_max_mm=float(max(visual_deltas)) if visual_deltas else None,
                visual_vs_imu_rotation_max_deg=float(max(row["visual_vs_imu_rotation_deg"] for row in rows)),
                rotation_witness_frames=witnesses,
                rows=rows,
                limitations=["Matched pixels and stereo depth can share scene/matching bias.",
                             "Within-anchor translation cannot resolve an anchor's common world-frame shift.",
                             "This is not Lighthouse/SteamVR ATE and does not change the trajectory."])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("probes", nargs="+", type=Path)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    results = [summarize(path) for path in args.probes]
    if args.summary_only:
        results = [{key: value for key, value in item.items() if key != "rows"} for item in results]
    print(json.dumps(results, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
