#!/usr/bin/env python3
"""Estimate the fixed robot-query clock offset from two recorded trajectories.

The estimator uses only motion-speed correlation.  It does not use an endpoint,
hand-eye matrix, Kabsch alignment, or any SLAM output as a constraint.  The
reported sign follows the precision evaluator: ``robot(t_umi + offset)``.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.optimize import minimize_scalar


_COMPARE = Path(__file__).with_name("compare_robot_umi_precision.py")
_SPEC = importlib.util.spec_from_file_location("compare_robot_umi_precision", _COMPARE)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover - import failure
    raise ImportError(f"cannot load {_COMPARE}")
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def load_umi_positions(path: Path) -> tuple[np.ndarray, np.ndarray]:
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if not rows or not {"t_sec", "x", "y", "z"}.issubset(rows[0]):
        raise ValueError(f"invalid UMI trajectory schema: {path}")
    times = np.asarray([float(row["t_sec"]) for row in rows], dtype=float)
    if np.nanmedian(times) > 1.0e12:
        times /= 1.0e9
    positions = np.asarray(
        [[float(row[key]) for key in ("x", "y", "z")] for row in rows],
        dtype=float,
    )
    order = np.argsort(times, kind="stable")
    times, positions = times[order], positions[order]
    keep = np.r_[True, np.diff(times) > 0.0]
    times, positions = times[keep], positions[keep]
    if len(times) < 10 or not np.all(np.isfinite(times)) or not np.all(np.isfinite(positions)):
        raise ValueError(f"UMI trajectory has too few finite samples: {path}")
    return times, positions


def _speed(times: np.ndarray, positions: np.ndarray) -> np.ndarray:
    velocity = np.gradient(positions, times, axis=0, edge_order=1)
    speed = np.linalg.norm(velocity, axis=1)
    # Suppress frame-level noise without erasing the motion events used for
    # correlation.  The window is deliberately short at 30 fps.
    return gaussian_filter1d(speed, sigma=1.2, mode="nearest")


def _normalised_correlation(a: np.ndarray, b: np.ndarray) -> float:
    if a.size < 30:
        return float("nan")
    a = a - np.mean(a)
    b = b - np.mean(b)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(a.dot(b) / denom) if denom > 1.0e-12 else float("nan")


def estimate_offset(
    umi_times: np.ndarray,
    umi_positions: np.ndarray,
    robot_times: np.ndarray,
    robot_positions: np.ndarray,
    *,
    search_min_s: float = -0.5,
    search_max_s: float = 0.5,
    coarse_step_s: float = 0.002,
) -> dict:
    if search_min_s >= search_max_s or coarse_step_s <= 0.0:
        raise ValueError("invalid offset search range")
    umi_speed = _speed(umi_times, umi_positions)
    robot_speed = _speed(robot_times, robot_positions)
    coarse_offsets = np.arange(search_min_s, search_max_s + coarse_step_s * 0.5, coarse_step_s)
    scores: list[tuple[float, float, int]] = []
    for offset in coarse_offsets:
        query = umi_times + offset
        inside = (query >= robot_times[0]) & (query <= robot_times[-1])
        if inside.sum() < 30:
            continue
        sampled = np.interp(query[inside], robot_times, robot_speed)
        score = _normalised_correlation(umi_speed[inside], sampled)
        if np.isfinite(score):
            scores.append((score, float(offset), int(inside.sum())))
    if not scores:
        raise ValueError("no overlapping motion samples in offset search range")
    scores.sort(reverse=True)
    coarse_score, coarse_offset, coarse_samples = scores[0]

    half_window = max(coarse_step_s * 2.0, 0.01)

    def objective(offset: float) -> float:
        query = umi_times + float(offset)
        inside = (query >= robot_times[0]) & (query <= robot_times[-1])
        if inside.sum() < 30:
            return 1.0
        sampled = np.interp(query[inside], robot_times, robot_speed)
        score = _normalised_correlation(umi_speed[inside], sampled)
        return -score if np.isfinite(score) else 1.0

    refined = minimize_scalar(
        objective,
        bounds=(max(search_min_s, coarse_offset - half_window), min(search_max_s, coarse_offset + half_window)),
        method="bounded",
        options={"xatol": 1.0e-5},
    )
    offset = float(refined.x)
    score = float(-refined.fun)
    query = umi_times + offset
    inside = (query >= robot_times[0]) & (query <= robot_times[-1])
    samples = int(inside.sum())

    # A local profile around the optimum provides a conservative stability
    # indicator.  It is not a statistical confidence interval; it flags a
    # flat/ambiguous correlation peak for operator review.
    profile_offsets = np.arange(offset - 0.05, offset + 0.0501, 0.001)
    profile = []
    for candidate in profile_offsets:
        q = umi_times + candidate
        mask = (q >= robot_times[0]) & (q <= robot_times[-1])
        if mask.sum() >= 30:
            profile.append((candidate, -objective(float(candidate))))
    near = [candidate for candidate, candidate_score in profile if candidate_score >= score - 0.005]
    peak_width_ms = (max(near) - min(near)) * 1000.0 if near else float("nan")
    return {
        "schema": "robot_umi_clock_offset_report_v1",
        "robot_query_offset_ms": offset * 1000.0,
        "coarse_offset_ms": coarse_offset * 1000.0,
        "correlation": score,
        "coarse_correlation": coarse_score,
        "overlap_samples": samples,
        "peak_width_at_delta_corr_0p005_ms": peak_width_ms,
        "search_range_ms": [search_min_s * 1000.0, search_max_s * 1000.0],
        "method": "smoothed speed-magnitude cross-correlation; robot pose queried at umi_t + offset",
        "uses_endpoint_constraint": False,
        "uses_handeye_constraint": False,
        "warning": (
            "ambiguous/flat motion correlation; collect a trajectory with acceleration and turns"
            if not np.isfinite(peak_width_ms) or peak_width_ms > 20.0 or score < 0.7
            else None
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="估计UMI与机械臂记录之间的固定时间偏移")
    parser.add_argument("--umi", type=Path, required=True, help="vio_corrected_stream.csv")
    parser.add_argument("--robot", type=Path, required=True, help="robot_joints.jsonl")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--search-min-ms", type=float, default=-500.0)
    parser.add_argument("--search-max-ms", type=float, default=500.0)
    args = parser.parse_args()
    umi_t, umi_p = load_umi_positions(args.umi)
    loaded_robot = _MODULE.load_robot(args.robot.resolve())
    robot_t, robot_T, _, robot_time = loaded_robot
    if robot_time.get("uses_event_time"):
        result = {
            "schema": "robot_umi_clock_offset_report_v1",
            "robot_query_offset_ms": 0.0,
            "method": "shared host epoch from SocketCAN kernel feedback events",
            "uses_endpoint_constraint": False,
            "uses_handeye_constraint": False,
            "robot_timestamp_source": robot_time["timestamp_source"],
            "robot_timestamp_semantics": robot_time["semantics"],
            "warning": None,
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    result = estimate_offset(
        umi_t,
        umi_p,
        robot_t,
        robot_T["actual"][:, :3, 3],
        search_min_s=args.search_min_ms / 1000.0,
        search_max_s=args.search_max_ms / 1000.0,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
