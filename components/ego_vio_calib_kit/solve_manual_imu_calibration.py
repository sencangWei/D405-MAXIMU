#!/usr/bin/env python3
"""求解 manual_imu_calibration_capture.py 产生的 IMU 标定数据。"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import numpy as np
import yaml


ACTION_SLUGS = ("rotate_roll", "rotate_pitch", "rotate_yaw")
AXES = "XYZ"


def trimmed(array: np.ndarray) -> np.ndarray:
    margin = len(array) // 10
    return array[margin : len(array) - margin] if margin else array


def load_segments(session: Path) -> tuple[dict, dict[str, dict[str, np.ndarray]]]:
    manifest = yaml.safe_load((session / "manifest.yaml").read_text(encoding="utf-8"))
    segments = {
        item["slug"]: {key: value for key, value in np.load(item["file"]).items()}
        for item in manifest["segments"]
    }
    return manifest, segments


def solve_accelerometer(segments: dict[str, dict[str, np.ndarray]]) -> dict:
    measured = []
    expected = []
    face_stats = {}
    for axis_index, axis in enumerate("xyz"):
        for sign_name, sign in (("pos", 1.0), ("neg", -1.0)):
            slug = f"static_{sign_name}_{axis}"
            accel = trimmed(segments[slug]["accel_g"])
            gyro = trimmed(segments[slug]["gyro_deg_s"])
            mean = accel.mean(axis=0)
            target = np.zeros(3)
            target[axis_index] = sign
            measured.append(mean)
            expected.append(target)
            face_stats[slug] = {
                "duration_s": float(
                    segments[slug]["timestamp_s"][-1] - segments[slug]["timestamp_s"][0]
                ),
                "accel_mean_g": mean.tolist(),
                "accel_std_g": accel.std(axis=0).tolist(),
                "gyro_mean_deg_s": gyro.mean(axis=0).tolist(),
                "gyro_std_deg_s": gyro.std(axis=0).tolist(),
            }

    design = np.column_stack([np.asarray(measured), np.ones(6)])
    coefficients, _, _, _ = np.linalg.lstsq(design, np.asarray(expected), rcond=None)
    matrix = coefficients[:3].T
    offset = coefficients[3]
    residual = (matrix @ np.asarray(measured).T).T + offset - np.asarray(expected)
    return {
        "matrix": matrix.tolist(),
        "offset_g": offset.tolist(),
        "formula": "a_calibrated_g = matrix @ a_raw_g + offset_g",
        "fit_rmse_g": float(np.sqrt(np.mean(np.square(residual)))),
        "fit_max_abs_g": float(np.max(np.abs(residual))),
        "faces": face_stats,
    }


def extract_rotation_trials(
    data: dict[str, np.ndarray], axis: int, gyro_bias: np.ndarray
) -> list[np.ndarray]:
    timestamp = data["timestamp_s"]
    gyro = data["gyro_deg_s"] - gyro_bias
    state = np.zeros(len(timestamp), dtype=np.int8)
    state[gyro[:, axis] > 3.0] = 1
    state[gyro[:, axis] < -3.0] = -1
    active = np.flatnonzero(state)
    if not len(active):
        return []

    groups = []
    start = previous = int(active[0])
    sign = int(state[start])
    for current_value in active[1:]:
        current = int(current_value)
        if state[current] != sign or timestamp[current] - timestamp[previous] > 0.35:
            groups.append((start, previous))
            start = current
            sign = int(state[current])
        previous = current
    groups.append((start, previous))

    trials = []
    for start, end in groups:
        low = max(0, start - 1)
        high = min(len(timestamp) - 1, end + 1)
        dt = np.diff(timestamp[low : high + 1])
        midpoint = (gyro[low:high] + gyro[low + 1 : high + 1]) * 0.5
        valid = (dt > 0.0) & (dt < 0.02)
        vector = np.sum(midpoint[valid] * dt[valid, None], axis=0)
        if 45.0 <= abs(vector[axis]) <= 135.0:
            trials.append(vector)
    return trials


def solve_gyroscope(
    segments: dict[str, dict[str, np.ndarray]], action_axis_map: str
) -> dict:
    static_gyro = trimmed(segments["static_bias"]["gyro_deg_s"])
    bias = static_gyro.mean(axis=0)
    measured = []
    expected = []
    trial_report = {}
    for slug, axis_name in zip(ACTION_SLUGS, action_axis_map):
        axis = AXES.index(axis_name)
        trials = extract_rotation_trials(segments[slug], axis, bias)
        trial_report[slug] = {
            "actual_physical_axis": axis_name,
            "measured_vectors_deg": [trial.tolist() for trial in trials],
        }
        for trial in trials:
            target = np.zeros(3)
            target[axis] = 90.0 * np.sign(trial[axis])
            measured.append(trial)
            expected.append(target)

    measured_array = np.asarray(measured)
    expected_array = np.asarray(expected)
    coefficients, _, _, _ = np.linalg.lstsq(measured_array, expected_array, rcond=None)
    matrix = coefficients.T
    residual = (matrix @ measured_array.T).T - expected_array
    return {
        "bias_deg_s": bias.tolist(),
        "static_std_deg_s": static_gyro.std(axis=0).tolist(),
        "matrix_candidate": matrix.tolist(),
        "formula": "gyro_calibrated = matrix_candidate @ (gyro_raw - bias_deg_s)",
        "fit_rmse_deg": float(np.sqrt(np.mean(np.square(residual)))),
        "fit_axis_rmse_deg": np.sqrt(np.mean(np.square(residual), axis=0)).tolist(),
        "action_file_axis_map": dict(zip(ACTION_SLUGS, action_axis_map)),
        "trials": trial_report,
        "warning": "手工90度动作只能产生候选比例/串扰矩阵；精确陀螺内参需要转台。",
    }


def timing_quality(segments: dict[str, dict[str, np.ndarray]]) -> dict:
    report = {}
    for slug, data in segments.items():
        timestamp = data["timestamp_s"]
        counter = data["counter"].astype(np.int64)
        dt = np.diff(timestamp)
        report[slug] = {
            "timestamp_nonpositive_steps": int(np.count_nonzero(dt <= 0.0)),
            "timestamp_max_gap_s": float(np.max(dt)) if len(dt) else 0.0,
            "counter_discontinuities": int(np.count_nonzero(np.diff(counter) != 1)),
        }
    return report


def quality_failures(result: dict) -> list[str]:
    failures = []
    for slug, face in result["accelerometer"]["faces"].items():
        if face["duration_s"] < 20.0:
            failures.append(f"{slug} 静止时长 {face['duration_s']:.2f}s < 20s")
        if max(face["accel_std_g"]) >= 0.006:
            failures.append(f"{slug} 加速度静态波动超限")
        if max(face["gyro_std_deg_s"]) >= 0.25:
            failures.append(f"{slug} 角速度静态波动超限")
    for slug, timing in result["timing"].items():
        if timing["timestamp_nonpositive_steps"]:
            failures.append(f"{slug} 时间戳非单调")
        if timing["timestamp_max_gap_s"] > 0.01:
            failures.append(f"{slug} 最大时间间隔 {timing['timestamp_max_gap_s']:.6f}s > 0.01s")
        if timing["counter_discontinuities"]:
            failures.append(f"{slug} counter 不连续 {timing['counter_discontinuities']} 次")
    if result["gyroscope"]["fit_rmse_deg"] > 3.0:
        failures.append(
            f"手工90度旋转拟合 RMSE {result['gyroscope']['fit_rmse_deg']:.2f}° > 3°"
        )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="求解人工门控 IMU 标定数据")
    parser.add_argument("session", type=Path)
    parser.add_argument(
        "--action-axis-map",
        default="XYZ",
        help="rotate_roll,rotate_pitch,rotate_yaw 三个文件的真实物理轴，例如 ZYX",
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    action_axis_map = args.action_axis_map.upper()
    if len(action_axis_map) != 3 or set(action_axis_map) != set(AXES):
        parser.error("--action-axis-map 必须是 XYZ 的一种排列，例如 XYZ 或 ZYX")

    manifest, segments = load_segments(args.session)
    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_manifest": str((args.session / "manifest.yaml").resolve()),
        "source_complete": bool(manifest["complete"]),
        "accelerometer": solve_accelerometer(segments),
        "gyroscope": solve_gyroscope(segments, action_axis_map),
        "timing": timing_quality(segments),
    }
    failures = quality_failures(result)
    result["acceptance"] = {
        "status": "PASS" if not failures else "FAIL",
        "runtime_applied": False,
        "failures": failures,
    }
    output = args.output or args.session / "calibration_candidate.yaml"
    output.write_text(
        yaml.safe_dump(result, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    print(f"结果：{result['acceptance']['status']}")
    for failure in failures:
        print(f"- {failure}")
    print(f"候选标定：{output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
