#!/usr/bin/env python3
"""Make one UMI-only long-loop displacement report for a diagnostic graph run."""

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def norm(values):
    return math.sqrt(sum(float(value) ** 2 for value in values))


def make_report(primary, probe, first, second, primary_path, probe_path):
    if probe.get("schema") != "saved_metric_loop_displacement_check_v1":
        raise ValueError("unexpected metric loop probe schema")
    if probe.get("external_reference_used") is not False:
        raise ValueError("probe must exclude external reference")
    if probe.get("trajectory") != primary.get("trajectory"):
        raise ValueError("probe trajectory differs from frozen stereo source")
    if abs(float(probe["metric_scale_m_per_mast3r_unit"]) -
           float(primary["scale_m_per_mast3r_unit"])) > 1e-12:
        raise ValueError("probe and primary metric scales differ")
    matches = [row for row in probe["results"]
               if row["first_raw"] == first and row["second_raw"] == second]
    if len(matches) != 1 or not matches[0]["accepted"]:
        raise ValueError("requested pair must have one accepted bidirectional stereo match")
    row = matches[0]
    displacement = row["bidirectional_mean_displacement_camera_i_m"]
    if not all(math.isfinite(value) for value in displacement):
        raise ValueError("metric displacement is nonfinite")
    visual_scale = float(primary["scale_m_per_mast3r_unit"])
    native_distance = norm(row["visual_displacement_camera_i_m"]) / visual_scale
    if native_distance <= 0:
        raise ValueError("native loop displacement is degenerate")
    trajectory = Path(primary["trajectory"])
    with trajectory.open(newline="") as stream:
        poses = list(csv.DictReader(stream))
    if not (0 <= first < second < len(poses)):
        raise ValueError("loop frame indices out of range")
    bidirectional_spread = float(row["forward_reverse_displacement_disagreement_mm"]) / 1000
    metric_distance = norm(displacement)
    observation = {
        "accepted": True,
        "first_index": first,
        "second_index": second,
        "first_t_sec": float(poses[first]["t_sec"]),
        "second_t_sec": float(poses[second]["t_sec"]),
        "sample_hop": second - first,
        "metric_displacement_camera_i_m": displacement,
        "metric_displacement_frame": primary["observation_frame"],
        "metric_distance_m": metric_distance,
        "mast3r_distance": native_distance,
        "scale": metric_distance / native_distance,
        "pnp_inlier_ratio": min(float(row["forward"]["inlier_ratio"]),
                                float(row["reverse"]["inlier_ratio"])),
        "rotation_error_deg": float(row["visual_minus_stereo_relative_rotation_deg"]),
        "bidirectional_relative_disagreement": bidirectional_spread / metric_distance,
        "pnp_reprojection_p95_px": max(float(row["forward"]["reprojection_p95_px"]),
                                         float(row["reverse"]["reprojection_p95_px"])),
        "method": "saved_backend_matches_bidirectional_d405_pnp_diagnostic",
    }
    return {
        "schema": "umi_mast3r_stereo_scale_v2",
        "diagnostic_source_schema": "metric_loop_stereo_graph_diagnostic_v1",
        "result": "PASS",
        "slam_supervision": False,
        "diagnostic_only": True,
        "external_ground_truth_used": False,
        "session": primary["session"],
        "trajectory": primary["trajectory"],
        "observation_frame": primary["observation_frame"],
        "scale_m_per_mast3r_unit": visual_scale,
        "factory_stereo_calibration": primary["factory_stereo_calibration"],
        "source_primary_sha256": digest(primary_path),
        "source_probe_sha256": digest(probe_path),
        "observations": [observation],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--primary", type=Path, required=True)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--first", type=int, required=True)
    parser.add_argument("--second", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("refusing to overwrite metric loop report")
    report = make_report(json.loads(args.primary.read_text()),
                         json.loads(args.probe.read_text()),
                         args.first, args.second, args.primary, args.probe)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
