#!/usr/bin/env python3
"""Propagate a final VINS pose-graph correction to the full-rate trajectory."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation, Slerp


FIELDS = ("t_sec", "x", "y", "z", "qw", "qx", "qy", "qz")


@dataclass(frozen=True)
class Trajectory:
    times: np.ndarray
    positions: np.ndarray
    rotations: Rotation


def _finish_trajectory(
    rows: np.ndarray, path: Path, *, timestamps_are_ns: bool = False
) -> Trajectory:
    if rows.ndim != 2 or rows.shape[1] != len(FIELDS) or len(rows) < 2:
        raise ValueError(f"invalid trajectory schema: {path}")
    if not np.all(np.isfinite(rows)):
        raise ValueError(f"non-finite trajectory values: {path}")
    times = rows[:, 0].copy()
    if timestamps_are_ns or np.median(times) > 1.0e12:
        times *= 1.0e-9
    order = np.argsort(times, kind="stable")
    rows = rows[order]
    times = times[order]
    keep = np.r_[True, np.diff(times) > 0.0]
    rows = rows[keep]
    times = times[keep]
    if len(times) < 2:
        raise ValueError(f"trajectory has fewer than two unique timestamps: {path}")
    quaternions_xyzw = rows[:, [5, 6, 7, 4]]
    norms = np.linalg.norm(quaternions_xyzw, axis=1)
    if np.any(norms < 1.0e-12):
        raise ValueError(f"zero quaternion: {path}")
    return Trajectory(
        times=times,
        positions=rows[:, 1:4],
        rotations=Rotation.from_quat(quaternions_xyzw / norms[:, None]),
    )


def load_trajectory(path: Path) -> Trajectory:
    with path.open(encoding="utf-8", errors="replace") as stream:
        first_line = stream.readline()
    if not first_line:
        raise ValueError(f"empty trajectory: {path}")
    has_header = first_line.split(",", 1)[0].strip() == "t_sec"
    if has_header:
        with path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames is None or not set(FIELDS).issubset(reader.fieldnames):
                raise ValueError(f"invalid trajectory schema: {path}")
            rows = [[float(row[field]) for field in FIELDS] for row in reader]
    else:
        with path.open(newline="", encoding="utf-8") as stream:
            rows = []
            for row in csv.reader(stream):
                if len(row) >= len(FIELDS):
                    rows.append([float(value) for value in row[: len(FIELDS)]])
    return _finish_trajectory(
        np.asarray(rows, dtype=float), path, timestamps_are_ns=not has_header
    )


def interpolate_trajectory(
    trajectory: Trajectory, query_times: np.ndarray
) -> tuple[np.ndarray, Rotation]:
    positions = np.column_stack(
        [
            np.interp(query_times, trajectory.times, trajectory.positions[:, axis])
            for axis in range(3)
        ]
    )
    rotations = Slerp(trajectory.times, trajectory.rotations)(query_times)
    return positions, rotations


def propagate_corrections(
    raw: Trajectory, pose_graph: Trajectory
) -> tuple[Trajectory, dict[str, float | int]]:
    usable = (pose_graph.times >= raw.times[0]) & (
        pose_graph.times <= raw.times[-1]
    )
    knot_times = pose_graph.times[usable]
    knot_positions = pose_graph.positions[usable]
    knot_rotations = pose_graph.rotations[usable]
    if len(knot_times) < 2:
        raise ValueError("pose graph has fewer than two knots inside the raw trajectory")

    raw_knot_positions, raw_knot_rotations = interpolate_trajectory(raw, knot_times)
    correction_rotations = knot_rotations * raw_knot_rotations.inv()
    correction_translations = knot_positions - correction_rotations.apply(
        raw_knot_positions
    )

    clipped_times = np.clip(raw.times, knot_times[0], knot_times[-1])
    full_correction_positions = np.column_stack(
        [
            np.interp(clipped_times, knot_times, correction_translations[:, axis])
            for axis in range(3)
        ]
    )
    full_correction_rotations = Slerp(knot_times, correction_rotations)(
        clipped_times
    )
    corrected_positions = (
        full_correction_rotations.apply(raw.positions) + full_correction_positions
    )
    corrected_rotations = full_correction_rotations * raw.rotations

    reconstructed_positions = (
        correction_rotations.apply(raw_knot_positions) + correction_translations
    )
    position_errors = np.linalg.norm(reconstructed_positions - knot_positions, axis=1)
    rotation_errors = (
        correction_rotations * raw_knot_rotations * knot_rotations.inv()
    ).magnitude()
    correction_norms = np.linalg.norm(correction_translations, axis=1)
    correction_angles = np.degrees(correction_rotations.magnitude())
    report: dict[str, float | int] = {
        "raw_samples": int(len(raw.times)),
        "pose_graph_knots": int(len(knot_times)),
        "position_reconstruction_max_m": float(np.max(position_errors)),
        "rotation_reconstruction_max_deg": float(np.degrees(np.max(rotation_errors))),
        "correction_translation_median_m": float(np.median(correction_norms)),
        "correction_translation_p95_m": float(np.quantile(correction_norms, 0.95)),
        "correction_translation_max_m": float(np.max(correction_norms)),
        "correction_rotation_median_deg": float(np.median(correction_angles)),
        "correction_rotation_p95_deg": float(np.quantile(correction_angles, 0.95)),
        "correction_rotation_max_deg": float(np.max(correction_angles)),
        "leading_hold_samples": int(np.count_nonzero(raw.times < knot_times[0])),
        "trailing_hold_samples": int(np.count_nonzero(raw.times > knot_times[-1])),
    }
    return Trajectory(raw.times, corrected_positions, corrected_rotations), report


def write_trajectory(path: Path, trajectory: Trajectory) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    quaternions_xyzw = trajectory.rotations.as_quat()
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(FIELDS)
        for index, timestamp in enumerate(trajectory.times):
            x, y, z = trajectory.positions[index]
            qx, qy, qz, qw = quaternions_xyzw[index]
            writer.writerow((timestamp, x, y, z, qw, qx, qy, qz))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--pose-graph", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    corrected, report = propagate_corrections(
        load_trajectory(args.raw), load_trajectory(args.pose_graph)
    )
    write_trajectory(args.output, corrected)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
