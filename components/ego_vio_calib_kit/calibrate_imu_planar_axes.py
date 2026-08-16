#!/usr/bin/env python3
"""用静止、水平X平移、水平Z平移标定IMU到水平刚体坐标系的旋转。"""

from __future__ import annotations

import argparse
import math
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import yaml


ROOT = Path(__file__).resolve().parent
HUMBLE_ROOT = Path("/home/robot/ego_vio_humble")
DEFAULT_CONFIG = HUMBLE_ROOT / "config/devices_ubuntu.yaml"
DEFAULT_INTRINSIC = ROOT / "imu_manual_calibration/intrinsic_20260803_000908/calibration_candidate.yaml"
DEFAULT_OUTPUT_ROOT = ROOT / "world_z_calibration"

sys.path.insert(0, str(HUMBLE_ROOT))
from ego_vio.config import load_config  # noqa: E402
from ego_vio.imu.imu_reader import ImuReader  # noqa: E402


# 用户已经确认：IMU +X 是一个水平轴，+Z 是另一个水平轴且指向左，+Y向下。
R_LEVEL_FROM_IMU_NOMINAL = np.array(
    [[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]], dtype=float
)
R_LEVEL_FROM_IMU_CURRENT_PIPELINE = np.array(
    [
        [0.99980212, -0.01423891, -0.01389161],
        [-0.01423891, -0.02458715, -0.99959628],
        [0.01389161, 0.99959628, -0.02478503],
    ], dtype=float
)


@dataclass(frozen=True)
class Thresholds:
    min_static_s: float = 5.0
    min_motion_s: float = 8.0
    min_static_samples: int = 1800
    min_motion_samples: int = 3000
    min_rate_hz: float = 399.0
    max_rate_hz: float = 401.0
    max_static_accel_std_g: float = 0.003
    max_static_gyro_std_deg_s: float = 0.15
    min_static_accel_norm_g: float = 0.98
    max_static_accel_norm_g: float = 1.02
    min_motion_rms_g: float = 0.010
    min_principal_ratio: float = 0.60
    min_axes_angle_deg: float = 75.0
    max_axes_angle_deg: float = 105.0
    max_fit_gyro_deg_s: float = 5.0
    min_gyro_gated_fraction: float = 0.50
    max_vertical_disagreement_deg: float = 5.0
    max_counter_drop_events: int = 0
    max_counter_resets: int = 0
    max_serial_errors: int = 0


@dataclass(frozen=True)
class Row:
    timestamp_s: float
    receiver_timestamp_s: float
    counter: int
    gyro_deg_s: tuple[float, float, float]
    accel_g: tuple[float, float, float]
    temperature_c: float


def unit(vector: Sequence[float]) -> np.ndarray:
    value = np.asarray(vector, dtype=float)
    norm = float(np.linalg.norm(value))
    if norm <= 1e-12:
        raise ValueError("零向量不能归一化")
    return value / norm


def angle_deg(first: Sequence[float], second: Sequence[float]) -> float:
    return math.degrees(math.acos(float(np.clip(np.dot(unit(first), unit(second)), -1.0, 1.0))))


def rotation_angle_deg(rotation: np.ndarray) -> float:
    cosine = float(np.clip((np.trace(rotation) - 1.0) * 0.5, -1.0, 1.0))
    return math.degrees(math.acos(cosine))


def rotation_align_vectors(source: Sequence[float], target: Sequence[float]) -> np.ndarray:
    """Return the smallest proper rotation that maps source onto target."""
    source_unit = unit(source)
    target_unit = unit(target)
    cross = np.cross(source_unit, target_unit)
    sine = float(np.linalg.norm(cross))
    cosine = float(np.clip(np.dot(source_unit, target_unit), -1.0, 1.0))
    if sine <= 1e-12:
        if cosine > 0.0:
            return np.eye(3)
        helper = np.array([1.0, 0.0, 0.0])
        if abs(float(source_unit[0])) > 0.9:
            helper = np.array([0.0, 1.0, 0.0])
        axis = unit(np.cross(source_unit, helper))
        return 2.0 * np.outer(axis, axis) - np.eye(3)
    axis = cross / sine
    skew = np.array(
        [[0.0, -axis[2], axis[1]], [axis[2], 0.0, -axis[0]], [-axis[1], axis[0], 0.0]]
    )
    return np.eye(3) + sine * skew + (1.0 - cosine) * (skew @ skew)


