#!/usr/bin/env python3
"""Restore UMI capture timestamps on an official MASt3R-SLAM trajectory."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation, Slerp


FIELDS = ("t_sec", "x", "y", "z", "qw", "qx", "qy", "qz")


def load_frame_times(path: Path) -> np.ndarray:
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    if not rows or not {"input_index", "t_sec"}.issubset(rows[0]):
        raise ValueError(f"invalid MASt3R frame table: {path}")
    indices = np.array([int(row["input_index"]) for row in rows])
    if not np.array_equal(indices, np.arange(len(rows))):
        raise ValueError("frames.csv input_index must be contiguous from zero")
    times = np.array([float(row["t_sec"]) for row in rows])
    if np.any(np.diff(times) <= 0):
        raise ValueError("frames.csv timestamps must be strictly increasing")
    return times


def load_mast3r(path: Path) -> np.ndarray:
    trajectory = np.loadtxt(path, dtype=np.float64)
    trajectory = np.atleast_2d(trajectory)
    if trajectory.shape[1] != 8:
        raise ValueError(f"expected 8 TUM columns in {path}")
    if len(trajectory) < 2:
        raise ValueError(
            "MASt3R produced fewer than 2 optimized keyframes; "
            "choose a sequence containing observable camera motion"
        )
    if np.any(np.diff(trajectory[:, 0]) <= 0):
        raise ValueError("MASt3R timestamps must be strictly increasing")
    return trajectory


def restore_timestamps(
    trajectory: np.ndarray,
    frame_times: np.ndarray,
    source_fps: float = 30.0,
    reverse_order: bool = False,
) -> np.ndarray:
    synthetic = trajectory[:, 0]
    indices = np.rint(synthetic * source_fps).astype(int)
    residual = np.abs(synthetic - indices / source_fps)
    if np.max(residual) > 1e-5:
        raise ValueError("MASt3R timestamps do not match its RGBFiles frame clock")
    if np.any(indices < 0) or np.any(indices >= len(frame_times)):
        raise ValueError("MASt3R trajectory references a frame outside frames.csv")
    if len(set(indices.tolist())) != len(indices):
        raise ValueError("MASt3R trajectory contains duplicate frame indices")
    restored = trajectory.copy()
    if reverse_order:
        indices = len(frame_times) - 1 - indices
    restored[:, 0] = frame_times[indices]
    if reverse_order:
        restored = restored[::-1].copy()
    return restored


def dense_interpolation(keyframes: np.ndarray, frame_times: np.ndarray) -> np.ndarray:
    inside = (frame_times >= keyframes[0, 0]) & (frame_times <= keyframes[-1, 0])
    times = frame_times[inside]
    positions = np.column_stack(
        [np.interp(times, keyframes[:, 0], keyframes[:, axis]) for axis in (1, 2, 3)]
    )
    rotations = Slerp(
        keyframes[:, 0], Rotation.from_quat(keyframes[:, [4, 5, 6, 7]])
    )(times).as_quat()
    # Keep the internal trajectory in TUM order so write_csv performs exactly one
    # conversion to the project's qw-first CSV schema.
    return np.column_stack((times, positions, rotations))


def write_csv(path: Path, trajectory: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(FIELDS)
        for row in trajectory:
            # TUM input is t,x,y,z,qx,qy,qz,qw; project CSV is qw first.
            writer.writerow(
                [
                    f"{row[0]:.9f}",
                    *(f"{value:.9f}" for value in row[1:4]),
                    f"{row[7]:.9f}",
                    *(f"{value:.9f}" for value in row[4:7]),
                ]
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trajectory", type=Path, required=True)
    parser.add_argument("--frames", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dense-output", type=Path)
    parser.add_argument("--source-fps", type=float, default=30.0)
    parser.add_argument(
        "--reverse-order",
        action="store_true",
        help="map a reverse-processing pass back to ascending source timestamps",
    )
    args = parser.parse_args()

    frame_times = load_frame_times(args.frames)
    restored = restore_timestamps(
        load_mast3r(args.trajectory),
        frame_times,
        args.source_fps,
        reverse_order=args.reverse_order,
    )
    write_csv(args.output, restored)
    if args.dense_output:
        write_csv(args.dense_output, dense_interpolation(restored, frame_times))
    report = {
        "schema": "umi_mast3r_trajectory_v1",
        "slam_supervision": False,
        "source_trajectory": str(args.trajectory.resolve()),
        "timestamp_table": str(args.frames.resolve()),
        "source_poses": len(restored),
        "dense_frames": int(
            np.sum((frame_times >= restored[0, 0]) & (frame_times <= restored[-1, 0]))
        ),
        "timestamp_source": "D405 global-time exposure timestamps restored by input frame index",
        "dense_pose_method": "translation_linear_and_rotation_slerp_across_untracked_frame_gaps",
        "processing_order": "reverse" if args.reverse_order else "forward",
    }
    manifest_path = args.output.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
