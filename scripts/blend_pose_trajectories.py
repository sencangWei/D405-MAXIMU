#!/usr/bin/env python3
"""Blend two synchronized, same-frame SLAM trajectories.

This utility is intentionally ground-truth blind.  It is used to retain a
stable long-edge estimate while injecting a bounded fraction of a short-edge
turn correction computed from the same UMI camera and IMU recording.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation


REQUIRED_FIELDS = ("t_sec", "x", "y", "z", "qw", "qx", "qy", "qz")


def load_trajectory(
    path: Path,
) -> tuple[list[dict[str, str]], np.ndarray, np.ndarray, Rotation]:
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    if not rows or not set(REQUIRED_FIELDS).issubset(rows[0]):
        raise ValueError(f"invalid trajectory schema: {path}")
    times = np.asarray([float(row["t_sec"]) for row in rows])
    positions = np.asarray(
        [[float(row[key]) for key in ("x", "y", "z")] for row in rows]
    )
    quaternions = np.asarray(
        [
            [float(row[key]) for key in ("qx", "qy", "qz", "qw")]
            for row in rows
        ]
    )
    if np.any(np.diff(times) <= 0.0):
        raise ValueError(f"timestamps must be strictly increasing: {path}")
    return rows, times, positions, Rotation.from_quat(quaternions)


def validate_timestamps(
    base_times: np.ndarray,
    candidate_times: np.ndarray,
    tolerance_s: float = 1e-6,
) -> None:
    if len(base_times) != len(candidate_times) or not np.allclose(
        base_times, candidate_times, rtol=0.0, atol=tolerance_s
    ):
        raise ValueError("trajectory timestamps do not match")


def blend_pose_arrays(
    base_positions: np.ndarray,
    base_rotations: Rotation,
    candidate_positions: np.ndarray,
    candidate_rotations: Rotation,
    candidate_weight: float,
) -> tuple[np.ndarray, Rotation]:
    if not 0.0 <= candidate_weight <= 1.0:
        raise ValueError("candidate weight must be in [0, 1]")
    if base_positions.shape != candidate_positions.shape:
        raise ValueError("trajectory position shapes do not match")
    positions = base_positions + candidate_weight * (
        candidate_positions - base_positions
    )
    relative = base_rotations.inv() * candidate_rotations
    rotations = base_rotations * Rotation.from_rotvec(
        candidate_weight * relative.as_rotvec()
    )
    return positions, rotations


def write_trajectory(
    path: Path,
    rows: list[dict[str, str]],
    positions: np.ndarray,
    rotations: Rotation,
) -> None:
    quaternions = rotations.as_quat()
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row, position, quaternion in zip(rows, positions, quaternions):
            updated = dict(row)
            for key, value in zip(("x", "y", "z"), position):
                updated[key] = f"{value:.9f}"
            updated["qw"] = f"{quaternion[3]:.9f}"
            for key, value in zip(("qx", "qy", "qz"), quaternion[:3]):
                updated[key] = f"{value:.9f}"
            writer.writerow(updated)


def run(args: argparse.Namespace) -> dict[str, object]:
    base_rows, base_times, base_positions, base_rotations = load_trajectory(
        args.base
    )
    _, candidate_times, candidate_positions, candidate_rotations = load_trajectory(
        args.candidate
    )
    validate_timestamps(base_times, candidate_times, args.timestamp_tolerance_s)
    start_position_gap_mm = float(
        np.linalg.norm(candidate_positions[0] - base_positions[0]) * 1000.0
    )
    start_rotation_gap_deg = float(
        np.degrees((base_rotations[0].inv() * candidate_rotations[0]).magnitude())
    )
    if start_position_gap_mm > args.max_start_position_gap_mm:
        raise ValueError(
            f"trajectory start position frames differ by {start_position_gap_mm:.3f} mm"
        )
    if start_rotation_gap_deg > args.max_start_rotation_gap_deg:
        raise ValueError(
            f"trajectory start attitude frames differ by {start_rotation_gap_deg:.3f} deg"
        )
    positions, rotations = blend_pose_arrays(
        base_positions,
        base_rotations,
        candidate_positions,
        candidate_rotations,
        args.candidate_weight,
    )
    write_trajectory(args.output, base_rows, positions, rotations)
    position_delta = np.linalg.norm(candidate_positions - base_positions, axis=1)
    rotation_delta_deg = np.degrees(
        (base_rotations.inv() * candidate_rotations).magnitude()
    )
    report = {
        "schema": "umi_multiscale_pose_blend_v1",
        "result": "PASS",
        "slam_supervision": False,
        "external_ground_truth_used": False,
        "method": "geodesic pose blend of synchronized long-edge base and short-edge turn candidate",
        "candidate_weight": args.candidate_weight,
        "samples": len(base_times),
        "timestamp_max_difference_ms": float(
            np.max(np.abs(base_times - candidate_times)) * 1000.0
        ),
        "start_position_gap_mm": start_position_gap_mm,
        "start_rotation_gap_deg": start_rotation_gap_deg,
        "input_position_delta_p95_mm": float(
            np.percentile(position_delta, 95) * 1000.0
        ),
        "input_position_delta_max_mm": float(np.max(position_delta) * 1000.0),
        "input_rotation_delta_p95_deg": float(np.percentile(rotation_delta_deg, 95)),
        "input_rotation_delta_max_deg": float(np.max(rotation_delta_deg)),
        "inputs": {
            "base": str(args.base.resolve()),
            "candidate": str(args.candidate.resolve()),
        },
        "output": str(args.output.resolve()),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--candidate-weight", type=float, default=0.20)
    parser.add_argument("--timestamp-tolerance-s", type=float, default=1e-6)
    parser.add_argument("--max-start-position-gap-mm", type=float, default=5.0)
    parser.add_argument("--max-start-rotation-gap-deg", type=float, default=1.0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = run(args)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