def matrix_to_quaternion_xyzw(rotation: np.ndarray) -> list[float]:
    matrix = np.asarray(rotation, dtype=float)
    trace = float(np.trace(matrix))
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0) * 2.0
        quaternion = np.array([
            (matrix[2, 1] - matrix[1, 2]) / scale,
            (matrix[0, 2] - matrix[2, 0]) / scale,
            (matrix[1, 0] - matrix[0, 1]) / scale,
            0.25 * scale,
        ])
    else:
        index = int(np.argmax(np.diag(matrix)))
        if index == 0:
            scale = math.sqrt(1.0 + matrix[0, 0] - matrix[1, 1] - matrix[2, 2]) * 2.0
            quaternion = np.array([
                0.25 * scale,
                (matrix[0, 1] + matrix[1, 0]) / scale,
                (matrix[0, 2] + matrix[2, 0]) / scale,
                (matrix[2, 1] - matrix[1, 2]) / scale,
            ])
        elif index == 1:
            scale = math.sqrt(1.0 + matrix[1, 1] - matrix[0, 0] - matrix[2, 2]) * 2.0
            quaternion = np.array([
                (matrix[0, 1] + matrix[1, 0]) / scale,
                0.25 * scale,
                (matrix[1, 2] + matrix[2, 1]) / scale,
                (matrix[0, 2] - matrix[2, 0]) / scale,
            ])
        else:
            scale = math.sqrt(1.0 + matrix[2, 2] - matrix[0, 0] - matrix[1, 1]) * 2.0
            quaternion = np.array([
                (matrix[0, 2] + matrix[2, 0]) / scale,
                (matrix[1, 2] + matrix[2, 1]) / scale,
                0.25 * scale,
                (matrix[1, 0] - matrix[0, 1]) / scale,
            ])
    quaternion = unit(quaternion)
    if quaternion[3] < 0.0:
        quaternion = -quaternion
    return [float(value) for value in quaternion]


def principal_direction(samples: np.ndarray, nominal_axis: Sequence[float]) -> dict[str, Any]:
    centered = np.asarray(samples, dtype=float) - np.mean(samples, axis=0)
    _, singular_values, axes = np.linalg.svd(centered, full_matrices=False)
    direction = axes[0]
    if float(np.dot(direction, nominal_axis)) < 0.0:
        direction = -direction
    power = singular_values ** 2
    ratio = float(power[0] / np.sum(power)) if float(np.sum(power)) > 0.0 else 0.0
    projection = centered @ direction
    return {
        "direction": unit(direction),
        "principal_ratio": ratio,
        "motion_rms_g": float(np.sqrt(np.mean(projection ** 2))),
    }


