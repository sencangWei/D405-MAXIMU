#!/usr/bin/env python3
"""Diagnostic: combine two onboard visual frontend channels without external GT."""

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from fuse_mast3r_stereo_imu import load_trajectory


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def compose(metric_path, attitude_path, output, report):
    metric_times, metric_positions, metric_rotations, _ = load_trajectory(metric_path)
    attitude_times, _, attitude_rotations, attitude_rows = load_trajectory(attitude_path)
    if len(metric_times) != len(attitude_times) or not np.array_equal(metric_times, attitude_times):
        raise ValueError("frontend trajectories must have identical frame timestamps")
    if len(metric_times) < 2 or not np.isfinite(metric_positions).all():
        raise ValueError("metric frontend trajectory is incomplete or nonfinite")
    world_alignment = attitude_rotations[0] * metric_rotations[0].inv()
    translation = -world_alignment.apply(metric_positions[0])
    aligned_positions = world_alignment.apply(metric_positions) + translation
    if output.exists() or report.exists():
        raise FileExistsError("refusing to overwrite a diagnostic artifact")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(attitude_rows[0]))
        writer.writeheader()
        for row, position in zip(attitude_rows, aligned_positions):
            record = dict(row)
            for name, value in zip(("x", "y", "z"), position):
                record[name] = f"{value:.9f}"
            writer.writerow(record)
    relative_rotation_deg = np.degrees(
        (attitude_rotations.inv() * world_alignment * metric_rotations).magnitude()
    )
    result = {
        "schema": "umi_metric_position_visual_attitude_probe_v1",
        "diagnostic_only": True,
        "external_reference_used": False,
        "frames": len(metric_times),
        "metric_position_source": str(metric_path.resolve()),
        "attitude_source": str(attitude_path.resolve()),
        "metric_position_sha256": digest(metric_path),
        "attitude_sha256": digest(attitude_path),
        "output_sha256": digest(output),
        "relative_rotation_deg_median": float(np.median(relative_rotation_deg)),
        "relative_rotation_deg_p95": float(np.percentile(relative_rotation_deg, 95)),
        "relative_rotation_deg_max": float(np.max(relative_rotation_deg)),
        "alignment_rotation_xyzw": world_alignment.as_quat().tolist(),
        "alignment_translation_m": translation.tolist(),
    }
    report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--metric-trajectory", type=Path, required=True)
    parser.add_argument("--attitude-trajectory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(compose(args.metric_trajectory, args.attitude_trajectory,
                             args.output, args.report), indent=2))


if __name__ == "__main__":
    main()
