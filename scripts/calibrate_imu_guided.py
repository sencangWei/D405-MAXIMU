#!/usr/bin/env python3
"""Guided six-face IMU intrinsic calibration with a live Rerun window.

This stage calibrates quantities observable from a standalone six-axis IMU:
accelerometer per-axis bias/scale and static gyroscope bias.  Translation is
intentionally not part of this calibration because an unaided IMU has no
absolute position reference.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from collections import deque
from pathlib import Path
from threading import Lock

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ego_vio.imu.imu_reader import ImuReader
from ego_vio.vio.stub import _quat_mul


# Consecutive faces are orthogonal, so every requested transition is 90 deg.
FACES = ("+Z", "+X", "+Y", "-Z", "-X", "-Y")
AXES = "XYZ"


def classify_face(accel_mean: np.ndarray) -> str | None:
    axis = int(np.argmax(np.abs(accel_mean)))
    if abs(accel_mean[axis]) < 0.85:
        return None
    other = np.delete(np.abs(accel_mean), axis)
    if float(other.max()) > 0.40:
        return None
    sign = "+" if accel_mean[axis] >= 0.0 else "-"
    return f"{sign}{AXES[axis]}"


def solve_calibration(captures: dict[str, dict]) -> dict:
    accel_bias = np.zeros(3)
    accel_scale = np.ones(3)
    cross_axis_residuals = []
    for axis, label in enumerate(AXES):
        plus = captures[f"+{label}"]["accel_mean"]
        minus = captures[f"-{label}"]["accel_mean"]
        span = float(plus[axis] - minus[axis])
        if span < 1.5:
            raise ValueError(f"{label} axis span too small: {span:.4f} g")
        accel_bias[axis] = 0.5 * (plus[axis] + minus[axis])
        accel_scale[axis] = 2.0 / span
        cross_axis_residuals.extend(np.delete(plus, axis).tolist())
        cross_axis_residuals.extend(np.delete(minus, axis).tolist())

    gyro_bias = np.mean(
        [capture["gyro_mean"] for capture in captures.values()], axis=0
    )
    return {
        "accelerometer_bias_g": accel_bias.tolist(),
        "accelerometer_scale": accel_scale.tolist(),
        "gyroscope_bias_deg_s": gyro_bias.tolist(),
        "cross_axis_static_rms_g": float(np.sqrt(np.mean(np.square(cross_axis_residuals)))),
        "model": "a_calibrated_g = diag(accelerometer_scale) * (a_raw_g - accelerometer_bias_g)",
    }


def integrate_rotation(samples: list, begin: int, end: int, gyro_bias_deg_s: np.ndarray) -> tuple[float, np.ndarray]:
    q = np.array([0.0, 0.0, 0.0, 1.0])
    for index in range(max(begin + 1, 1), min(end + 1, len(samples))):
        previous = samples[index - 1]
        current = samples[index]
        dt = float(current.ts - previous.ts)
        if dt <= 0.0 or dt > 0.02:
            continue
        w = np.radians(
            np.array([current.gx, current.gy, current.gz]) - gyro_bias_deg_s
        )
        dq = np.array([w[0] * dt, w[1] * dt, w[2] * dt, 0.0])
        q = q + 0.5 * _quat_mul(q, dq)
        q /= np.linalg.norm(q)
    angle = math.degrees(2.0 * math.acos(float(np.clip(abs(q[3]), 0.0, 1.0))))
    axis = q[:3]
    norm = float(np.linalg.norm(axis))
    if norm > 1e-9:
        axis = axis / norm
    return angle, axis


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--baud", type=int, default=921600)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--window", type=int, default=400, help="stable samples per face")
    parser.add_argument("--hold-seconds", type=float, default=1.5)
    args = parser.parse_args()

    import rerun as rr
    import rerun.blueprint as rrb

    rr.init("ego_vio_imu_six_face_calibration", spawn=True)
    rr.send_blueprint(
        rrb.Blueprint(
            rrb.Horizontal(
                rrb.Spatial3DView(origin="world", name="当前重力方向"),
                rrb.Vertical(
                    rrb.TimeSeriesView(origin="imu/accel/**", name="原始加速度 [g]"),
                    rrb.TimeSeriesView(origin="imu/gyro/**", name="原始角速度 [deg/s]"),
                    rrb.TextLogView(origin="status", name="中文标定提示"),
                ),
                column_shares=[0.55, 0.45],
            ),
            collapse_panels=True,
        )
    )

    samples = deque(maxlen=args.window)
    all_samples = []
    lock = Lock()
    def on_sample(sample) -> None:
        with lock:
            samples.append(sample)
            all_samples.append(sample)

    reader = ImuReader(
        port=args.port,
        baud=args.baud,
        on_sample=on_sample,
        name="imu-six-face",
    )
    if not reader.start():
        return 2

    captures: dict[str, dict] = {}
    stable_face = None
    stable_since = None
    last_status = None
    last_console_time = 0.0
    print("六面标定已启动。每个朝向放稳，系统会自动采集。")

    try:
        while len(captures) < len(FACES):
            time.sleep(0.1)
            with lock:
                window = list(samples)
            if window:
                latest = window[-1]
                rr.set_time("time", timestamp=float(latest.ts))
                for axis, value in zip("xyz", (latest.ax, latest.ay, latest.az)):
                    rr.log(f"imu/accel/{axis}", rr.Scalars([float(value)]))
                for axis, value in zip("xyz", (latest.gx, latest.gy, latest.gz)):
                    rr.log(f"imu/gyro/{axis}", rr.Scalars([float(value)]))
            if len(window) < args.window:
                message = f"正在积累静止样本 {len(window)}/{args.window}，请勿移动"
                if message != last_status:
                    print(message)
                    rr.log("status", rr.TextLog(message))
                    last_status = message
                continue

            accel = np.asarray([(s.ax, s.ay, s.az) for s in window])
            gyro = np.asarray([(s.gx, s.gy, s.gz) for s in window])
            accel_mean = accel.mean(axis=0)
            gyro_mean = gyro.mean(axis=0)
            accel_std = accel.std(axis=0)
            gyro_std = gyro.std(axis=0)
            face = classify_face(accel_mean)
            stable = (
                face is not None
                and float(accel_std.max()) < 0.006
                and float(gyro_std.max()) < 0.25
            )

            rr.log(
                "world/gravity",
                rr.Arrows3D(
                    origins=[[0.0, 0.0, 0.0]],
                    vectors=[accel_mean.tolist()],
                    colors=[[40, 220, 100]],
                ),
            )

            expected_face = FACES[len(captures)]
            if not stable:
                stable_face = None
                stable_since = None
                message = (
                    f"等待放稳… accel_std={accel_std.max():.4f}g "
                    f"gyro_std={gyro_std.max():.3f}deg/s"
                )
            elif face != expected_face:
                stable_face = face
                stable_since = None
                message = f"当前检测为 {face}；本步需要 {expected_face}，请按90°顺序调整"
            else:
                now = time.monotonic()
                if face != stable_face:
                    stable_face = face
                    stable_since = now
                held = now - stable_since
                message = f"检测到 {face}，保持不动 {max(0.0, args.hold_seconds-held):.1f}s"
                if held >= args.hold_seconds:
                    captures[face] = {
                        "accel_mean": accel_mean.copy(),
                        "accel_std": accel_std.copy(),
                        "gyro_mean": gyro_mean.copy(),
                        "gyro_std": gyro_std.copy(),
                        "sample_index": len(all_samples) - 1,
                    }
                    stable_since = None
                    completed = " ".join(item for item in FACES if item in captures)
                    missing = " ".join(item for item in FACES if item not in captures)
                    message = f"✓ 已采集 {face}；完成：{completed}；剩余：{missing or '无'}"
                    print(message)

            if message != last_status:
                rr.log("status", rr.TextLog(message))
                last_status = message
            now = time.monotonic()
            if now - last_console_time >= 1.0:
                print(message, flush=True)
                last_console_time = now

    except KeyboardInterrupt:
        print("标定已取消")
        return 130
    finally:
        reader.stop()

    result = solve_calibration(captures)
    gyro_bias = np.asarray(result["gyroscope_bias_deg_s"])
    rotation_checks = []
    per_axis_scales = {axis: [] for axis in AXES}
    for previous_face, current_face in zip(FACES, FACES[1:]):
        measured_deg, rotation_axis = integrate_rotation(
            all_samples,
            int(captures[previous_face]["sample_index"]),
            int(captures[current_face]["sample_index"]),
            gyro_bias,
        )
        dominant_axis = int(np.argmax(np.abs(rotation_axis)))
        scale = 90.0 / measured_deg if measured_deg > 1.0 else float("nan")
        if math.isfinite(scale):
            per_axis_scales[AXES[dominant_axis]].append(scale)
        rotation_checks.append(
            {
                "from": previous_face,
                "to": current_face,
                "expected_deg": 90.0,
                "measured_deg": measured_deg,
                "error_deg": measured_deg - 90.0,
                "rotation_axis": rotation_axis.tolist(),
                "dominant_gyro_axis": AXES[dominant_axis],
                "scale_correction": scale,
            }
        )
    result["gyroscope_scale"] = [
        float(np.mean(per_axis_scales[axis])) if per_axis_scales[axis] else 1.0
        for axis in AXES
    ]
    result["rotation_90deg_checks"] = rotation_checks
    result["update_rate_hz"] = 400.0
    result["faces"] = {
        face: {
            "accelerometer_mean_g": captures[face]["accel_mean"].tolist(),
            "accelerometer_std_g": captures[face]["accel_std"].tolist(),
            "gyroscope_mean_deg_s": captures[face]["gyro_mean"].tolist(),
            "gyroscope_std_deg_s": captures[face]["gyro_std"].tolist(),
        }
        for face in FACES
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as stream:
        yaml.safe_dump(result, stream, allow_unicode=True, sort_keys=False)

    print("\n=== 六面 IMU 标定完成 ===")
    print(f"accelerometer bias [g]: {np.asarray(result['accelerometer_bias_g'])}")
    print(f"accelerometer scale:    {np.asarray(result['accelerometer_scale'])}")
    print(f"gyroscope bias [deg/s]: {np.asarray(result['gyroscope_bias_deg_s'])}")
    print(f"gyroscope scale:        {np.asarray(result['gyroscope_scale'])}")
    print(f"cross-axis RMS:         {result['cross_axis_static_rms_g']:.6f} g")
    for check in rotation_checks:
        print(
            f"{check['from']} -> {check['to']}: {check['measured_deg']:.3f} deg "
            f"(error {check['error_deg']:+.3f}, gyro axis {check['dominant_gyro_axis']})"
        )
    print(f"saved: {args.output}")
    rr.log("status", rr.TextLog(f"六面标定完成，结果已保存：{args.output}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
