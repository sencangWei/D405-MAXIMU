#!/usr/bin/env python3
"""Replay one recorded IMU stream through three dead-reckoning baselines.

The three trajectories deliberately answer different questions:
  1. image_style: subtract the initial acceleration vector and double-integrate
     in the initial body axes (the simple algorithm shown by the user).
  2. strapdown: integrate gyro attitude, rotate acceleration into the world,
     subtract gravity, then double-integrate.
  3. strapdown_zupt: the same strapdown solution with a conservative zero-
     velocity reset while the raw IMU is stationary.

All paths use the recorded per-sample timestamp.  The script also reports
counter continuity and timestamp quality before displaying anything.
"""

from __future__ import annotations

import argparse
import math
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ego_vio.vio.stub import _quat_from_two_vectors, _quat_mul, _quat_rotate


IMU_FMT = "<dI7f"
IMU_SIZE = struct.calcsize(IMU_FMT)
G = 9.81


def load_imu(path: Path) -> dict[str, np.ndarray]:
    rows = []
    with path.open("rb") as stream:
        while chunk := stream.read(IMU_SIZE):
            if len(chunk) != IMU_SIZE:
                raise ValueError(f"truncated IMU record: {len(chunk)} bytes")
            rows.append(struct.unpack(IMU_FMT, chunk))
    if not rows:
        raise ValueError(f"no IMU samples in {path}")
    data = np.asarray(rows, dtype=np.float64)
    return {
        "time": data[:, 0],
        "counter": data[:, 1].astype(np.int64),
        "gyro_deg_s": data[:, 2:5],
        "accel_g": data[:, 5:8],
        "temperature_c": data[:, 8],
    }


def integrate(data: dict[str, np.ndarray], static_seconds: float) -> dict[str, np.ndarray]:
    time_s = data["time"]
    gyro = np.radians(data["gyro_deg_s"])
    accel = data["accel_g"]
    dt_all = np.diff(time_s)
    positive = dt_all[dt_all > 0.0]
    if len(positive) != len(dt_all):
        raise ValueError("IMU timestamps are not strictly increasing")

    nominal_dt = float(np.median(positive))
    calibration_count = min(
        len(time_s), max(20, int(round(static_seconds / nominal_dt)))
    )
    gyro_bias = gyro[:calibration_count].mean(axis=0)
    accel_mean = accel[:calibration_count].mean(axis=0)
    gravity_norm = float(np.linalg.norm(accel_mean))
    if not 0.8 < gravity_norm < 1.2:
        raise ValueError(f"invalid static gravity magnitude: {gravity_norm:.3f} g")

    q = _quat_from_two_vectors(
        accel_mean / gravity_norm, np.array([0.0, 0.0, 1.0])
    )
    q_hist = np.zeros((len(time_s), 4), dtype=np.float64)
    q_hist[:calibration_count] = q

    image_pos = np.zeros((len(time_s), 3), dtype=np.float64)
    strap_pos = np.zeros_like(image_pos)
    zupt_pos = np.zeros_like(image_pos)
    image_vel = np.zeros(3)
    strap_vel = np.zeros(3)
    zupt_vel = np.zeros(3)
    stationary = np.zeros(len(time_s), dtype=bool)
    stationary[:calibration_count] = True

    for index in range(calibration_count, len(time_s)):
        dt = float(time_s[index] - time_s[index - 1])
        w = gyro[index] - gyro_bias
        dq = np.array([w[0] * dt, w[1] * dt, w[2] * dt, 0.0])
        q = q + 0.5 * _quat_mul(q, dq)
        q /= np.linalg.norm(q)
        q_hist[index] = q

        # Screenshot-style baseline: assumes the initial body axes never rotate.
        a_image = (accel[index] - accel_mean) * G
        image_pos[index] = image_pos[index - 1] + image_vel * dt + 0.5 * a_image * dt * dt
        image_vel += a_image * dt

        # Strapdown INS: rotate specific force into the world, then remove gravity.
        a_world = _quat_rotate(accel[index], q) * G + np.array([0.0, 0.0, -G])
        strap_pos[index] = strap_pos[index - 1] + strap_vel * dt + 0.5 * a_world * dt * dt
        strap_vel += a_world * dt

        is_stationary = (
            np.linalg.norm(w) < math.radians(0.8)
            and abs(np.linalg.norm(accel[index]) - gravity_norm) < 0.015
        )
        stationary[index] = is_stationary
        zupt_pos[index] = zupt_pos[index - 1] + zupt_vel * dt + 0.5 * a_world * dt * dt
        zupt_vel += a_world * dt
        if is_stationary:
            zupt_vel[:] = 0.0

    return {
        "image_style": image_pos,
        "strapdown": strap_pos,
        "strapdown_zupt": zupt_pos,
        "quaternion": q_hist,
        "stationary": stationary,
        "gyro_bias_rad_s": gyro_bias,
        "accel_mean_g": accel_mean,
        "calibration_count": np.array([calibration_count]),
    }


