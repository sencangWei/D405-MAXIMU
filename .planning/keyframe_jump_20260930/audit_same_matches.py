#!/usr/bin/env python3
"""Compare frozen MASt3R and onboard stereo/VINS geometry on identical matches.

Read-only diagnostic. Neither Lighthouse nor a candidate trajectory is loaded.
"""

import argparse
import csv
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation, Slerp

from fuse_mast3r_stereo_imu import load_trajectory, load_vins_config


def camera_poses(times, vins_path, config_path):
    vt, body_xyz, body_rotation, _ = load_trajectory(vins_path)
    transform = load_vins_config(config_path, expected_td_s=-0.009109323)["body_T_camera"]
    camera_xyz = body_xyz + body_rotation.apply(transform[:3, 3])
    camera_rotation = body_rotation * Rotation.from_matrix(transform[:3, :3])
    coverage = (times >= vt[0]) & (times <= vt[-1])
    positions = np.column_stack([np.interp(times, vt, camera_xyz[:, i]) for i in range(3)])
    return positions, Slerp(vt, camera_rotation)(np.clip(times, vt[0], vt[-1])), coverage


def pixel_error(K, xyz, pixel):
    uv = np.column_stack((K[0, 0] * xyz[:, 0] / xyz[:, 2] + K[0, 2],
                          K[1, 1] * xyz[:, 1] / xyz[:, 2] + K[1, 2]))
    return np.linalg.norm(uv - pixel, axis=1)


def stats(error):
    return {"median_px": float(np.median(error)),
            "p95_px": float(np.percentile(error, 95)), "count": len(error)}


def score_sample(path, anchor, times, positions, rotations, coverage):
    with np.load(path) as sample:
        frame = int(path.stem)
        if not (coverage[frame] and coverage[anchor]):
            return {"frame": frame, "status": "vins_uncovered"}
        Xf, Xk = sample["Xf"], sample["Xk"]
        pf, pk = sample["pixel_current"], sample["pixel_keyframe"]
        df, dk = sample["depth_current_m"], sample["depth_keyframe_m"]
        K, sim, final = sample["K"], sample["T_post"], sample["T_final"]
        visual_valid = (sample["valid"] & np.all(np.isfinite(Xf), axis=1)
                 & np.all(np.isfinite(Xk), axis=1) & (Xf[:, 2] > 0)
                 & (Xk[:, 2] > 0))
        valid = visual_valid & np.isfinite(df) & np.isfinite(dk) & (df > 0) & (dk > 0)
        if valid.sum() < 20:
            return {"frame": frame, "status": "insufficient_common_stereo", "count": int(valid.sum())}
        visual_all = Rotation.from_quat(sim[3:7]).apply(Xf) * sim[7] + sim[:3]
        final_all = Rotation.from_quat(final[3:7]).apply(Xf) * final[7] + final[:3]
        visual = visual_all[valid]
        Mf = np.column_stack(((pf[valid, 0] - K[0, 2]) * df[valid] / K[0, 0],
                              (pf[valid, 1] - K[1, 2]) * df[valid] / K[1, 1], df[valid]))
        R = rotations[anchor].inv() * rotations[frame]
        t = rotations[anchor].inv().apply(positions[frame] - positions[anchor])
        metric = R.apply(Mf) + t
        projected = (visual[:, 2] > 0) & (metric[:, 2] > 0)
        if projected.sum() < 20:
            return {"frame": frame, "status": "insufficient_projectable", "count": int(projected.sum())}
        observed = pk[valid][projected]
        uncovered = visual_valid & ~valid & (visual_all[:, 2] > 0)
        final_mask = visual_valid & (final_all[:, 2] > 0)
        final_common = valid & (final_all[:, 2] > 0)
        correction_angle_deg = float((Rotation.from_quat(sim[3:7]).inv()
                                      * Rotation.from_quat(final[3:7])).magnitude() * 180 / np.pi)
        return {"frame": frame, "keyframe": anchor, "status": "OK",
                "visual_learned": stats(pixel_error(K, visual[projected], observed)),
                "final_visual_common": stats(pixel_error(K, final_all[final_common], pk[final_common])),
                "final_visual_all": stats(pixel_error(K, final_all[final_mask], pk[final_mask])),
                "post_to_final_rotation_deg": correction_angle_deg,
                "visual_uncovered": stats(pixel_error(K, visual_all[uncovered], pk[uncovered]))
                if uncovered.any() else None,
                "visual_all": stats(pixel_error(K, visual_all[visual_valid & (visual_all[:, 2] > 0)],
                                               pk[visual_valid & (visual_all[:, 2] > 0)])),
                "stereo_vins": stats(pixel_error(K, metric[projected], observed)),
                "time_gap_s": float(times[frame] - times[anchor])}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--vins", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    trace = json.loads((args.probe / "geometry_trace.json").read_text())
    frozen = Path(trace["frozen"])
    with (frozen / "trajectory_frames.csv").open(newline="") as stream:
        times = np.array([float(row["t_sec"]) for row in csv.DictReader(stream)])
    vins = args.vins or frozen.parents[2] / "vins/vio_corrected_stream.csv"
    positions, rotations, coverage = camera_poses(times, vins, args.config)
    rows = []
    for row in trace["rows"]:
        if not row.get("captured"):
            continue
        sample = args.probe / "samples" / f'{int(row["frame_id"]):04d}.npz'
        rows.append(score_sample(sample, int(row["keyframe_id"]), times, positions, rotations, coverage))
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
