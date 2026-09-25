#!/usr/bin/env python3
"""Apply fixed zero-phase position smoothing to an offline pose trajectory."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal import savgol_filter


def smooth_positions(
    positions: np.ndarray, window_length: int, polynomial_order: int
) -> np.ndarray:
    positions = np.asarray(positions, dtype=float)
    if positions.ndim != 2 or positions.shape[1] != 3:
        raise ValueError("positions must have shape (N, 3)")
    if window_length < 3 or window_length % 2 == 0:
        raise ValueError("window length must be odd and at least three")
    if polynomial_order < 1 or polynomial_order >= window_length:
        raise ValueError("polynomial order must be within [1, window_length)")
    if window_length > len(positions):
        raise ValueError("window length exceeds trajectory length")
    return savgol_filter(
        positions,
        window_length=window_length,
        polyorder=polynomial_order,
        axis=0,
        mode="interp",
    )


def gaussian_smooth_positions(
    times: np.ndarray, positions: np.ndarray, sigma_s: float
) -> tuple[np.ndarray, float, int]:
    times = np.asarray(times, dtype=float)
    positions = np.asarray(positions, dtype=float)
    if times.ndim != 1 or len(times) != len(positions):
        raise ValueError("times must be one-dimensional and match positions")
    if positions.ndim != 2 or positions.shape[1] != 3:
        raise ValueError("positions must have shape (N, 3)")
    if sigma_s <= 0.0:
        raise ValueError("Gaussian sigma must be positive")
    intervals = np.diff(times)
    if len(intervals) == 0 or np.any(intervals <= 0.0):
        raise ValueError("trajectory timestamps must be strictly increasing")
    median_interval_s = float(np.median(intervals))
    gaps = intervals > 1.5 * median_interval_s
    if float(np.max(np.abs(intervals[~gaps] - median_interval_s))) > 0.25 * median_interval_s:
        raise ValueError("Gaussian smoothing requires near-uniform timestamps")
    sigma_samples = sigma_s / median_interval_s
    filtered = positions.copy()
    starts = np.r_[0, np.flatnonzero(gaps) + 1]
    stops = np.r_[starts[1:], len(positions)]
    for start, stop in zip(starts, stops):
        filtered[start:stop] = gaussian_filter1d(
            positions[start:stop], sigma_samples, axis=0, mode="nearest"
        )
    return filtered, sigma_samples, int(gaps.sum())


def run(
    input_path: Path,
    output_path: Path,
    window_length: int,
    polynomial_order: int,
    method: str = "savgol",
    gaussian_sigma_s: float = 0.05,
    report_path: Path | None = None,
):
    with input_path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError("input trajectory is empty")
    required = {"t_sec", "x", "y", "z", "qw", "qx", "qy", "qz"}
    if not required.issubset(rows[0]):
        raise ValueError("input trajectory schema is invalid")
    positions = np.asarray(
        [[float(row[axis]) for axis in ("x", "y", "z")] for row in rows]
    )
    times = np.asarray([float(row["t_sec"]) for row in rows])
    if method == "savgol":
        filtered = smooth_positions(positions, window_length, polynomial_order)
        settings = {
            "method": method,
            "window_length": window_length,
            "polynomial_order": polynomial_order,
        }
    elif method == "gaussian":
        filtered, sigma_samples, temporal_gap_count = gaussian_smooth_positions(
            times, positions, gaussian_sigma_s
        )
        settings = {
            "method": method,
            "sigma_s": gaussian_sigma_s,
            "sigma_samples": sigma_samples,
            "temporal_gap_count": temporal_gap_count,
        }
    else:
        raise ValueError(f"unsupported smoothing method: {method}")
    for row, position in zip(rows, filtered):
        for axis, value in zip(("x", "y", "z"), position):
            row[axis] = f"{value:.12f}"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    if report_path is not None:
        report = {
            "schema": "umi_pose_trajectory_smoothing_v1",
            "result": "PASS",
            "slam_supervision": False,
            "external_ground_truth_used": False,
            "samples": len(rows),
            "input": str(input_path.resolve()),
            "output": str(output_path.resolve()),
            **settings,
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--window-length", type=int, default=7)
    parser.add_argument("--polynomial-order", type=int, default=2)
    parser.add_argument("--method", choices=("savgol", "gaussian"), default="savgol")
    parser.add_argument("--gaussian-sigma-s", type=float, default=0.05)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    run(
        args.input,
        args.output,
        args.window_length,
        args.polynomial_order,
        method=args.method,
        gaussian_sigma_s=args.gaussian_sigma_s,
        report_path=args.report,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
