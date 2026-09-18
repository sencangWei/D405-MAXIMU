#!/usr/bin/env python3
"""Break a trajectory's ground-truth error down by motion speed.

The gate in `evaluate_slam_ground_truth.py` is a single global number, which
cannot distinguish "systematically broken" from "at the motion limit". This
scores the same estimate against the same ground truth but buckets the frames
by ground-truth speed, so a new recording answers the question that actually
matters: at what speed does the error cross 10 mm?

Ground truth is used for scoring only; it never reaches trajectory generation.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import numpy as np


BUCKETS_MM_S = (0, 25, 50, 75, 100, 125, 150, 175, 200, 250)
GATE_MM = 10.0


def load_evaluator():
    sibling = Path(__file__).resolve().parent / "evaluate_slam_ground_truth.py"
    spec = importlib.util.spec_from_file_location("evaluate_slam_ground_truth", sibling)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def score_by_speed(estimate: Path, ground_truth: Path, max_gap_s: float) -> dict:
    ev = load_evaluator()
    est_times, est_positions, _ = ev.load_trajectory(estimate)
    gt_times, gt_positions, gt_quaternions = ev.load_trajectory(ground_truth)
    inside, valid, interpolated, _ = ev.interpolate_ground_truth(
        est_times, gt_times, gt_positions, gt_quaternions, max_gap_s
    )
    times = interpolated[:, 0]
    gt_positions = interpolated[:, 1:4]
    estimate_positions = est_positions[inside][valid]
    if len(estimate_positions) != len(gt_positions):
        raise ValueError("estimate and interpolated ground truth length mismatch")

    rotation, translation = ev.rigid_align(estimate_positions, gt_positions)
    aligned = estimate_positions @ rotation.T + translation
    errors_mm = np.linalg.norm(aligned - gt_positions, axis=1) * 1000.0

    velocity = np.gradient(gt_positions, times, axis=0)
    speed_mm_s = np.linalg.norm(velocity, axis=1) * 1000.0

    rows = []
    for low, high in zip(BUCKETS_MM_S, BUCKETS_MM_S[1:]):
        selected = (speed_mm_s >= low) & (speed_mm_s < high)
        if not selected.any():
            rows.append({"speed_lo_mm_s": low, "speed_hi_mm_s": high, "frames": 0})
            continue
        bucket = errors_mm[selected]
        rows.append(
            {
                "speed_lo_mm_s": low,
                "speed_hi_mm_s": high,
                "frames": int(selected.sum()),
                "share": float(selected.mean()),
                "rmse_mm": float(np.sqrt(np.mean(bucket**2))),
                "p95_mm": float(np.percentile(bucket, 95)),
                "max_mm": float(bucket.max()),
                "within_gate_ratio": float(np.mean(bucket <= GATE_MM)),
            }
        )
    # The tail is what the gate fails on, so it gets its own bucket.
    tail = speed_mm_s >= BUCKETS_MM_S[-1]
    if tail.any():
        bucket = errors_mm[tail]
        rows.append(
            {
                "speed_lo_mm_s": BUCKETS_MM_S[-1],
                "speed_hi_mm_s": None,
                "frames": int(tail.sum()),
                "share": float(tail.mean()),
                "rmse_mm": float(np.sqrt(np.mean(bucket**2))),
                "p95_mm": float(np.percentile(bucket, 95)),
                "max_mm": float(bucket.max()),
                "within_gate_ratio": float(np.mean(bucket <= GATE_MM)),
            }
        )

    scored = [row for row in rows if row["frames"] > 0]
    # Two independent crossings: where the bucket's typical error reaches the
    # gate, and where the bucket stops holding the gate for 95% of its frames.
    rmse_crossing = [row["speed_lo_mm_s"] for row in scored if row["rmse_mm"] > GATE_MM]
    ratio_crossing = [
        row["speed_lo_mm_s"] for row in scored if row["within_gate_ratio"] < 0.95
    ]
    return {
        "samples": int(len(errors_mm)),
        "duration_s": float(times[-1] - times[0]),
        "grid_step_s": float(np.median(np.diff(times))),
        "overall": {
            "rmse_mm": float(np.sqrt(np.mean(errors_mm**2))),
            "p95_mm": float(np.percentile(errors_mm, 95)),
            "max_mm": float(errors_mm.max()),
            "within_gate_ratio": float(np.mean(errors_mm <= GATE_MM)),
        },
        "speed_p95_mm_s": float(np.percentile(speed_mm_s, 95)),
        "speed_max_mm_s": float(speed_mm_s.max()),
        # Speed from which the gate is no longer held, by either reading.
        "gate_rmse_crossing_speed_mm_s": (
            float(min(rmse_crossing)) if rmse_crossing else None
        ),
        "gate_ratio_crossing_speed_mm_s": (
            float(min(ratio_crossing)) if ratio_crossing else None
        ),
        "buckets": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--estimate", type=Path, required=True)
    parser.add_argument("--ground-truth", type=Path, required=True)
    parser.add_argument("--max-interpolation-gap-s", type=float, default=0.1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = score_by_speed(
        args.estimate, args.ground_truth, args.max_interpolation_gap_s
    )
    report["schema"] = "umi_error_vs_motion_v1"
    report["estimate"] = str(args.estimate.resolve())
    report["ground_truth"] = str(args.ground_truth.resolve())
    report["ground_truth_role"] = "scoring_only"
    report["external_ground_truth_used"] = False

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    print(f"样本 {report['samples']}  时长 {report['duration_s']:.1f}s")
    print(
        f"整体 RMSE {report['overall']['rmse_mm']:.3f} mm  "
        f"P95 {report['overall']['p95_mm']:.2f} mm  "
        f"MAX {report['overall']['max_mm']:.2f} mm  "
        f"{GATE_MM:.0f}mm 内 {100 * report['overall']['within_gate_ratio']:.2f}%"
    )
    print(
        f"速度 p95 {report['speed_p95_mm_s']:.0f} mm/s  "
        f"max {report['speed_max_mm_s']:.0f} mm/s"
    )
    rmse_crossing = report["gate_rmse_crossing_speed_mm_s"]
    ratio_crossing = report["gate_ratio_crossing_speed_mm_s"]
    print(
        "越过门限的速度: RMSE "
        + (f"{rmse_crossing:.0f} mm/s 起" if rmse_crossing is not None else "全程未越")
        + "  |  10mm 内占比 <95% "
        + (f"{ratio_crossing:.0f} mm/s 起" if ratio_crossing is not None else "全程未越")
    )
    print(
        f"{'速度段 mm/s':>16}{'帧数':>7}{'占比':>8}{'RMSE':>8}{'P95':>8}{'MAX':>8}{'10mm内':>9}"
    )
    for row in report["buckets"]:
        if row["frames"] == 0:
            continue
        high = "∞" if row["speed_hi_mm_s"] is None else f"{row['speed_hi_mm_s']:.0f}"
        span = f"{row['speed_lo_mm_s']:.0f}-{high}"
        print(
            f"{span:>16}{row['frames']:>7}{100 * row['share']:>7.1f}%"
            f"{row['rmse_mm']:>8.2f}{row['p95_mm']:>8.2f}{row['max_mm']:>8.2f}"
            f"{100 * row['within_gate_ratio']:>8.1f}%"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
