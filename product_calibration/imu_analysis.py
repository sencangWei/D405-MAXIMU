"""Automatic static-bias and Allan analysis for normalized product IMU captures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from .imu_stream import NORMALIZED_FORMAT, NORMALIZED_SIZE


G0 = 9.80665
DTYPE = np.dtype(
    [
        ("timestamp_s", "<f8"),
        ("counter", "<u4"),
        ("gyro_deg_s", "<f4", (3,)),
        ("accel_g", "<f4", (3,)),
        ("temperature_c", "<f4"),
    ]
)
assert DTYPE.itemsize == NORMALIZED_SIZE == 40


def load_capture(path: Path) -> np.memmap:
    path = Path(path)
    if path.stat().st_size % DTYPE.itemsize:
        raise ValueError("imu.bin length is not a whole normalized record")
    return np.memmap(path, dtype=DTYPE, mode="r")


def _summary(capture_dir: Path) -> dict[str, Any]:
    path = Path(capture_dir) / "summary.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def analyze_static(
    capture_dir: Path,
    *,
    warmup_s: float = 120.0,
    formal_s: float = 480.0,
) -> dict[str, Any]:
    samples = load_capture(Path(capture_dir) / "imu.bin")
    if len(samples) < 2:
        raise ValueError("not enough IMU samples")
    elapsed = np.asarray(samples["timestamp_s"] - samples["timestamp_s"][0])
    formal = samples[(elapsed >= warmup_s) & (elapsed <= warmup_s + formal_s)]
    gyro = np.asarray(formal["gyro_deg_s"], dtype=float)
    accel = np.asarray(formal["accel_g"], dtype=float)
    temp = np.asarray(formal["temperature_c"], dtype=float)
    if len(formal) < 2:
        raise ValueError("capture does not contain the formal static window")
    duration = float(formal["timestamp_s"][-1] - formal["timestamp_s"][0])
    rate = (len(formal) - 1) / duration if duration > 0 else 0.0
    gyro_std = np.std(gyro, axis=0)
    accel_std = np.std(accel, axis=0)
    accel_mean = np.mean(accel, axis=0)
    health = _summary(capture_dir)
    checks = {
        "capture_summary_present": bool(health),
        "formal_duration": duration >= formal_s * 0.995,
        "rate_400hz": 395.0 <= rate <= 405.0,
        "counter_gaps_zero": health.get("counter_gaps", 0) == 0,
        "sequence_gaps_zero": health.get("sequence_gaps", 0) == 0,
        "crc_errors_zero": health.get("crc_or_checksum_errors", 0) == 0,
        "discarded_bytes_zero": health.get("discarded_bytes", 0) == 0,
        "invalid_imu_flags_zero": health.get("invalid_imu_flags", 0) == 0,
        "queue_overflow_zero": health.get("queue_overflow_flags", 0) == 0,
        "capture_not_interrupted": not health.get("interrupted", False),
        "gyro_std": bool(np.max(gyro_std) <= 0.15),
        "accel_std": bool(np.max(accel_std) <= 0.006),
        "accel_norm": 0.98 <= float(np.linalg.norm(accel_mean)) <= 1.02,
    }
    return {
        "format_version": 1,
        "result": "PASS" if all(checks.values()) else "FAIL",
        "method": "2min_warmup_plus_8min_static",
        "gyro_bias_deg_s": np.mean(gyro, axis=0).tolist(),
        "gyro_std_deg_s": gyro_std.tolist(),
        "accel_mean_g": accel_mean.tolist(),
        "accel_std_g": accel_std.tolist(),
        "accel_mean_norm_g": float(np.linalg.norm(accel_mean)),
        "temperature_c": {"mean": float(np.mean(temp)), "min": float(np.min(temp)), "max": float(np.max(temp))},
        "metrics": {"formal_duration_s": duration, "samples": len(formal), "rate_hz": rate},
        "capture_health": health,
        "checks": checks,
    }


def allan_deviation(data: np.ndarray, dt: float, max_points: int = 28) -> tuple[np.ndarray, np.ndarray]:
    data = np.asarray(data, dtype=float)
    max_cluster = max(2, len(data) // 4)
    clusters = np.unique(np.geomspace(1, max_cluster, num=max_points).astype(int))
    taus, deviations = [], []
    for cluster in clusters:
        blocks = len(data) // cluster
        if blocks < 4:
            continue
        means = data[: blocks * cluster].reshape(blocks, cluster, 3).mean(axis=1)
        deviations.append(np.sqrt(np.mean(np.diff(means, axis=0) ** 2, axis=0) / 2.0))
        taus.append(cluster * dt)
    return np.asarray(taus), np.asarray(deviations)


def _fixed_slope_coefficient(tau: np.ndarray, deviation: np.ndarray, slope: float, region: slice) -> float:
    x = np.log(tau[region])
    y = np.log(deviation[region])
    if not len(x) or not np.all(np.isfinite(y)):
        raise ValueError("invalid Allan fit region")
    return float(np.exp(np.mean(y - slope * x)))


def analyze_allan(capture_dir: Path, *, min_duration_s: float = 15 * 3600.0) -> dict[str, Any]:
    samples = load_capture(Path(capture_dir) / "imu.bin")
    if len(samples) < 1000:
        raise ValueError("not enough IMU samples for Allan analysis")
    timestamps = np.asarray(samples["timestamp_s"], dtype=float)
    dt = float(np.median(np.diff(timestamps)))
    duration = float(timestamps[-1] - timestamps[0])
    gyro = np.radians(np.asarray(samples["gyro_deg_s"], dtype=float))
    accel = np.asarray(samples["accel_g"], dtype=float) * G0
    outputs = {}
    for name, data in (("gyroscope", gyro), ("accelerometer", accel)):
        tau, axes = allan_deviation(data, dt)
        combined = np.sqrt(np.mean(axes**2, axis=1))
        split = max(2, len(tau) // 2)
        noise = _fixed_slope_coefficient(tau, combined, -0.5, slice(0, split))
        walk = _fixed_slope_coefficient(tau, combined, 0.5, slice(split, None))
        index = int(np.argmin(combined))
        outputs[name] = {
            "noise_density": noise,
            "random_walk": walk,
            "bias_stability": float(combined[index]),
            "bias_stability_tau_s": float(tau[index]),
            "tau_s": tau.tolist(),
            "allan_deviation_axes": axes.tolist(),
        }
    gyro_rms = float(np.sqrt(np.mean((gyro - np.mean(gyro, axis=0)) ** 2)))
    accel_rms = float(np.sqrt(np.mean((accel - np.mean(accel, axis=0)) ** 2)))
    health = _summary(capture_dir)
    checks = {
        "capture_summary_present": bool(health),
        "duration": duration >= min_duration_s,
        "rate_400hz": 395.0 <= 1.0 / dt <= 405.0,
        "counter_gaps_zero": health.get("counter_gaps", 0) == 0,
        "sequence_gaps_zero": health.get("sequence_gaps", 0) == 0,
        "crc_errors_zero": health.get("crc_or_checksum_errors", 0) == 0,
        "discarded_bytes_zero": health.get("discarded_bytes", 0) == 0,
        "invalid_imu_flags_zero": health.get("invalid_imu_flags", 0) == 0,
        "queue_overflow_zero": health.get("queue_overflow_flags", 0) == 0,
        "capture_not_interrupted": not health.get("interrupted", False),
        "stationary_gyro_rms": gyro_rms <= np.radians(0.15),
        "stationary_accel_rms": accel_rms <= 0.006 * G0,
    }
    return {
        "format_version": 1,
        "result": "PASS" if all(checks.values()) else "FAIL",
        "method": "stationary_allan_fixed_slope_fit",
        "duration_s": duration,
        "rate_hz": 1.0 / dt,
        "gyroscope": outputs["gyroscope"],
        "accelerometer": outputs["accelerometer"],
        "stationary_metrics": {"gyro_rms_rad_s": gyro_rms, "accel_rms_m_s2": accel_rms},
        "capture_health": health,
        "checks": checks,
    }