def solve_planar_axes(
    static_accel: np.ndarray,
    x_accel: np.ndarray,
    z_accel: np.ndarray,
    static_gyro: np.ndarray,
    x_gyro: np.ndarray,
    z_gyro: np.ndarray,
    max_fit_gyro_deg_s: float = 5.0,
) -> dict[str, Any]:
    x_gyro_norm = np.linalg.norm(x_gyro, axis=1)
    z_gyro_norm = np.linalg.norm(z_gyro, axis=1)
    x_fit_mask = x_gyro_norm <= max_fit_gyro_deg_s
    z_fit_mask = z_gyro_norm <= max_fit_gyro_deg_s
    if int(np.count_nonzero(x_fit_mask)) < 100 or int(np.count_nonzero(z_fit_mask)) < 100:
        raise ValueError("低角速度平移样本不足；平移时请减少刚体旋转")

    x_fit = principal_direction(x_accel[x_fit_mask], [1.0, 0.0, 0.0])
    z_fit = principal_direction(z_accel[z_fit_mask], [0.0, 0.0, 1.0])
    x_axis = x_fit["direction"]
    z_raw = z_fit["direction"]
    axes_angle = angle_deg(x_axis, z_raw)

    motion_specific_force = unit(np.cross(x_axis, z_raw))
    static_specific_force = unit(np.mean(static_accel, axis=0))
    if float(np.dot(motion_specific_force, static_specific_force)) < 0.0:
        motion_specific_force = -motion_specific_force

    # 手推方向不可能精确代表IMU轴，不能用它重新定义水平航向。这里只从运动平面
    # 估计固定倾角，在保留已确认轴向/航向的前提下，把平面法向最小旋转到世界-Z。
    nominal_world_normal = R_LEVEL_FROM_IMU_NOMINAL @ motion_specific_force
    tilt_correction = rotation_align_vectors(nominal_world_normal, [0.0, 0.0, -1.0])
    rotation = tilt_correction @ R_LEVEL_FROM_IMU_NOMINAL
    return {
        "rotation_level_from_imu": rotation,
        "x_direction_imu": x_axis,
        "z_direction_imu": z_raw,
        "static_specific_force_imu": static_specific_force,
        "motion_specific_force_imu": motion_specific_force,
        "axes_angle_deg": axes_angle,
        "vertical_disagreement_deg": angle_deg(static_specific_force, motion_specific_force),
        "x_principal_ratio": x_fit["principal_ratio"],
        "z_principal_ratio": z_fit["principal_ratio"],
        "x_motion_rms_g": x_fit["motion_rms_g"],
        "z_motion_rms_g": z_fit["motion_rms_g"],
        "static_accel_mean_g": np.mean(static_accel, axis=0),
        "static_accel_std_g": np.std(static_accel, axis=0),
        "static_accel_norm_g": float(np.linalg.norm(np.mean(static_accel, axis=0))),
        "static_gyro_std_deg_s": np.std(static_gyro, axis=0),
        "fit_gyro_threshold_deg_s": float(max_fit_gyro_deg_s),
        "x_gyro_gated_fraction": float(np.mean(x_fit_mask)),
        "z_gyro_gated_fraction": float(np.mean(z_fit_mask)),
        "x_gyro_norm_p95_deg_s": float(np.percentile(x_gyro_norm, 95.0)),
        "z_gyro_norm_p95_deg_s": float(np.percentile(z_gyro_norm, 95.0)),
        "tilt_correction_deg": rotation_angle_deg(tilt_correction),
        "difference_from_nominal_deg": rotation_angle_deg(rotation @ R_LEVEL_FROM_IMU_NOMINAL.T),
        "difference_from_current_pipeline_deg": rotation_angle_deg(
            rotation @ R_LEVEL_FROM_IMU_CURRENT_PIPELINE.T
        ),
    }


def segment_metrics(rows: list[Row]) -> dict[str, float | int]:
    timestamps = np.asarray([row.timestamp_s for row in rows], dtype=float)
    duration = float(timestamps[-1] - timestamps[0]) if len(rows) >= 2 else 0.0
    rate = float((len(rows) - 1) / duration) if duration > 0.0 else 0.0
    return {"samples": len(rows), "duration_s": duration, "rate_hz": rate}


