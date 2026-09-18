#!/usr/bin/env python3
"""Interpolate one pose trajectory at another trajectory's timestamps."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation, Slerp


FIELDS = ("t_sec", "x", "y", "z", "qw", "qx", "qy", "qz")


def load_csv(path: Path) -> np.ndarray:
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    if not rows or not set(FIELDS).issubset(rows[0]):
        raise ValueError(f"invalid trajectory schema: {path}")
    trajectory = np.array([[float(row[key]) for key in FIELDS] for row in rows])
    if np.any(np.diff(trajectory[:, 0]) <= 0):
        raise ValueError(f"timestamps must be strictly increasing: {path}")
    return trajectory


def resample(source: np.ndarray, query_times: np.ndarray, max_gap_s: float) -> np.ndarray:
    if np.any(np.diff(query_times) <= 0):
        raise ValueError("query timestamps must be strictly increasing")
    inside = (query_times >= source[0, 0]) & (query_times <= source[-1, 0])
    selected = query_times[inside]
    right = np.searchsorted(source[:, 0], selected, side="right")
    right = np.clip(right, 1, len(source) - 1)
    left = right - 1
    gaps = source[right, 0] - source[left, 0]
    valid = gaps <= max_gap_s
    selected = selected[valid]
    if len(selected) < 2:
        raise ValueError("insufficient query timestamps inside source trajectory")
    positions = np.column_stack(
        [np.interp(selected, source[:, 0], source[:, axis]) for axis in (1, 2, 3)]
    )
    source_xyzw = source[:, [5, 6, 7, 4]]
    rotations_xyzw = Slerp(source[:, 0], Rotation.from_quat(source_xyzw))(
        selected
    ).as_quat()
    return np.column_stack(
        (selected, positions, rotations_xyzw[:, 3], rotations_xyzw[:, :3])
    )


def write_csv(path: Path, trajectory: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(FIELDS)
        for row in trajectory:
            writer.writerow([f"{value:.9f}" for value in row])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--query", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-gap-s", type=float, default=0.1)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    source = load_csv(args.source)
    query = load_csv(args.query)
    result = resample(source, query[:, 0], args.max_gap_s)
    if len(result) != len(query) and not args.allow_partial:
        raise ValueError(
            f"only {len(result)}/{len(query)} query timestamps can be interpolated"
        )
    write_csv(args.output, result)
    print(f"resampled {len(result)} poses -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