def print_report(data: dict[str, np.ndarray], result: dict[str, np.ndarray]) -> None:
    time_s = data["time"]
    dt_ms = np.diff(time_s) * 1000.0
    counter_step = np.diff(data["counter"])
    counter_gaps = int(np.count_nonzero(counter_step != 1))
    duration = float(time_s[-1] - time_s[0])
    print("=== IMU-only trajectory report ===")
    print(f"samples={len(time_s)} duration={duration:.3f}s rate={(len(time_s)-1)/duration:.3f}Hz")
    print(
        f"timestamp dt: median={np.median(dt_ms):.6f}ms "
        f"min={dt_ms.min():.6f}ms max={dt_ms.max():.6f}ms "
        f"std={dt_ms.std():.6f}ms"
    )
    print(f"counter discontinuities={counter_gaps}")
    print(
        "gyro bias [deg/s]="
        + np.array2string(np.degrees(result["gyro_bias_rad_s"]), precision=6)
    )
    print(
        "static accel mean [g]="
        + np.array2string(result["accel_mean_g"], precision=6)
        + f" norm={np.linalg.norm(result['accel_mean_g']):.6f}g"
    )
    gyro_norm = np.linalg.norm(data["gyro_deg_s"], axis=1)
    accel_norm = np.linalg.norm(data["accel_g"], axis=1)
    print(
        f"raw gyro norm: median={np.median(gyro_norm):.4f}deg/s "
        f"p99={np.percentile(gyro_norm, 99):.4f}deg/s max={gyro_norm.max():.4f}deg/s"
    )
    print(
        f"raw accel norm: median={np.median(accel_norm):.6f}g "
        f"p01={np.percentile(accel_norm, 1):.6f}g "
        f"p99={np.percentile(accel_norm, 99):.6f}g"
    )
    for name in ("image_style", "strapdown", "strapdown_zupt"):
        path = result[name]
        final = path[-1]
        radius = np.linalg.norm(path, axis=1)
        print(
            f"{name:16s} final={np.array2string(final, precision=4)}m "
            f"final_norm={np.linalg.norm(final):.4f}m max_radius={radius.max():.4f}m"
        )
    calibration_count = int(result["calibration_count"][0])
    q_start = result["quaternion"][calibration_count - 1]
    q_end = result["quaternion"][-1]
    attitude_error_deg = math.degrees(
        2.0 * math.acos(float(np.clip(abs(np.dot(q_start, q_end)), 0.0, 1.0)))
    )
    print(f"gyro-only final attitude change={attitude_error_deg:.4f}deg")
    print(f"stationary decisions={result['stationary'].mean()*100.0:.1f}%")


def show_rerun(data: dict[str, np.ndarray], result: dict[str, np.ndarray]) -> None:
    import rerun as rr
    import rerun.blueprint as rrb

    rr.init("ego_vio_imu_only_ab", spawn=True)
    blueprint = rrb.Blueprint(
        rrb.Horizontal(
            rrb.Spatial3DView(origin="world", name="IMU-only trajectories"),
            rrb.Vertical(
                rrb.TimeSeriesView(origin="imu/gyro/**", name="Raw gyro [rad/s]"),
                rrb.TimeSeriesView(origin="imu/accel/**", name="Raw accel [m/s²]"),
            ),
            column_shares=[0.68, 0.32],
        ),
        collapse_panels=True,
    )
    rr.send_blueprint(blueprint)

    colors = {
        "image_style": [255, 170, 0],
        "strapdown": [255, 70, 70],
        "strapdown_zupt": [40, 220, 100],
    }
    for name, color in colors.items():
        rr.log(
            f"world/{name}",
            rr.LineStrips3D([result[name].tolist()], colors=[color], radii=[0.003]),
            static=True,
        )

    # Raw curves remain raw measurements; only units are converted to SI.
    stride = max(1, int(round(len(data["time"]) / max(len(data["time"]), 12000))))
    for index in range(0, len(data["time"]), stride):
        rr.set_time("time", timestamp=float(data["time"][index]))
        gyro = np.radians(data["gyro_deg_s"][index])
        accel = data["accel_g"][index] * G
        for axis, value in zip("xyz", gyro):
            rr.log(f"imu/gyro/{axis}", rr.Scalars([float(value)]))
        for axis, value in zip("xyz", accel):
            rr.log(f"imu/accel/{axis}", rr.Scalars([float(value)]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path, help="recording session directory or imu.bin")
    parser.add_argument("--unit", default="left_hand")
    parser.add_argument("--static-seconds", type=float, default=3.0)
    parser.add_argument("--no-viz", action="store_true")
    args = parser.parse_args()

    imu_path = args.input
    if imu_path.is_dir():
        imu_path = imu_path / args.unit / "imu.bin"
    data = load_imu(imu_path)
    result = integrate(data, args.static_seconds)
    print_report(data, result)
    if not args.no_viz:
        show_rerun(data, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