def assess_quality(
    result: dict[str, Any],
    metrics: dict[str, dict[str, float | int]],
    reader_stats: dict[str, Any],
    thresholds: Thresholds,
) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}

    def add(name: str, value: float | int, limit: float | int, relation: str) -> None:
        passed = value >= limit if relation == ">=" else value <= limit
        checks[name] = {
            "value": float(value), "limit": float(limit), "relation": relation, "pass": bool(passed)
        }

    add("static_duration_s", metrics["static"]["duration_s"], thresholds.min_static_s, ">=")
    add("x_duration_s", metrics["x_motion"]["duration_s"], thresholds.min_motion_s, ">=")
    add("z_duration_s", metrics["z_motion"]["duration_s"], thresholds.min_motion_s, ">=")
    add("static_samples", metrics["static"]["samples"], thresholds.min_static_samples, ">=")
    add("x_samples", metrics["x_motion"]["samples"], thresholds.min_motion_samples, ">=")
    add("z_samples", metrics["z_motion"]["samples"], thresholds.min_motion_samples, ">=")
    for name, item in metrics.items():
        add(f"{name}_rate_min_hz", item["rate_hz"], thresholds.min_rate_hz, ">=")
        add(f"{name}_rate_max_hz", item["rate_hz"], thresholds.max_rate_hz, "<=")
    add("static_accel_std_g", np.max(result["static_accel_std_g"]), thresholds.max_static_accel_std_g, "<=")
    add("static_gyro_std_deg_s", np.max(result["static_gyro_std_deg_s"]), thresholds.max_static_gyro_std_deg_s, "<=")
    add("static_accel_norm_min_g", result["static_accel_norm_g"], thresholds.min_static_accel_norm_g, ">=")
    add("static_accel_norm_max_g", result["static_accel_norm_g"], thresholds.max_static_accel_norm_g, "<=")
    add("x_motion_rms_g", result["x_motion_rms_g"], thresholds.min_motion_rms_g, ">=")
    add("z_motion_rms_g", result["z_motion_rms_g"], thresholds.min_motion_rms_g, ">=")
    add("x_principal_ratio", result["x_principal_ratio"], thresholds.min_principal_ratio, ">=")
    add("z_principal_ratio", result["z_principal_ratio"], thresholds.min_principal_ratio, ">=")
    add("axes_angle_min_deg", result["axes_angle_deg"], thresholds.min_axes_angle_deg, ">=")
    add("axes_angle_max_deg", result["axes_angle_deg"], thresholds.max_axes_angle_deg, "<=")
    add("x_gyro_gated_fraction", result["x_gyro_gated_fraction"], thresholds.min_gyro_gated_fraction, ">=")
    add("z_gyro_gated_fraction", result["z_gyro_gated_fraction"], thresholds.min_gyro_gated_fraction, ">=")
    add("vertical_disagreement_deg", result["vertical_disagreement_deg"], thresholds.max_vertical_disagreement_deg, "<=")
    add("counter_drop_events", reader_stats.get("dropped_frames", 0), thresholds.max_counter_drop_events, "<=")
    add("counter_resets", reader_stats.get("counter_resets", 0), thresholds.max_counter_resets, "<=")
    add("serial_errors", reader_stats.get("serial_errors", 0), thresholds.max_serial_errors, "<=")
    failed = [name for name, check in checks.items() if not check["pass"]]
    return {"result": "PASS" if not failed else "FAIL", "checks": checks, "failed_checks": failed}


def stats_delta(after: dict[str, Any], before: dict[str, Any]) -> dict[str, Any]:
    cumulative = {
        "frames_ok", "frames_bad", "resyncs", "dropped_frames", "counter_resets",
        "counter_stalls", "serial_errors", "serial_reconnects",
    }
    return {
        key: int(value) - int(before.get(key, 0)) if key in cumulative else value
        for key, value in after.items()
    }


def rows_to_arrays(rows: list[Row]) -> dict[str, np.ndarray]:
    return {
        "timestamp_s": np.asarray([row.timestamp_s for row in rows], dtype=float),
        "receiver_timestamp_s": np.asarray([row.receiver_timestamp_s for row in rows], dtype=float),
        "counter": np.asarray([row.counter for row in rows], dtype=np.uint32),
        "gyro_deg_s": np.asarray([row.gyro_deg_s for row in rows], dtype=float),
        "accel_g": np.asarray([row.accel_g for row in rows], dtype=float),
        "temperature_c": np.asarray([row.temperature_c for row in rows], dtype=float),
    }


def arrays_to_rows(archive: Any, prefix: str) -> list[Row]:
    required = (
        "timestamp_s", "receiver_timestamp_s", "counter", "gyro_deg_s", "accel_g", "temperature_c"
    )
    arrays = {name: np.asarray(archive[f"{prefix}_{name}"]) for name in required}
    lengths = {len(value) for value in arrays.values()}
    if len(lengths) != 1:
        raise ValueError(f"{prefix}原始数组长度不一致")
    return [
        Row(
            float(arrays["timestamp_s"][index]),
            float(arrays["receiver_timestamp_s"][index]),
            int(arrays["counter"][index]),
            tuple(float(value) for value in arrays["gyro_deg_s"][index]),
            tuple(float(value) for value in arrays["accel_g"][index]),
            float(arrays["temperature_c"][index]),
        )
        for index in range(next(iter(lengths), 0))
    ]


