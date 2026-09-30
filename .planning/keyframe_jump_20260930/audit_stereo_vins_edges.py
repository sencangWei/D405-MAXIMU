#!/usr/bin/env python3
"""Audit saved stereo displacement edges against onboard VINS camera motion.

This is read-only and does not load an external reference or alter poses.
"""

import argparse
import csv
import json
from pathlib import Path

import numpy as np

from audit_same_matches import camera_poses
from fuse_mast3r_stereo_imu import load_trajectory


def audit(report_path, config_path, center, radius):
    frontend = report_path.parent
    evaluation = frontend.parents[2]
    with (frontend / "trajectory_frames.csv").open(newline="") as stream:
        times = np.array([float(row["t_sec"]) for row in csv.DictReader(stream)])
    positions, rotations, covered = camera_poses(
        times, evaluation / "vins/vio_corrected_stream.csv", config_path
    )
    report = json.loads(report_path.read_text())
    observations = report["observations"]
    visual_times, visual_positions, visual_rotations, _ = load_trajectory(
        frontend / "trajectory_frames.csv"
    )
    if len(visual_times) != len(times) or not np.allclose(visual_times, times, atol=1e-5, rtol=0):
        raise ValueError("stereo and frontend timestamps differ")
    scale = float(report["scale_m_per_mast3r_unit"])
    accepted = []
    for edge in observations:
        if not edge.get("accepted") or "metric_displacement_camera_i_m" not in edge:
            continue
        i, j = int(edge["first_index"]), int(edge["second_index"])
        if not (covered[i] and covered[j]):
            continue
        stereo = np.asarray(edge["metric_displacement_camera_i_m"], dtype=float)
        if stereo.shape != (3,) or not np.all(np.isfinite(stereo)):
            continue
        vins = rotations[i].inv().apply(positions[j] - positions[i])
        visual = (visual_rotations[i].inv().apply(
            visual_positions[j] - visual_positions[i]) * scale)
        accepted.append({"first": i, "second": j,
                         "stereo_vins_delta_mm": float(np.linalg.norm(stereo - vins) * 1000),
                         "visual_vins_delta_mm": float(np.linalg.norm(visual - vins) * 1000),
                         "stereo_visual_delta_mm": float(np.linalg.norm(stereo - visual) * 1000),
                         "stereo_mm": float(np.linalg.norm(stereo) * 1000),
                         "vins_mm": float(np.linalg.norm(vins) * 1000),
                         "pnp_inliers": edge.get("pnp_inliers"),
                         "bidirectional_relative_disagreement": edge.get(
                             "bidirectional_relative_disagreement")})
    local = [edge for edge in accepted if edge["first"] <= center + radius
             and edge["second"] >= center - radius]

    def summary(rows):
        if not rows:
            return {"count": 0}
        error = np.array([row["stereo_vins_delta_mm"] for row in rows])
        visual_error = np.array([row["visual_vins_delta_mm"] for row in rows])
        return {"count": len(rows), "stereo_vins_median_mm": float(np.median(error)),
                "stereo_vins_p95_mm": float(np.percentile(error, 95)),
                "stereo_vins_max_mm": float(np.max(error)),
                "visual_vins_median_mm": float(np.median(visual_error)),
                "visual_vins_p95_mm": float(np.percentile(visual_error, 95)),
                "stereo_closer_fraction": float(np.mean(error < visual_error))}

    return {"report": str(report_path), "center": center, "radius": radius,
            "all_edges": summary(accepted), "local_edges": summary(local),
            "local_rows": local}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--center", type=int, required=True)
    parser.add_argument("--radius", type=int, default=20)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    result = audit(args.report, args.config, args.center, args.radius)
    if args.summary_only:
        result.pop("local_rows")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
