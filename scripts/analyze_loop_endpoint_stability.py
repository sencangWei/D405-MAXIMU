#!/usr/bin/env python3
"""Audit closed-loop endpoint stability without modifying the trajectory."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path


DEFAULT_WINDOWS_S = (1.0, 2.0, 3.0)


def read_trajectory(path: Path) -> list[tuple[float, tuple[float, float, float]]]:
    samples: list[tuple[float, tuple[float, float, float]]] = []
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        required = {"t_sec", "x", "y", "z"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f"trajectory lacks t_sec/x/y/z columns: {path}")
        previous_time: float | None = None
        for line_number, row in enumerate(reader, start=2):
            try:
                timestamp = float(row["t_sec"])
                point = tuple(float(row[axis]) for axis in ("x", "y", "z"))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"invalid trajectory point at {path}:{line_number}"
                ) from exc
            if not math.isfinite(timestamp) or not all(math.isfinite(v) for v in point):
                raise ValueError(
                    f"non-finite trajectory point at {path}:{line_number}"
                )
            if previous_time is not None and timestamp <= previous_time:
                raise ValueError(
                    f"non-increasing trajectory timestamp at {path}:{line_number}"
                )
            previous_time = timestamp
            samples.append((timestamp, point))
    if len(samples) < 2:
        raise ValueError(f"trajectory needs at least two samples: {path}")
    return samples


def center(points: list[tuple[float, float, float]], method: str) -> tuple[float, ...]:
    reducer = statistics.fmean if method == "mean" else statistics.median
    return tuple(reducer(point[axis] for point in points) for axis in range(3))


def dispersion(
    points: list[tuple[float, float, float]],
    center_point: tuple[float, ...],
) -> dict[str, float]:
    distances = sorted(math.dist(point, center_point) for point in points)
    p95_index = min(len(distances) - 1, math.ceil(0.95 * len(distances)) - 1)
    return {
        "rms_radius_m": math.sqrt(statistics.fmean(value * value for value in distances)),
        "p95_radius_m": distances[p95_index],
        "max_radius_m": distances[-1],
    }


def select_window(
    samples: list[tuple[float, tuple[float, float, float]]],
    duration_s: float,
    side: str,
) -> list[tuple[float, float, float]]:
    if duration_s <= 0:
        raise ValueError("window duration must be positive")
    if side == "start":
        limit = samples[0][0] + duration_s
        return [point for timestamp, point in samples if timestamp <= limit]
    limit = samples[-1][0] - duration_s
    return [point for timestamp, point in samples if timestamp >= limit]


def analyze(
    samples: list[tuple[float, tuple[float, float, float]]],
    windows_s: tuple[float, ...] = DEFAULT_WINDOWS_S,
    threshold_m: float = 0.01,
) -> dict:
    if threshold_m <= 0:
        raise ValueError("threshold must be positive")
    point_endpoint_delta_xyz_m = [
        samples[-1][1][axis] - samples[0][1][axis] for axis in range(3)
    ]
    point_endpoint_m = math.dist(samples[0][1], samples[-1][1])
    windows = []
    for duration_s in windows_s:
        start_points = select_window(samples, duration_s, "start")
        end_points = select_window(samples, duration_s, "end")
        methods = {}
        for method in ("mean", "median"):
            start_center = center(start_points, method)
            end_center = center(end_points, method)
            center_delta_xyz_m = [
                end_center[axis] - start_center[axis] for axis in range(3)
            ]
            methods[method] = {
                "start_center_m": start_center,
                "end_center_m": end_center,
                "center_delta_xyz_m": center_delta_xyz_m,
                "center_delta_m": math.dist(start_center, end_center),
                "start_dispersion": dispersion(start_points, start_center),
                "end_dispersion": dispersion(end_points, end_center),
            }
        windows.append(
            {
                "duration_s": duration_s,
                "start_samples": len(start_points),
                "end_samples": len(end_points),
                "methods": methods,
                "sub_centimeter": all(
                    item["center_delta_m"] < threshold_m for item in methods.values()
                ),
            }
        )
    return {
        "truth_usage": "post_run_audit_only",
        "trajectory_modified": False,
        "threshold_m": threshold_m,
        "sample_count": len(samples),
        "duration_s": samples[-1][0] - samples[0][0],
        "point_endpoint_delta_xyz_m": point_endpoint_delta_xyz_m,
        "point_endpoint_delta_m": point_endpoint_m,
        "point_endpoint_sub_centimeter": point_endpoint_m < threshold_m,
        "windows": windows,
        "stable_sub_centimeter_all_windows": (
            point_endpoint_m < threshold_m
            and all(window["sub_centimeter"] for window in windows)
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trajectory", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--threshold-m", type=float, default=0.01)
    parser.add_argument(
        "--windows-s",
        type=float,
        nargs="+",
        default=list(DEFAULT_WINDOWS_S),
    )
    args = parser.parse_args()
    result = {
        "trajectory": str(args.trajectory.resolve()),
        **analyze(
            read_trajectory(args.trajectory),
            tuple(args.windows_s),
            args.threshold_m,
        ),
    }
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
