#!/usr/bin/env python3
"""Estimate Tracker query time offset from the UMI gyroscope only.

The norm of angular velocity is invariant to the unknown Tracker-to-body
rotation.  This lets timing be estimated before, and independently from, the
rigid external calibration.  No SLAM trajectory is accepted as input.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.optimize import minimize_scalar
from scipy.spatial.transform import Rotation

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from calibrate_lighthouse_umi import load_tracker


IMU_DTYPE = np.dtype(
    [
        ("ts", "<f8"),
        ("counter", "<u4"),
        ("gx", "<f4"),
        ("gy", "<f4"),
        ("gz", "<f4"),
        ("ax", "<f4"),
        ("ay", "<f4"),
        ("az", "<f4"),
        ("temp", "<f4"),
    ]
)


def load_imu(path: Path) -> tuple[np.ndarray, np.ndarray]:
    records = np.fromfile(path, dtype=IMU_DTYPE)
    if len(records) < 400:
        raise ValueError(f"too few IMU samples: {len(records)}")
    times = records["ts"].astype(float)
    if np.any(np.diff(times) <= 0.0):
        raise ValueError(f"IMU timestamps are not strictly increasing: {path}")
    gyro = np.column_stack(
        [records[name].astype(float) for name in ("gx", "gy", "gz")]
    )
    return times, np.radians(gyro)


def tracker_angular_speed(
    times: np.ndarray, quaternions: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    elapsed = np.diff(times)
    valid = (elapsed > 0.001) & (elapsed < 0.05)
    if valid.sum() < 100:
        raise ValueError("too few valid Tracker angular-velocity intervals")
    rotations = Rotation.from_quat(quaternions)
    delta_angle = (rotations[:-1].inv() * rotations[1:]).magnitude()
    midpoint = (times[:-1] + times[1:]) * 0.5
    return midpoint[valid], (delta_angle / elapsed)[valid]


def robust_normalize(values: np.ndarray) -> np.ndarray:
    median = float(np.median(values))
    scale = float(np.median(np.abs(values - median)) * 1.4826)
    if scale < 1.0e-6:
        scale = float(np.std(values))
    if scale < 1.0e-6:
        raise ValueError("angular motion has no measurable variation")
    return np.clip((values - median) / scale, -5.0, 10.0)


def estimate_offset(
    imu_times: np.ndarray,
    imu_gyro: np.ndarray,
    tracker_times: np.ndarray,
    tracker_quaternions: np.ndarray,
    search_ms: float = 30.0,
) -> dict[str, float]:
    tracker_speed_times, tracker_speed = tracker_angular_speed(
        tracker_times, tracker_quaternions
    )
    step_s = 0.0025
    search_s = search_ms / 1000.0
    start = max(float(imu_times[0]), float(tracker_speed_times[0] - search_s)) + 0.1
    end = min(float(imu_times[-1]), float(tracker_speed_times[-1] + search_s)) - 0.1
    if end - start < 5.0:
        raise ValueError(f"IMU/Tracker overlap is too short: {end - start:.3f}s")
    grid = np.arange(start, end, step_s)
    imu_speed = np.linalg.norm(imu_gyro, axis=1)
    tracker_clip_rad_s = max(
        float(np.percentile(imu_speed, 99.9) * 1.5),
        float(np.percentile(tracker_speed, 95.0) * 1.5),
    )
    tracker_outliers = tracker_speed > tracker_clip_rad_s
    if tracker_outliers.any():
        tracker_speed = np.interp(
            tracker_speed_times,
            tracker_speed_times[~tracker_outliers],
            tracker_speed[~tracker_outliers],
        )
    sigma_samples = 0.010 / step_s
    imu_signal = robust_normalize(
        gaussian_filter1d(np.interp(grid, imu_times, imu_speed), sigma_samples)
    )

    def correlation(offset_s: float) -> float:
        signal = robust_normalize(
            gaussian_filter1d(
                np.interp(grid + offset_s, tracker_speed_times, tracker_speed),
                sigma_samples,
            )
        )
        active = (imu_signal > 0.5) | (signal > 0.5)
        if active.sum() < 200:
            raise ValueError("too little rotational excitation for time alignment")
        return float(np.corrcoef(imu_signal[active], signal[active])[0, 1])

    coarse_offsets = np.linspace(-search_s, search_s, 241)
    coarse_correlations = np.asarray(
        [correlation(float(offset)) for offset in coarse_offsets]
    )
    coarse_index = int(np.nanargmax(coarse_correlations))
    coarse_best = float(coarse_offsets[coarse_index])
    half_window = 0.0015
    refined = minimize_scalar(
        lambda value: -correlation(float(value)),
        bounds=(
            max(-search_s, coarse_best - half_window),
            min(search_s, coarse_best + half_window),
        ),
        method="bounded",
        options={"xatol": 1.0e-5},
    )
    offset_s = float(refined.x)
    best_correlation = correlation(offset_s)
    return {
        "tracker_query_offset_ms": offset_s * 1000.0,
        "correlation": best_correlation,
        "overlap_duration_s": end - start,
        "imu_rate_hz": float(1.0 / np.median(np.diff(imu_times))),
        "tracker_rate_hz": float(1.0 / np.median(np.diff(tracker_times))),
        "tracker_speed_clip_deg_s": float(np.degrees(tracker_clip_rad_s)),
        "tracker_speed_clipped_samples": int(tracker_outliers.sum()),
        "tracker_speed_clipped_ratio": float(tracker_outliers.mean()),
        "search_range_ms": search_ms,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--capture",
        nargs=2,
        action="append",
        required=True,
        metavar=("IMU_BIN", "TRACKER_CSV"),
        help="repeat for independent captures",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--search-ms", type=float, default=30.0)
    args = parser.parse_args()

    captures = []
    serials = []
    for imu_path, tracker_path in args.capture:
        imu_path = Path(imu_path).resolve()
        tracker_path = Path(tracker_path).resolve()
        imu_times, imu_gyro = load_imu(imu_path)
        tracker_times, _, tracker_quaternions, serial = load_tracker(
            tracker_path, "host_monotonic"
        )
        estimate = estimate_offset(
            imu_times,
            imu_gyro,
            tracker_times,
            tracker_quaternions,
            args.search_ms,
        )
        captures.append(
            {
                "imu": str(imu_path),
                "tracker": str(tracker_path),
                **estimate,
            }
        )
        serials.append(serial)
    if len(set(serials)) != 1:
        raise ValueError(f"captures use different Trackers: {sorted(set(serials))}")
    offsets = np.asarray([item["tracker_query_offset_ms"] for item in captures])
    correlations = np.asarray([item["correlation"] for item in captures])
    spread_ms = float(np.ptp(offsets)) if len(offsets) > 1 else 0.0
    maximum_clipped_ratio = max(
        item["tracker_speed_clipped_ratio"] for item in captures
    )
    passed = bool(
        np.min(correlations) >= 0.80
        and spread_ms <= 2.0
        and maximum_clipped_ratio <= 0.005
    )
    report = {
        "schema": "lighthouse_imu_time_offset_v1",
        "result": "PASS_CANDIDATE" if passed else "DIAGNOSTIC_CANDIDATE",
        "method": "coordinate-invariant angular-speed correlation",
        "slam_supervision": False,
        "slam_inputs": [],
        "tracker_serial": serials[0],
        "tracker_time_source": "host_monotonic",
        "tracker_query_offset_ms": float(np.median(offsets)),
        "repeat_spread_ms": spread_ms,
        "captures": captures,
        "acceptance": {
            "minimum_correlation": float(np.min(correlations)),
            "maximum_repeat_spread_ms": spread_ms,
            "maximum_tracker_speed_clipped_ratio": maximum_clipped_ratio,
            "thresholds": {
                "minimum_correlation": 0.80,
                "maximum_repeat_spread_ms": 2.0,
                "maximum_tracker_speed_clipped_ratio": 0.005,
            },
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