def load_saved_segments(path: Path) -> tuple[dict[str, list[Row]], dict[str, Any]]:
    with np.load(path) as archive:
        segments = {
            name: arrays_to_rows(archive, name) for name in ("static", "x_motion", "z_motion")
        }
    reader_stats: dict[str, Any] = {}
    report_path = path.with_name("imu_planar_axis_calibration.yaml")
    if report_path.is_file():
        document = yaml.safe_load(report_path.read_text(encoding="utf-8")) or {}
        reader_stats = dict(document.get("reader_formal_window", {}))
    return segments, reader_stats


class Capture:
    def __init__(self, port: str, baud: int):
        self.rows: list[Row] = []
        self.lock = threading.Lock()
        self.reader = ImuReader(
            port=port, baud=baud, on_sample=self.on_sample,
            name="imu-planar-axis-calibration", warmup_frames=500
        )

    def on_sample(self, sample: Any) -> None:
        row = Row(
            float(sample.ts), float(sample.rx_time), int(sample.counter),
            (float(sample.gx), float(sample.gy), float(sample.gz)),
            (float(sample.ax), float(sample.ay), float(sample.az)), float(sample.temp)
        )
        with self.lock:
            self.rows.append(row)

    def snapshot(self) -> list[Row]:
        with self.lock:
            return list(self.rows)

    def capture_timed(self, duration_s: float, label: str) -> list[Row]:
        start = len(self.snapshot())
        started = time.monotonic()
        last_remaining = -1
        while time.monotonic() - started < duration_s:
            remaining = int(math.ceil(duration_s - (time.monotonic() - started)))
            if remaining != last_remaining:
                print(f"{label}：还剩 {remaining:2d} 秒", end="\r", flush=True)
                last_remaining = remaining
            time.sleep(0.02)
        print(" " * 60, end="\r")
        return self.snapshot()[start:]

    def capture_manual(self, label: str, instruction: str) -> list[Row]:
        print("\n" + instruction)
        input(f"准备好后按回车开始录制{label}……")
        start = len(self.snapshot())
        print(f"正在录制{label}；持续往复运动至少8秒，完成并放稳后按回车结束。")
        input()
        return self.snapshot()[start:]

    def run(self) -> tuple[dict[str, list[Row]], dict[str, Any]]:
        if not self.reader.start():
            raise RuntimeError("无法打开IMU串口；请先停止其他IMU采集程序")
        try:
            print("正在预热IMU……")
            deadline = time.monotonic() + 8.0
            while len(self.snapshot()) < 400 and time.monotonic() < deadline:
                time.sleep(0.02)
            if len(self.snapshot()) < 300:
                raise RuntimeError("8秒内未收到足够IMU数据")
            print("\n=== IMU水平平移三轴标定 ===")
            print("只读取外置IMU，不启动相机、ROS或VINS。")
            print("刚体始终贴水平桌面，保持姿态不变，禁止抬起或旋转。")
            input("按实际安装姿态放稳，按回车采集5秒静止基准……")
            before = self.reader.stats()
            static_rows = self.capture_timed(5.2, "静止基准")
            x_rows = self.capture_manual(
                "水平X轴",
                "沿已确认的IMU X轴前后往复5～20cm；反复加速、减速、停住，姿态不转。",
            )
            z_rows = self.capture_manual(
                "水平Z轴",
                "沿已确认的IMU Z轴左右往复5～20cm；反复加速、减速、停住，姿态不转。",
            )
            return {"static": static_rows, "x_motion": x_rows, "z_motion": z_rows}, stats_delta(
                self.reader.stats(), before
            )
        finally:
            self.reader.stop()


