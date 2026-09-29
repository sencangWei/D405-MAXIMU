#!/usr/bin/env python3
"""Compare stereo, learned, and onboard VINS metric chord lengths only."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from scipy.spatial.transform import Rotation, Slerp

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from fuse_mast3r_stereo_imu import (
    body_t_trajectory_camera_from_stereo_report,
    load_trajectory,
    load_vins_config,
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def camera_chord(times, positions, rotations, first_t, second_t, lever_body):
    if first_t < times[0] or second_t > times[-1]:
        return None
    queries = np.array([first_t, second_t])
    p_body = np.column_stack([np.interp(queries, times, positions[:, axis])
                              for axis in range(3)])
    body_rotation = Slerp(times, rotations)(queries)
    camera_positions = p_body + body_rotation.apply(lever_body)
    return float(np.linalg.norm(camera_positions[1] - camera_positions[0]))


def camera_chord_vector(times, positions, rotations, first_t, second_t,
                        body_t_camera):
    """Return camera-centre displacement in the first camera's axes."""
    if first_t < times[0] or second_t > times[-1]:
        return None
    queries = np.array([first_t, second_t])
    p_body = np.column_stack([np.interp(queries, times, positions[:, axis])
                              for axis in range(3)])
    body_rotation = Slerp(times, rotations)(queries)
    camera_positions = p_body + body_rotation.apply(body_t_camera[:3, 3])
    first_camera_rotation = body_rotation[0] * Rotation.from_matrix(
        body_t_camera[:3, :3])
    return first_camera_rotation.inv().apply(camera_positions[1] - camera_positions[0])


def compare(probe, stereo, vins, vins_config, probe_path, stereo_path, vins_path, config_path):
    if probe.get("external_reference_used") is not False:
        raise ValueError("probe must exclude external reference")
    if (stereo.get("result") != "PASS" or
            stereo.get("external_ground_truth_used") is not False or
            stereo.get("slam_supervision") is not False):
        raise ValueError("onboard stereo report must pass without external supervision")
    if probe.get("trajectory") != stereo.get("trajectory"):
        raise ValueError("probe and stereo report must share original trajectory")
    source_times, _, _, _ = load_trajectory(Path(stereo["trajectory"]))
    vins_times, vins_positions, vins_rotations, _ = load_trajectory(vins_path)
    config = load_vins_config(config_path, -0.009109323)
    body_t_camera = body_t_trajectory_camera_from_stereo_report(
        config["body_T_camera"], stereo)
    lever = body_t_camera[:3, 3]
    rows = []
    for row in probe["results"]:
        if not row["accepted"]:
            continue
        first, second = int(row["first_raw"]), int(row["second_raw"])
        vins_vector = camera_chord_vector(
            vins_times, vins_positions, vins_rotations,
            source_times[first], source_times[second], body_t_camera)
        vins_length = None if vins_vector is None else float(np.linalg.norm(vins_vector))
        visual_vector = np.asarray(row["visual_displacement_camera_i_m"], dtype=float)
        stereo_vector = np.asarray(row["bidirectional_mean_displacement_camera_i_m"], dtype=float)
        visual_length = float(np.linalg.norm(visual_vector))
        stereo_length = float(np.linalg.norm(stereo_vector))
        rows.append(dict(first_raw=first, second_raw=second,
                         vins_camera_chord_mm=None if vins_length is None else vins_length * 1000,
                         vins_camera_vector_first_m=None if vins_vector is None else vins_vector.tolist(),
                         vins_minus_stereo_vector_mm=None if vins_vector is None else
                         float(np.linalg.norm(vins_vector - stereo_vector) * 1000),
                         vins_minus_visual_vector_mm=None if vins_vector is None else
                         float(np.linalg.norm(vins_vector - visual_vector) * 1000),
                         visual_minus_stereo_vector_mm=float(np.linalg.norm(visual_vector - stereo_vector) * 1000),
                         visual_chord_mm=visual_length * 1000,
                         stereo_chord_mm=stereo_length * 1000,
                         stereo_forward_reverse_spread_mm=row["forward_reverse_displacement_disagreement_mm"]))
    return dict(schema="umi_three_metric_chord_diagnostic_v2",
                diagnostic_only=True, external_reference_used=False,
                input_sha256={str(path): digest(path) for path in
                              (probe_path, stereo_path, vins_path, config_path)},
                body_camera_lever_m=lever.tolist(), rows=rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--stereo-report", type=Path, required=True)
    parser.add_argument("--vins", type=Path, required=True)
    parser.add_argument("--vins-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("refusing to overwrite chord diagnostic")
    report = compare(json.loads(args.probe.read_text()),
                     json.loads(args.stereo_report.read_text()), args.vins,
                     args.vins_config, args.probe, args.stereo_report,
                     args.vins, args.vins_config)
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
