#!/usr/bin/env python3
"""Interpolate frozen Docker2 body poses into per-frame left-IR camera poses."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation, Slerp

from fuse_mast3r_stereo_imu import load_trajectory, load_vins_config


def generate(dataset: Path, vins: Path, vins_config: Path, output: Path) -> int:
    frames = list(csv.DictReader((dataset / "frames.csv").open(newline="", encoding="utf-8")))
    if not frames or [int(row["input_index"]) for row in frames] != list(range(len(frames))):
        raise ValueError("dataset frame indices must be contiguous")
    times = np.asarray([float(row["t_sec"]) for row in frames])
    if np.any(np.diff(times) <= 0):
        raise ValueError("dataset timestamps must be strictly increasing")
    vins_times, body_positions, body_rotations, _ = load_trajectory(vins)
    runtime = load_vins_config(vins_config, expected_td_s=-0.009109323)
    body_t_camera = runtime["body_T_camera"]
    camera_from_body = Rotation.from_matrix(body_t_camera[:3, :3])
    camera_positions = body_positions + body_rotations.apply(body_t_camera[:3, 3])
    camera_rotations = body_rotations * camera_from_body

    valid = (times >= vins_times[0]) & (times <= vins_times[-1])
    if np.count_nonzero(valid) < 2:
        raise ValueError("VINS and camera frames have insufficient overlap")
    query = times[valid]
    interpolated_positions = np.column_stack(
        [np.interp(query, vins_times, camera_positions[:, axis]) for axis in range(3)]
    )
    interpolated_rotations = Slerp(vins_times, camera_rotations)(query).as_quat()
    with output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("input_index", "valid", "x", "y", "z", "qx", "qy", "qz", "qw"))
        position_index = 0
        for index, is_valid in enumerate(valid):
            if not is_valid:
                writer.writerow((index, 0, "", "", "", "", "", "", ""))
                continue
            writer.writerow((
                index, 1,
                *[f"{value:.12g}" for value in interpolated_positions[position_index]],
                *[f"{value:.12g}" for value in interpolated_rotations[position_index]],
            ))
            position_index += 1
    return int(np.count_nonzero(valid))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--vins", type=Path, required=True)
    parser.add_argument("--vins-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(f"VINS camera pose priors: {generate(args.dataset, args.vins, args.vins_config, args.output)} valid frames")


if __name__ == "__main__":
    main()