def load_intrinsic(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    return (
        np.asarray(document["accelerometer"]["matrix"], dtype=float),
        np.asarray(document["accelerometer"]["offset_g"], dtype=float),
        np.asarray(document["gyroscope"]["bias_deg_s"], dtype=float),
    )


def calibrated_arrays(
    rows: list[Row], matrix: np.ndarray, offset: np.ndarray, gyro_bias: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    raw = rows_to_arrays(rows)
    return (matrix @ raw["accel_g"].T).T + offset, raw["gyro_deg_s"] - gyro_bias


def plain_vector(vector: Sequence[float]) -> list[float]:
    return [float(value) for value in np.asarray(vector)]


def plain_matrix(matrix: np.ndarray) -> list[list[float]]:
    return [[float(value) for value in row] for row in np.asarray(matrix)]


def save_result(
    output_dir: Path, segments: dict[str, list[Row]], result: dict[str, Any],
    metrics: dict[str, dict[str, float | int]], quality: dict[str, Any],
    thresholds: Thresholds, reader_stats: dict[str, Any], config_path: Path, intrinsic_path: Path
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=False)
    arrays: dict[str, np.ndarray] = {}
    for name, rows in segments.items():
        arrays.update({f"{name}_{key}": value for key, value in rows_to_arrays(rows).items()})
    raw_path = output_dir / "raw_imu_planar_axes.npz"
    np.savez_compressed(raw_path, **arrays)
    rotation = result["rotation_level_from_imu"]
    document = {
        "format_version": 1,
        "calibration_id": output_dir.name,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "type": "imu_planar_motion_axis_alignment",
        "result": quality["result"],
        "known_constraints": [
            "rig attitude fixed", "true vertical acceleration is zero",
            "labelled X and Z translations span the horizontal plane; hand-pushed paths need not be exact axes",
        ],
        "source": {
            "device_config": str(config_path.resolve()),
            "imu_intrinsic": str(intrinsic_path.resolve()),
            "raw_data": str(raw_path.resolve()),
        },
        "application": {
            "accelerometer": "a_level = R_level_from_imu @ (M_accel @ a_raw + offset_g)",
            "gyroscope": "w_level = R_level_from_imu @ (w_raw - gyro_bias)",
            "world_z": "up; static specific force points toward -Z",
        },
        "R_level_from_imu": plain_matrix(rotation),
        "R_level_from_imu_opencv": {
            "rows": 3, "cols": 3, "dt": "d",
            "data": [float(value) for value in rotation.reshape(-1)],
        },
        "q_level_from_imu_xyzw": matrix_to_quaternion_xyzw(rotation),
        "measurement": {
            "segments": metrics,
            "x_direction_imu": plain_vector(result["x_direction_imu"]),
            "z_direction_imu": plain_vector(result["z_direction_imu"]),
            "static_specific_force_imu": plain_vector(result["static_specific_force_imu"]),
            "motion_specific_force_imu": plain_vector(result["motion_specific_force_imu"]),
            "axes_angle_deg": result["axes_angle_deg"],
            "vertical_disagreement_deg": result["vertical_disagreement_deg"],
            "x_principal_ratio": result["x_principal_ratio"],
            "z_principal_ratio": result["z_principal_ratio"],
            "x_motion_rms_g": result["x_motion_rms_g"],
            "z_motion_rms_g": result["z_motion_rms_g"],
            "fit_gyro_threshold_deg_s": result["fit_gyro_threshold_deg_s"],
            "x_gyro_gated_fraction": result["x_gyro_gated_fraction"],
            "z_gyro_gated_fraction": result["z_gyro_gated_fraction"],
            "x_gyro_norm_p95_deg_s": result["x_gyro_norm_p95_deg_s"],
            "z_gyro_norm_p95_deg_s": result["z_gyro_norm_p95_deg_s"],
            "tilt_correction_deg": result["tilt_correction_deg"],
            "difference_from_nominal_deg": result["difference_from_nominal_deg"],
            "difference_from_current_pipeline_deg": result["difference_from_current_pipeline_deg"],
        },
        "reader_formal_window": reader_stats,
        "quality_thresholds": asdict(thresholds),
        "quality": quality,
        "activation": {
            "automatically_enabled": False,
            "reason": "通过后与相机-IMU外参的约90度轴变换成对组合，再用水平和真实升降数据A/B。",
        },
    }
    path = output_dir / "imu_planar_axis_calibration.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def self_test() -> None:
    rng = np.random.default_rng(20260816)
    ax, ay = math.radians(2.0), math.radians(-1.5)
    rx = np.array([[1, 0, 0], [0, math.cos(ax), -math.sin(ax)], [0, math.sin(ax), math.cos(ax)]])
    ry = np.array([[math.cos(ay), 0, math.sin(ay)], [0, 1, 0], [-math.sin(ay), 0, math.cos(ay)]])
    expected = rx @ ry @ R_LEVEL_FROM_IMU_NOMINAL
    x_sensor = expected.T @ [1.0, 0.0, 0.0]
    z_sensor = expected.T @ [0.0, -1.0, 0.0]
    gravity = expected.T @ [0.0, 0.0, -1.0]
    count = 5000
    excitation = 0.08 * np.sin(np.linspace(0.0, 30.0 * math.pi, count))
    static = np.tile(gravity, (count, 1)) + rng.normal(0.0, 0.0007, (count, 3))
    gyro = rng.normal(0.0, 0.05, (count, 3))
    result = solve_planar_axes(
        static, static + excitation[:, None] * x_sensor,
        static + excitation[:, None] * z_sensor, gyro, gyro, gyro
    )
    np.testing.assert_allclose(result["rotation_level_from_imu"], expected, atol=5e-3)
    assert abs(np.linalg.det(result["rotation_level_from_imu"]) - 1.0) < 1e-12
    print("SELF_TEST_OK: two-axis planar excitation, vertical recovery, proper rotation")


def main() -> int:
    parser = argparse.ArgumentParser(description="纯IMU水平X/Z平移三轴标定")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--intrinsic", type=Path, default=DEFAULT_INTRINSIC)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--input-npz", type=Path, help="用已保存的原始NPZ重新解算，不读取硬件")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not args.config.is_file() or not args.intrinsic.is_file():
        parser.error("设备配置或IMU内参文件不存在")
    if args.input_npz:
        if not args.input_npz.is_file():
            parser.error(f"原始NPZ不存在: {args.input_npz}")
        print(f"正在重新解算已保存数据: {args.input_npz.resolve()}")
        segments, reader_stats = load_saved_segments(args.input_npz)
    else:
        unit = load_config(str(args.config)).units[0]
        try:
            segments, reader_stats = Capture(unit.imu.port, unit.imu.baud).run()
        except KeyboardInterrupt:
            print("\n标定已取消；本次未生成标定文件。")
            return 130
    matrix, offset, gyro_bias = load_intrinsic(args.intrinsic)
    calibrated = {
        name: calibrated_arrays(rows, matrix, offset, gyro_bias) for name, rows in segments.items()
    }
    result = solve_planar_axes(
        calibrated["static"][0], calibrated["x_motion"][0], calibrated["z_motion"][0],
        calibrated["static"][1], calibrated["x_motion"][1], calibrated["z_motion"][1]
    )
    metrics = {name: segment_metrics(rows) for name, rows in segments.items()}
    thresholds = Thresholds()
    quality = assess_quality(result, metrics, reader_stats, thresholds)
    output_dir = args.output_root / f"imu_planar_axes_{datetime.now():%Y%m%d_%H%M%S}"
    path = save_result(
        output_dir, segments, result, metrics, quality, thresholds, reader_stats,
        args.config, args.intrinsic
    )
    print("\n=== IMU水平平移标定结果 ===")
    print(f"结果: {quality['result']}")
    print(f"X/Z轴夹角: {result['axes_angle_deg']:.3f}°")
    print(f"静止重力与运动平面法向差: {result['vertical_disagreement_deg']:.3f}°")
    print(f"X/Z激励RMS: {result['x_motion_rms_g']:.4f}/{result['z_motion_rms_g']:.4f} g")
    print(
        "低角速度有效样本: "
        f"X {100.0 * result['x_gyro_gated_fraction']:.1f}% / "
        f"Z {100.0 * result['z_gyro_gated_fraction']:.1f}%"
    )
    print(f"固定水平倾角修正: {result['tilt_correction_deg']:.4f}°")
    print(f"与当前VINS固定旋转差异: {result['difference_from_current_pipeline_deg']:.4f}°")
    print(f"标定文件: {path.resolve()}")
    if quality["result"] != "PASS":
        print("未通过: " + ", ".join(quality["failed_checks"]))
        print("本次数据保留诊断，但不会自动启用。")
        return 2
    print("标定通过；下一步与相机-IMU外参成对组合并做水平/升降A/B。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
