#!/usr/bin/env python3
"""中文引导的 IMU 本体与已知运动标定。

流程：
1. 六面静止：加速度计零偏、比例和轴间映射；
2. 三轴正反 90°：陀螺比例和轴间映射；
3. 三轴正反 5/8 cm：比较裸双积分与 ZUPT/区间漂移校正，并拟合经验位移映射。

已知距离试验是带人工外部真值的系统辨识，不等同于无外部参考的纯 IMU
绝对位置标定。每一帧的拟合时间戳、原始接收时间、硬件计数器和传感器值
都会保存，失败或 Ctrl+C 中止也会保留原始数据。
"""

from __future__ import annotations

import argparse
import math
import os
import signal
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import yaml
from PIL import Image, ImageDraw, ImageFont


PROJECT = Path("/home/robot/ego_vio_humble")
sys.path.insert(0, str(PROJECT))

from ego_vio.config import load_config
from ego_vio.imu.imu_reader import ImuReader


GRAVITY = 9.80665
AXES = "XYZ"


@dataclass(frozen=True)
class Row:
    ts: float
    rx_time: float
    counter: int
    gyro: np.ndarray
    accel: np.ndarray
    temp: float


def classify_face(accel_mean: np.ndarray) -> tuple[int, int] | None:
    axis = int(np.argmax(np.abs(accel_mean)))
    sign = 1 if accel_mean[axis] >= 0.0 else -1
    if abs(accel_mean[axis]) < 0.85:
        return None
    if float(np.max(np.delete(np.abs(accel_mean), axis))) > 0.35:
        return None
    return axis, sign


def face_name(face: tuple[int, int] | None) -> str:
    if face is None:
        return "姿态倾斜过大"
    axis, sign = face
    return f"{'+' if sign > 0 else '-'}{AXES[axis]} 朝上"


def six_face_sequence(initial: tuple[int, int]) -> list[tuple[int, int]]:
    """生成连续姿态均相差 90°的六面顺序。"""
    axis0, sign0 = initial
    other = [axis for axis in range(3) if axis != axis0]
    return [
        initial,
        (other[0], 1),
        (other[1], 1),
        (other[0], -1),
        (other[1], -1),
        (axis0, -sign0),
    ]


def integrate_gyro(ts: np.ndarray, gyro_deg_s: np.ndarray, bias_deg_s: np.ndarray) -> np.ndarray:
    if len(ts) < 2:
        return np.zeros(3)
    dt = np.diff(ts)
    omega = gyro_deg_s - bias_deg_s
    return np.sum(0.5 * (omega[:-1] + omega[1:]) * dt[:, None], axis=0)


def quaternion_multiply(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """xyzw 四元数乘法。"""
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    return np.asarray(
        [
            lw * rx + lx * rw + ly * rz - lz * ry,
            lw * ry - lx * rz + ly * rw + lz * rx,
            lw * rz + lx * ry - ly * rx + lz * rw,
            lw * rw - lx * rx - ly * ry - lz * rz,
        ]
    )


def quaternion_rotate(vector: np.ndarray, quaternion: np.ndarray) -> np.ndarray:
    """用 xyzw 四元数把机体向量旋转到起始参考系。"""
    xyz = quaternion[:3]
    uv = np.cross(xyz, vector)
    uuv = np.cross(xyz, uv)
    return vector + 2.0 * (quaternion[3] * uv + uuv)


def attitude_compensated_acceleration(
    ts: np.ndarray,
    accel_g: np.ndarray,
    gyro_deg_s: np.ndarray,
    baseline_g: np.ndarray,
    gyro_bias_deg_s: np.ndarray,
) -> np.ndarray:
    """用短时陀螺姿态将加速度旋回起始系，再扣除起始重力。"""
    count = len(ts)
    linear_g = np.zeros((count, 3))
    quaternion = np.asarray([0.0, 0.0, 0.0, 1.0])
    angular_rate = np.radians(gyro_deg_s - gyro_bias_deg_s)
    for index in range(count):
        if index > 0:
            dt = float(ts[index] - ts[index - 1])
            omega = 0.5 * (angular_rate[index - 1] + angular_rate[index])
            angle = float(np.linalg.norm(omega) * dt)
            if angle > 0.0:
                axis = omega / np.linalg.norm(omega)
                half = 0.5 * angle
                delta = np.asarray(
                    [axis[0] * math.sin(half), axis[1] * math.sin(half), axis[2] * math.sin(half), math.cos(half)]
                )
                quaternion = quaternion_multiply(quaternion, delta)
                quaternion /= np.linalg.norm(quaternion)
        linear_g[index] = quaternion_rotate(accel_g[index], quaternion) - baseline_g
    return linear_g


def integrate_translation(
    ts: np.ndarray,
    accel_g: np.ndarray,
    baseline_g: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """返回裸速度/位置和终点零速度漂移校正后的速度/位置。"""
    count = len(ts)
    raw_velocity = np.zeros((count, 3))
    raw_position = np.zeros((count, 3))
    if count < 2:
        return raw_velocity, raw_position, raw_velocity.copy(), raw_position.copy()

    acceleration = (accel_g - baseline_g) * GRAVITY
    for index in range(1, count):
        dt = float(ts[index] - ts[index - 1])
        raw_velocity[index] = raw_velocity[index - 1] + 0.5 * (
            acceleration[index - 1] + acceleration[index]
        ) * dt
        raw_position[index] = raw_position[index - 1] + 0.5 * (
            raw_velocity[index - 1] + raw_velocity[index]
        ) * dt

    elapsed = ts - ts[0]
    duration = max(float(elapsed[-1]), 1e-9)
    corrected_velocity = raw_velocity - (elapsed / duration)[:, None] * raw_velocity[-1]
    corrected_position = np.zeros((count, 3))
    for index in range(1, count):
        dt = float(ts[index] - ts[index - 1])
        corrected_position[index] = corrected_position[index - 1] + 0.5 * (
            corrected_velocity[index - 1] + corrected_velocity[index]
        ) * dt
    return raw_velocity, raw_position, corrected_velocity, corrected_position


def integrate_translation_attitude_compensated(
    ts: np.ndarray,
    accel_g: np.ndarray,
    gyro_deg_s: np.ndarray,
    baseline_g: np.ndarray,
    gyro_bias_deg_s: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    linear_g = attitude_compensated_acceleration(
        ts, accel_g, gyro_deg_s, baseline_g, gyro_bias_deg_s
    )
    return integrate_translation(ts, linear_g, np.zeros(3))


def fit_linear_map(measured: np.ndarray, expected: np.ndarray) -> tuple[np.ndarray, float]:
    """拟合列向量形式 expected = matrix @ measured。"""
    coefficients, _, _, _ = np.linalg.lstsq(measured, expected, rcond=None)
    predicted = measured @ coefficients
    rmse = float(np.sqrt(np.mean(np.square(predicted - expected))))
    return coefficients.T, rmse


class GuidedCalibration:
    def __init__(
        self,
        port: str,
        baud: int,
        output: Path,
        distances_cm: list[float],
        distance_only: bool = False,
        auto_delay_s: float = 0.0,
        axes: str = "XYZ",
        capture_window_s: float = 4.0,
    ):
        self.port = port
        self.baud = baud
        self.output = output
        self.distances_m = [value / 100.0 for value in distances_cm]
        self.distance_only = distance_only
        self.auto_delay_s = max(0.0, auto_delay_s)
        self.translation_axes = [AXES.index(axis) for axis in axes]
        self.capture_window_s = max(1.0, capture_window_s)
        self.lock = threading.Lock()
        self.samples: deque[Row] = deque(maxlen=8000)
        self.all_rows: list[Row] = []
        self.faces: dict[tuple[int, int], dict] = {}
        self.rotation_trials: list[dict] = []
        self.translation_trials: list[dict] = []
        self.live_raw_position = np.zeros(3)
        self.live_corrected_position = np.zeros(3)
        self.live_angle = np.zeros(3)
        self.viz_stop = threading.Event()
        self.gate_event = threading.Event()
        self.waiting_for_start = False
        self.first_sample_ts: float | None = None
        self.status_path = self.output.parent / "current_imu_calibration_status.yaml"
        self.status_font = ImageFont.truetype(
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 30, index=2
        )
        self.status_small_font = ImageFont.truetype(
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 20, index=2
        )
        self.reader = ImuReader(port=port, baud=baud, on_sample=self.on_sample, name="imu-known-motion")
        signal.signal(signal.SIGUSR1, self.on_start_signal)

        import rerun as rr
        import rerun.blueprint as rrb

        self.rr = rr
        rr.init("ego_vio_imu_known_motion_calibration", spawn=False)
        rr.connect_grpc("rerun+http://127.0.0.1:9876/proxy")
        rr.send_blueprint(
            rrb.Blueprint(
                rrb.Horizontal(
                    rrb.Spatial3DView(origin="trajectory", name="IMU Trajectory (m)"),
                    rrb.Vertical(
                        rrb.Spatial2DView(origin="ui/status", name="Chinese Status"),
                        rrb.TimeSeriesView(origin="raw/accel", name="Raw Accel (g)"),
                        rrb.TimeSeriesView(origin="raw/gyro", name="Raw Gyro (deg/s)"),
                    ),
                    rrb.Vertical(
                        rrb.TimeSeriesView(origin="integral/direct", name="Direct Integral (m)"),
                        rrb.TimeSeriesView(origin="integral/zupt", name="ZUPT Corrected (m)"),
                        rrb.TimeSeriesView(origin="integral/angle", name="Integrated Angle (deg)"),
                    ),
                    column_shares=[0.36, 0.34, 0.30],
                ),
                rrb.TimePanel(timeline="calib_time", state="collapsed"),
                collapse_panels=True,
            )
        )
        self.viz_thread = threading.Thread(target=self.viz_loop, name="imu-calib-viz", daemon=True)
        self.viz_thread.start()

    def on_sample(self, sample) -> None:
        row = Row(
            ts=float(sample.ts),
            rx_time=float(sample.rx_time),
            counter=int(sample.counter),
            gyro=np.asarray([sample.gx, sample.gy, sample.gz], dtype=float),
            accel=np.asarray([sample.ax, sample.ay, sample.az], dtype=float),
            temp=float(sample.temp),
        )
        with self.lock:
            if self.first_sample_ts is None:
                self.first_sample_ts = row.ts
            self.samples.append(row)
            self.all_rows.append(row)

    def viz_loop(self) -> None:
        last_timestamp = None
        while not self.viz_stop.wait(0.05):
            with self.lock:
                row = self.samples[-1] if self.samples else None
                raw_position = self.live_raw_position.copy()
                corrected_position = self.live_corrected_position.copy()
                angle = self.live_angle.copy()
            if row is None or row.ts == last_timestamp:
                continue
            last_timestamp = row.ts
            first_sample_ts = self.first_sample_ts if self.first_sample_ts is not None else row.ts
            self.rr.set_time("calib_time", duration=max(0.0, row.ts - first_sample_ts))
            for axis, value in zip("xyz", row.accel):
                self.rr.log(f"raw/accel/{axis}", self.rr.Scalars([float(value)]))
            for axis, value in zip("xyz", row.gyro):
                self.rr.log(f"raw/gyro/{axis}", self.rr.Scalars([float(value)]))
            for axis, value in zip("xyz", raw_position):
                self.rr.log(f"integral/direct/{axis}", self.rr.Scalars([float(value)]))
            for axis, value in zip("xyz", corrected_position):
                self.rr.log(f"integral/zupt/{axis}", self.rr.Scalars([float(value)]))
            for axis, value in zip("xyz", angle):
                self.rr.log(f"integral/angle/{axis}", self.rr.Scalars([float(value)]))
            self.rr.log(
                "trajectory/current",
                self.rr.Points3D([corrected_position.tolist()], colors=[[40, 220, 100]], radii=[0.006]),
            )

    def render_status_card(self, message: str) -> np.ndarray:
        width, height = 1100, 230
        image = Image.new("RGB", (width, height), (18, 22, 28))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, width, 56), fill=(24, 94, 160))
        draw.text((24, 9), "IMU 标定实时提示", font=self.status_font, fill=(255, 255, 255))
        lines = []
        current = ""
        for char in message:
            candidate = current + char
            if draw.textlength(candidate, font=self.status_font) > width - 48:
                lines.append(current)
                current = char
            else:
                current = candidate
        if current:
            lines.append(current)
        for index, line in enumerate(lines[:4]):
            draw.text((24, 72 + index * 38), line, font=self.status_font, fill=(238, 242, 247))
        local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        draw.text(
            (width - 250, height - 30),
            local_time,
            font=self.status_small_font,
            fill=(150, 170, 190),
        )
        return np.asarray(image)

    def status(self, message: str) -> None:
        print(f"\n>>> {message}", flush=True)
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        self.status_path.write_text(
            yaml.safe_dump(
                {
                    "pid": os.getpid(),
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                    "waiting_for_start": self.waiting_for_start,
                    "message": message,
                    "output": str(self.output),
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        with self.lock:
            latest_ts = self.samples[-1].ts if self.samples else None
            first_ts = self.first_sample_ts
        duration = latest_ts - first_ts if latest_ts is not None and first_ts is not None else 0.0
        self.rr.set_time("calib_time", duration=max(0.0, duration))
        self.rr.log("ui/status/card", self.rr.Image(self.render_status_card(message)))

    def on_start_signal(self, _signum, _frame) -> None:
        """只在门控等待期间接受 SIGUSR1，避免提前信号污染下一步。"""
        if self.waiting_for_start:
            self.gate_event.set()

    def operator_gate(self, message: str) -> None:
        """由操作者明确确认起点，避免聊天延迟造成自动流程错位。"""
        if self.auto_delay_s > 0.0:
            deadline = time.monotonic() + self.auto_delay_s
            last_second = None
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    break
                second = max(1, math.ceil(remaining))
                if second != last_second:
                    self.status(f"{message}；{second} 秒后自动开始")
                    last_second = second
                time.sleep(min(0.1, remaining))
            self.status("间隔结束；即将进行静止确认和采样倒计时")
            return
        self.gate_event.clear()
        self.waiting_for_start = True
        self.status(message + "；程序已暂停，等待开始命令")
        self.gate_event.wait()
        self.waiting_for_start = False
        self.status("已收到开始命令；即将进行静止确认和倒计时")

    def rows(self) -> list[Row]:
        with self.lock:
            return list(self.samples)

    def tail(self, count: int) -> list[Row]:
        rows = self.rows()
        return rows[-count:]

    @staticmethod
    def stable(rows: list[Row], gyro_std_limit: float = 0.30) -> tuple[bool, dict]:
        if len(rows) < 300:
            return False, {"reason": "样本不足"}
        gyro = np.asarray([row.gyro for row in rows])
        accel = np.asarray([row.accel for row in rows])
        metrics = {
            "gyro_std": float(np.max(np.std(gyro, axis=0))),
            "gyro_mean_norm": float(np.mean(np.linalg.norm(gyro, axis=1))),
            "accel_std": float(np.max(np.std(accel, axis=0))),
        }
        passed = (
            metrics["gyro_std"] < gyro_std_limit
            and metrics["gyro_mean_norm"] < 1.0
            and metrics["accel_std"] < 0.006
        )
        return passed, metrics

    def wait_stable(
        self,
        label: str,
        timeout: float = 30.0,
        gyro_std_limit: float = 0.30,
    ) -> list[Row]:
        deadline = time.monotonic() + timeout
        last_report = 0.0
        while time.monotonic() < deadline:
            rows = self.tail(400)
            passed, metrics = self.stable(rows, gyro_std_limit=gyro_std_limit)
            if passed and len(rows) >= 400:
                return rows
            now = time.monotonic()
            if now - last_report >= 1.0:
                self.status(
                    f"{label}：等待放稳；样本 {len(rows)}/400，"
                    f"加速度波动 {metrics.get('accel_std', math.nan):.4f} g，"
                    f"角速度波动 {metrics.get('gyro_std', math.nan):.3f} °/s"
                )
                last_report = now
            time.sleep(0.05)
        raise RuntimeError(f"{label}：30秒内未稳定，请检查线缆、桌面和串口占用")

    def wait_reposition(self, label: str, gyro_std_limit: float = 0.30) -> None:
        """失败后必须先检测到人为返回动作，再允许建立新的静止基线。"""
        baseline_rows = self.wait_stable(
            f"{label}失败后的当前位置", gyro_std_limit=gyro_std_limit
        )
        baseline_accel = np.mean([row.accel for row in baseline_rows], axis=0)
        baseline_gyro = np.mean([row.gyro for row in baseline_rows], axis=0)
        self.status(f"{label}：请先回到本项起始挡位；检测到返回动作后再放稳")
        deadline = time.monotonic() + 30.0
        moved = False
        while time.monotonic() < deadline:
            rows = self.tail(20)
            if rows:
                moved = moved or any(
                    np.linalg.norm(row.accel - baseline_accel) > 0.010
                    or np.linalg.norm(row.gyro - baseline_gyro) > 5.0
                    for row in rows
                )
            if moved:
                self.wait_stable(f"{label}已返回，准备重试", gyro_std_limit=gyro_std_limit)
                return
            time.sleep(0.02)
        raise RuntimeError(f"{label}：30秒内未检测到返回起点动作")

    def collect_initial(self) -> tuple[np.ndarray, np.ndarray, tuple[int, int]]:
        self.status("步骤1/4：保持当前姿态完全静止，先采集零偏并识别第一个面")
        rows = self.wait_stable("初始静止")
        gyro = np.asarray([row.gyro for row in rows])
        accel = np.asarray([row.accel for row in rows])
        face = classify_face(accel.mean(axis=0))
        if face is None:
            raise RuntimeError("当前姿态不是标准六面之一，请让某一根轴尽量竖直")
        self.status(
            f"初始静止通过：{face_name(face)}；陀螺零偏 "
            f"{np.mean(gyro, axis=0).round(5).tolist()} °/s"
        )
        return gyro.mean(axis=0), accel.mean(axis=0), face

    def capture_face(self, target: tuple[int, int]) -> None:
        last_report = 0.0
        while True:
            rows = self.tail(400)
            passed, metrics = self.stable(rows)
            accel = np.asarray([row.accel for row in rows]) if rows else np.empty((0, 3))
            current = classify_face(accel.mean(axis=0)) if len(accel) else None
            if passed and len(rows) >= 400 and current == target:
                gyro = np.asarray([row.gyro for row in rows])
                self.faces[target] = {
                    "accel_mean_g": accel.mean(axis=0).tolist(),
                    "accel_std_g": accel.std(axis=0).tolist(),
                    "gyro_mean_deg_s": gyro.mean(axis=0).tolist(),
                    "gyro_std_deg_s": gyro.std(axis=0).tolist(),
                }
                self.status(f"✓ 已采集 {face_name(target)}（{len(self.faces)}/6）")
                return
            now = time.monotonic()
            if now - last_report >= 1.0:
                self.status(
                    f"目标：{face_name(target)}；当前：{face_name(current)}；"
                    f"加速度波动 {metrics.get('accel_std', math.nan):.4f} g"
                )
                last_report = now
            time.sleep(0.05)

    def collect_six_faces(self, initial_face: tuple[int, int]) -> None:
        self.status("步骤2/4：六面静止。每次按提示翻转90°并放稳")
        sequence = six_face_sequence(initial_face)
        self.capture_face(sequence[0])
        for target in sequence[1:]:
            self.status(f"请翻转 90°，直到 {face_name(target)}，然后放稳")
            self.capture_face(target)

    def collect_rotation(self, axis: int, sign: int, gyro_bias: np.ndarray) -> dict:
        direction = "+90°" if sign > 0 else "−90°（转回）"
        self.wait_stable(f"{AXES[axis]}轴{direction}前")
        self.status(
            f"绕 IMU {AXES[axis]} 轴做{direction}：右手拇指指向 +{AXES[axis]}，"
            "四指弯曲方向为正；转到90°挡位后停住"
        )
        with self.lock:
            self.live_angle = np.zeros(3)
        start_deadline = time.monotonic() + 300.0
        start_index = None
        while time.monotonic() < start_deadline:
            with self.lock:
                if self.all_rows and np.linalg.norm(self.all_rows[-1].gyro - gyro_bias) > 5.0:
                    start_index = max(0, len(self.all_rows) - 20)
                    break
            time.sleep(0.01)
        if start_index is None:
            raise RuntimeError(f"{AXES[axis]}轴{direction}：30秒内未检测到转动")

        quiet_since = None
        last_index = start_index
        while True:
            with self.lock:
                end_index = len(self.all_rows)
                trial_rows = list(self.all_rows[start_index:end_index])
            if len(trial_rows) < 2:
                time.sleep(0.01)
                continue
            ts = np.asarray([row.ts for row in trial_rows])
            gyro = np.asarray([row.gyro for row in trial_rows])
            angle = integrate_gyro(ts, gyro, gyro_bias)
            with self.lock:
                self.live_angle = angle
            if end_index != last_index:
                recent = trial_rows[-200:]
                quiet = len(recent) >= 160 and np.mean(
                    [np.linalg.norm(row.gyro - gyro_bias) for row in recent]
                ) < 0.8
                if quiet and ts[-1] - ts[0] > 0.4:
                    quiet_since = quiet_since or time.monotonic()
                else:
                    quiet_since = None
                last_index = end_index
            if quiet_since is not None and time.monotonic() - quiet_since >= 0.5:
                break
            if ts[-1] - ts[0] > 10.0:
                raise RuntimeError(f"{AXES[axis]}轴{direction}持续超过10秒，试验作废")
            time.sleep(0.01)

        main = float(angle[axis])
        if sign * main < 45.0 or abs(main) > 135.0:
            self.status(f"✗ 测得 {main:.2f}°，方向或角度不正确，重做本项")
            self.wait_reposition(f"{AXES[axis]}轴{direction}")
            return self.collect_rotation(axis, sign, gyro_bias)
        cross = float(np.linalg.norm(np.delete(angle, axis)))
        result = {
            "axis": AXES[axis],
            "sign": sign,
            "expected_deg": 90.0 * sign,
            "measured_vector_deg": angle.tolist(),
            "main_angle_deg": main,
            "cross_axis_deg": cross,
            "duration_s": float(ts[-1] - ts[0]),
        }
        self.status(
            f"✓ {AXES[axis]}轴{direction}：测得 {main:.2f}°，串轴 {cross:.2f}°"
        )
        return result

    def collect_rotations(self, gyro_bias: np.ndarray) -> None:
        self.status("步骤3/4：X/Y/Z 每轴做 +90° 和 −90°，每组回到原姿态")
        for axis in range(3):
            self.rotation_trials.append(self.collect_rotation(axis, 1, gyro_bias))
            self.rotation_trials.append(self.collect_rotation(axis, -1, gyro_bias))

    def collect_translation_fixed(self, axis: int, sign: int, distance_m: float) -> dict:
        """严格定时采集：摆位倒计时后固定窗口录制，不在中途隐式等待。"""
        label = f"{AXES[axis]}轴 {sign * distance_m * 100.0:+.0f} cm"
        baseline_rows = self.tail(400)
        if len(baseline_rows) < 300:
            raise RuntimeError(f"{label}：起始静止样本不足")
        baseline_ok, baseline_metrics = self.stable(baseline_rows, gyro_std_limit=1.0)
        baseline_accel = np.mean([row.accel for row in baseline_rows], axis=0)
        baseline_gyro = np.mean([row.gyro for row in baseline_rows], axis=0)

        with self.lock:
            capture_start = len(self.all_rows)
            self.live_raw_position = np.zeros(3)
            self.live_corrected_position = np.zeros(3)
            self.live_angle = np.zeros(3)
        self.status(
            f"现在开始：沿 IMU {'+' if sign > 0 else '-'}{AXES[axis]} 方向平移 "
            f"{distance_m * 100:.0f} cm；{self.capture_window_s:.0f} 秒固定录制已开始，到位后放稳"
        )
        capture_deadline = time.monotonic() + self.capture_window_s
        while time.monotonic() < capture_deadline:
            with self.lock:
                live_rows = list(self.all_rows[capture_start:])
            if len(live_rows) >= 2:
                live_ts = np.asarray([row.ts for row in live_rows])
                live_accel = np.asarray([row.accel for row in live_rows])
                live_gyro = np.asarray([row.gyro for row in live_rows])
                _, raw_pos, _, corrected_pos = integrate_translation_attitude_compensated(
                    live_ts, live_accel, live_gyro, baseline_accel, baseline_gyro
                )
                angle = integrate_gyro(live_ts, live_gyro, baseline_gyro)
                with self.lock:
                    self.live_raw_position = raw_pos[-1]
                    self.live_corrected_position = corrected_pos[-1]
                    self.live_angle = angle
            time.sleep(0.02)

        with self.lock:
            captured_rows = list(self.all_rows[capture_start:])
        if len(captured_rows) < 2:
            raise RuntimeError(f"{label}：固定窗口内没有 IMU 数据")

        captured_accel = np.asarray([row.accel for row in captured_rows])
        delta = captured_accel - baseline_accel
        motion_mask = (
            (np.abs(delta[:, axis]) > 0.004)
            & (np.linalg.norm(delta, axis=1) > 0.006)
        )
        run = np.convolve(motion_mask.astype(np.int8), np.ones(4, dtype=np.int8), mode="valid")
        onset_candidates = np.flatnonzero(run >= 4)
        problems = []
        if len(onset_candidates):
            first_motion = int(onset_candidates[0])
            last_motion = int(np.flatnonzero(motion_mask)[-1])
            start_offset = max(0, first_motion - 20)
            end_offset = min(len(captured_rows), last_motion + 201)
            trial_rows = captured_rows[start_offset:end_offset]
        else:
            trial_rows = captured_rows
            problems.append("未检测到明确平移")

        ts = np.asarray([row.ts for row in trial_rows])
        accel = np.asarray([row.accel for row in trial_rows])
        gyro = np.asarray([row.gyro for row in trial_rows])
        projection = sign * (accel[:, axis] - baseline_accel[axis])
        positive_phase = bool(np.max(projection) > 0.004)
        braking_phase = bool(np.min(projection) < -0.004)
        _, raw_position, _, corrected_position = integrate_translation_attitude_compensated(
            ts, accel, gyro, baseline_accel, baseline_gyro
        )
        angle = integrate_gyro(ts, gyro, baseline_gyro)

        counters = np.asarray([row.counter for row in trial_rows], dtype=np.int64)
        counter_diff = np.diff(counters)
        counter_resets = (counter_diff < 0) & (counters[1:] == 1)
        counter_drops = (counter_diff != 1) & ~counter_resets
        reset_count = int(np.count_nonzero(counter_resets))
        drop_count = int(np.count_nonzero(counter_drops))
        dt = np.diff(ts)
        angle_norm = float(np.linalg.norm(angle))
        expected = np.zeros(3)
        expected[axis] = sign * distance_m
        measured = corrected_position[-1]
        axial = float(sign * measured[axis])
        cross = float(np.linalg.norm(np.delete(measured, axis)))
        warnings = []
        if not baseline_ok:
            problems.append(
                f"起始未放稳(加速度波动 {baseline_metrics['accel_std']:.4f} g，"
                f"角速度波动 {baseline_metrics['gyro_std']:.2f} °/s)"
            )
        if drop_count:
            problems.append(f"非复位型硬件计数不连续 {drop_count} 次")
        if reset_count:
            warnings.append(f"DIO2/counter 清零 {reset_count} 次，但拟合时间保持连续")
        if float(np.max(dt)) > 0.010:
            problems.append(f"最大时间间隔 {np.max(dt) * 1000:.1f} ms")
        if angle_norm > 3.0:
            problems.append(f"移动中转动 {angle_norm:.2f}°")
        if not positive_phase or not braking_phase:
            problems.append("未清楚检测到加速和减速两个阶段")
        cross_limit = max(0.015, 0.30 * distance_m)
        if cross > cross_limit:
            warnings.append(
                f"横向串扰 {cross * 100:.2f} cm 超过参考值 {cross_limit * 100:.2f} cm，"
                "保留用于三轴耦合矩阵拟合"
            )
        if axial <= 0.003:
            warnings.append("积分方向异常或轴向结果很小")

        result = {
            "axis": AXES[axis],
            "sign": sign,
            "expected_m": expected.tolist(),
            "direct_position_m": raw_position[-1].tolist(),
            "zupt_position_m": measured.tolist(),
            "axial_zupt_m": float(measured[axis]),
            "cross_axis_m": cross,
            "rotation_deg": angle.tolist(),
            "duration_s": float(ts[-1] - ts[0]),
            "counter_resets": reset_count,
            "counter_drops": drop_count,
            "dt_max_ms": float(np.max(dt) * 1000.0),
            "valid": not problems,
            "problems": problems,
            "warnings": warnings,
        }
        color = [40, 220, 100] if not problems else [230, 70, 70]
        self.rr.log(
            f"trajectory/trial_{len(self.translation_trials):02d}",
            self.rr.LineStrips3D([corrected_position.tolist()], colors=[color], radii=[0.002]),
        )
        if problems:
            self.status(
                f"✗ {label}无效：{'；'.join(problems)}；原始数据已保留，继续下一项"
            )
        else:
            self.status(
                f"✓ {label}：裸积分 {raw_position[-1][axis] * 100:+.2f} cm；"
                f"ZUPT校正 {measured[axis] * 100:+.2f} cm；横向串扰 {cross * 100:.2f} cm"
                + (f"；警告：{'；'.join(warnings)}" if warnings else "")
            )
        return result

    def collect_translation(
        self,
        axis: int,
        sign: int,
        distance_m: float,
        attempt: int = 1,
    ) -> dict:
        if self.auto_delay_s > 0.0:
            return self.collect_translation_fixed(axis, sign, distance_m)
        if attempt > 5:
            raise RuntimeError(
                f"{AXES[axis]}轴 {sign*distance_m*100:+.0f} cm 连续5次数据质量不合格，停止并检查动作门槛"
            )
        signed_cm = sign * distance_m * 100.0
        label = f"{AXES[axis]}轴 {signed_cm:+.0f} cm"
        baseline_rows = self.wait_stable(f"{label}开始前", gyro_std_limit=1.0)
        self.status(f"{label}：准备倒计时 2 秒，此时不要移动")
        time.sleep(2.0)
        baseline_rows = self.wait_stable(f"{label}倒计时结束", gyro_std_limit=1.0)
        baseline_accel = np.mean([row.accel for row in baseline_rows], axis=0)
        baseline_gyro = np.mean([row.gyro for row in baseline_rows], axis=0)
        self.status(
            f"现在开始：沿 IMU {'+' if sign > 0 else '-'}{AXES[axis]} 方向平移 {distance_m*100:.0f} cm；"
            "保持姿态不转，在约0.5~1.5秒内一次移动到标记并停住"
        )
        with self.lock:
            self.live_raw_position = np.zeros(3)
            self.live_corrected_position = np.zeros(3)
            self.live_angle = np.zeros(3)

        start_deadline = time.monotonic() + 30.0
        start_index = None
        onset_count = 0
        while time.monotonic() < start_deadline:
            with self.lock:
                if self.all_rows:
                    delta = self.all_rows[-1].accel - baseline_accel
                    if abs(float(delta[axis])) > 0.006 and np.linalg.norm(delta) > 0.008:
                        onset_count += 1
                    else:
                        onset_count = 0
                    if onset_count >= 8:
                        start_index = max(0, len(self.all_rows) - 30)
                        break
            time.sleep(0.005)
        if start_index is None:
            self.status(f"✗ {label}：30 秒内未检测到平移启动，本次不记录")
            self.operator_gate(f"{label}未采到；请回到本项起始挡位并放稳")
            return self.collect_translation(axis, sign, distance_m, attempt + 1)

        quiet_since = None
        last_index = start_index
        positive_phase = False
        braking_phase = False
        while True:
            with self.lock:
                end_index = len(self.all_rows)
                trial_rows = list(self.all_rows[start_index:end_index])
            if len(trial_rows) < 2:
                time.sleep(0.005)
                continue
            ts = np.asarray([row.ts for row in trial_rows])
            accel = np.asarray([row.accel for row in trial_rows])
            gyro = np.asarray([row.gyro for row in trial_rows])
            projection = sign * (accel[:, axis] - baseline_accel[axis])
            positive_phase = positive_phase or bool(np.max(projection) > 0.006)
            braking_phase = braking_phase or bool(np.min(projection) < -0.006)
            _, raw_position, _, corrected_position = integrate_translation(ts, accel, baseline_accel)
            angle = integrate_gyro(ts, gyro, baseline_gyro)
            with self.lock:
                self.live_raw_position = raw_position[-1]
                self.live_corrected_position = corrected_position[-1]
                self.live_angle = angle

            if end_index != last_index:
                recent = trial_rows[-200:]
                recent_accel = np.asarray([row.accel for row in recent])
                recent_gyro = np.asarray([row.gyro for row in recent])
                quiet = (
                    len(recent) >= 160
                    and float(np.max(np.std(recent_accel, axis=0))) < 0.006
                    and float(np.max(np.std(recent_gyro, axis=0))) < 1.0
                    and float(np.linalg.norm(np.mean(recent_accel, axis=0) - baseline_accel)) < 0.020
                )
                if quiet and ts[-1] - ts[0] > 0.35:
                    quiet_since = quiet_since or time.monotonic()
                else:
                    quiet_since = None
                last_index = end_index
            if quiet_since is not None and time.monotonic() - quiet_since >= 0.6:
                break
            if ts[-1] - ts[0] > 10.0:
                raise RuntimeError(f"{label}：单次运动超过10秒，试验作废")
            time.sleep(0.01)

        counters = np.asarray([row.counter for row in trial_rows], dtype=np.int64)
        counter_diff = np.diff(counters)
        counter_resets = (counter_diff < 0) & (counters[1:] == 1)
        counter_drops = (counter_diff != 1) & ~counter_resets
        reset_count = int(np.count_nonzero(counter_resets))
        drop_count = int(np.count_nonzero(counter_drops))
        dt = np.diff(ts)
        angle_norm = float(np.linalg.norm(angle))
        expected = np.zeros(3)
        expected[axis] = sign * distance_m
        measured = corrected_position[-1]
        axial = float(sign * measured[axis])
        cross = float(np.linalg.norm(np.delete(measured, axis)))
        problems = []
        warnings = []
        if drop_count:
            problems.append(f"非复位型硬件计数不连续 {drop_count} 次")
        if reset_count:
            warnings.append(f"DIO2/counter 清零 {reset_count} 次，但拟合时间保持连续")
        if float(np.max(dt)) > 0.010:
            problems.append(f"最大时间间隔 {np.max(dt)*1000:.1f} ms")
        if angle_norm > 3.0:
            problems.append(f"移动中转动 {angle_norm:.2f}°")
        if not positive_phase or not braking_phase:
            problems.append("未清楚检测到加速和减速两个阶段")
        if axial <= 0.003:
            warnings.append("积分方向异常或轴向结果很小，保留用于拟合检查")

        result = {
            "axis": AXES[axis],
            "sign": sign,
            "expected_m": expected.tolist(),
            "direct_position_m": raw_position[-1].tolist(),
            "zupt_position_m": measured.tolist(),
            "axial_zupt_m": float(measured[axis]),
            "cross_axis_m": cross,
            "rotation_deg": angle.tolist(),
            "duration_s": float(ts[-1] - ts[0]),
            "counter_resets": reset_count,
            "counter_drops": drop_count,
            "dt_max_ms": float(np.max(dt) * 1000.0),
            "valid": not problems,
            "problems": problems,
            "warnings": warnings,
        }

        color = [40, 220, 100] if not problems else [230, 70, 70]
        self.rr.log(
            f"trajectory/trial_{len(self.translation_trials):02d}",
            self.rr.LineStrips3D([corrected_position.tolist()], colors=[color], radii=[0.002]),
        )
        if problems:
            self.status(
                f"✗ {label} 数据无效：{'；'.join(problems)}。请回到起点，重做本项"
            )
            self.operator_gate(f"{label}无效，请回到本项起始挡位并放稳")
            return self.collect_translation(axis, sign, distance_m, attempt + 1)
        self.status(
            f"✓ {label}：裸积分 {raw_position[-1][axis]*100:+.2f} cm；"
            f"ZUPT校正 {measured[axis]*100:+.2f} cm；横向串扰 {cross*100:.2f} cm"
            + (f"；警告：{'；'.join(warnings)}" if warnings else "")
        )
        return result

    def collect_translations(self) -> None:
        self.status(
            "步骤4/4：已知距离平移。每轴先 +5/−5 cm 回原点，再 +8/−8 cm 回原点"
        )
        for axis in self.translation_axes:
            for distance_m in self.distances_m:
                self.operator_gate(
                    f"下一项：从当前基准沿 +{AXES[axis]} 移动 {distance_m*100:.0f} cm；"
                    "请先摆好起始位置"
                )
                self.translation_trials.append(self.collect_translation(axis, 1, distance_m))
                self.save_progress()
                self.operator_gate(
                    f"下一项：沿 -{AXES[axis]} 移动 {distance_m*100:.0f} cm 返回基准；"
                    "请确认当前仍在正向终点并已放稳"
                )
                self.translation_trials.append(self.collect_translation(axis, -1, distance_m))
                self.save_progress()

    def save_progress(self) -> Path:
        """每个平移动作后立即落盘，中止时不丢已完成项。"""
        path = self.output.with_name(self.output.stem + "_progress.yaml")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(
                {
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                    "axes": [AXES[axis] for axis in self.translation_axes],
                    "complete": False,
                    "trials": self.translation_trials,
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return path

    def solve(self, gyro_bias: np.ndarray) -> dict:
        face_measured = []
        face_expected = []
        for face, data in self.faces.items():
            target = np.zeros(3)
            target[face[0]] = face[1]
            face_measured.append(data["accel_mean_g"])
            face_expected.append(target)
        design = np.column_stack([np.asarray(face_measured), np.ones(6)])
        accel_coefficients, _, _, _ = np.linalg.lstsq(design, np.asarray(face_expected), rcond=None)

        measured_angle = np.asarray([trial["measured_vector_deg"] for trial in self.rotation_trials])
        expected_angle = np.zeros_like(measured_angle)
        for index, trial in enumerate(self.rotation_trials):
            expected_angle[index, AXES.index(trial["axis"])] = trial["expected_deg"]
        gyro_matrix, gyro_rmse = fit_linear_map(measured_angle, expected_angle)

        measured_distance = np.asarray([trial["zupt_position_m"] for trial in self.translation_trials])
        expected_distance = np.asarray([trial["expected_m"] for trial in self.translation_trials])
        distance_matrix, distance_rmse = fit_linear_map(measured_distance, expected_distance)

        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "method": "six-face + bidirectional 90deg + known 5/8cm endpoint-constrained motion",
            "accelerometer": {
                "matrix": accel_coefficients[:3, :].T.tolist(),
                "offset_g": accel_coefficients[3, :].tolist(),
                "formula": "a_calibrated = matrix @ a_raw + offset_g",
                "faces": {face_name(key): value for key, value in self.faces.items()},
            },
            "gyroscope": {
                "bias_deg_s": gyro_bias.tolist(),
                "matrix": gyro_matrix.tolist(),
                "fit_rmse_deg": gyro_rmse,
                "formula": "gyro_calibrated = matrix @ (gyro_raw - bias_deg_s)",
                "trials": self.rotation_trials,
            },
            "known_distance_validation": {
                "matrix": distance_matrix.tolist(),
                "fit_rmse_m": distance_rmse,
                "formula": "empirical_displacement = matrix @ zupt_integrated_displacement",
                "warning": "This endpoint-constrained empirical map is not an absolute-position observable of an unaided IMU.",
                "trials": self.translation_trials,
            },
            "algorithm_comparison": {
                "direct": "body-frame acceleration delta double integration",
                "zupt": "endpoint zero velocity plus linear velocity drift removal per motion interval",
                "xio_note": "The referenced gait example also forces zero velocity during foot stance and removes per-interval velocity drift.",
            },
        }

    def solve_distance_only(self, gyro_bias: np.ndarray) -> dict:
        valid_trials = [trial for trial in self.translation_trials if trial["valid"]]
        measured_distance = np.asarray(
            [trial["zupt_position_m"] for trial in valid_trials]
        )
        expected_distance = np.asarray(
            [trial["expected_m"] for trial in valid_trials]
        )
        enough_directions = (
            len(valid_trials) >= 3
            and measured_distance.ndim == 2
            and np.linalg.matrix_rank(measured_distance) == 3
        )
        if enough_directions:
            distance_matrix, distance_rmse = fit_linear_map(
                measured_distance, expected_distance
            )
            matrix_value = distance_matrix.tolist()
        else:
            distance_rmse = None
            matrix_value = None
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "method": "known 5/8cm endpoint-constrained motion (distance-only continuation)",
            "gyroscope_bias_deg_s_for_rotation_rejection": gyro_bias.tolist(),
            "known_distance_validation": {
                "matrix": matrix_value,
                "fit_rmse_m": distance_rmse,
                "formula": "empirical_displacement = matrix @ zupt_integrated_displacement",
                "warning": "This endpoint-constrained empirical map is not an absolute-position observable of an unaided IMU.",
                "trials": self.translation_trials,
                "valid_trial_count": len(valid_trials),
            },
            "algorithm_comparison": {
                "direct": "body-frame acceleration delta double integration",
                "zupt": "endpoint zero velocity plus linear velocity drift removal per motion interval",
            },
        }

    def raw_path(self) -> Path:
        return self.output.with_name(self.output.stem + "_raw.npz")

    def save_raw(self) -> Path | None:
        with self.lock:
            rows = list(self.all_rows)
        if not rows:
            return None
        path = self.raw_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path,
            timestamp_s=np.asarray([row.ts for row in rows]),
            receiver_timestamp_s=np.asarray([row.rx_time for row in rows]),
            counter=np.asarray([row.counter for row in rows], dtype=np.uint32),
            gyro_deg_s=np.asarray([row.gyro for row in rows]),
            accel_g=np.asarray([row.accel for row in rows]),
            temperature_c=np.asarray([row.temp for row in rows]),
        )
        return path

    def run(self) -> None:
        if not self.reader.start():
            raise RuntimeError(f"无法独占打开 IMU 串口：{self.port}")
        completed = False
        try:
            gyro_bias, _, initial_face = self.collect_initial()
            if self.distance_only:
                self.status("距离专项续测：前面的六面和已完成角度不重做，直接进入5/8 cm")
                self.collect_translations()
                result = self.solve_distance_only(gyro_bias)
            else:
                self.collect_six_faces(initial_face)
                gyro_bias = np.mean(
                    [np.asarray(value["gyro_mean_deg_s"]) for value in self.faces.values()], axis=0
                )
                self.collect_rotations(gyro_bias)
                self.collect_translations()
                result = self.solve(gyro_bias)
            raw_path = self.save_raw()
            result["raw_data_file"] = str(raw_path.resolve()) if raw_path else None
            self.output.parent.mkdir(parents=True, exist_ok=True)
            self.output.write_text(
                yaml.safe_dump(result, allow_unicode=True, sort_keys=False), encoding="utf-8"
            )
            completed = True
            distance_rmse = result["known_distance_validation"]["fit_rmse_m"]
            if distance_rmse is None:
                self.status(
                    f"全部定时采集完成，但有效方向不足，未拟合距离矩阵；"
                    f"结果已保存 {self.output}"
                )
            else:
                self.status(
                    f"全部标定完成：距离拟合RMSE={distance_rmse * 100:.2f} cm；"
                    f"结果已保存 {self.output}"
                )
        finally:
            self.reader.stop()
            self.viz_stop.set()
            self.viz_thread.join(timeout=2.0)
            if not completed:
                raw_path = self.save_raw()
                if raw_path:
                    print(f"\n>>> 流程未完成；原始数据已保存：{raw_path}", flush=True)


def self_test() -> None:
    assert len(six_face_sequence((1, -1))) == 6
    assert len(set(six_face_sequence((1, -1)))) == 6
    for first, second in zip(six_face_sequence((1, -1)), six_face_sequence((1, -1))[1:]):
        assert first[0] != second[0]

    rate = 400.0
    ts = np.arange(0.0, 1.0 + 1.0 / rate, 1.0 / rate)
    accel = np.zeros((len(ts), 3))
    accel[:, 1] = -1.0
    accel[ts < 0.5, 0] = 0.2 / GRAVITY
    accel[ts >= 0.5, 0] = -0.2 / GRAVITY
    _, raw_position, corrected_velocity, corrected_position = integrate_translation(
        ts, accel, np.asarray([0.0, -1.0, 0.0])
    )
    assert abs(raw_position[-1, 0] - 0.05) < 4e-4
    assert np.linalg.norm(corrected_velocity[-1]) < 1e-12
    assert abs(corrected_position[-1, 0] - 0.05) < 4e-4

    gyro = np.zeros((len(ts), 3))
    gyro[:, 2] = 90.0
    angle = integrate_gyro(ts, gyro, np.zeros(3))
    assert np.allclose(angle, [0.0, 0.0, 90.0], atol=1e-9)

    # 机体绕 X 轴慢速倾斜 5°，但没有平移；姿态补偿后线加速度应近似为零。
    tilt_gyro = np.zeros((len(ts), 3))
    tilt_gyro[:, 0] = 5.0
    gravity_initial = np.asarray([0.0, -1.0, 0.0])
    tilt_accel = np.zeros((len(ts), 3))
    for index, timestamp in enumerate(ts):
        half = math.radians(5.0 * timestamp) / 2.0
        body_to_initial = np.asarray([math.sin(half), 0.0, 0.0, math.cos(half)])
        initial_to_body = np.asarray(
            [-body_to_initial[0], -body_to_initial[1], -body_to_initial[2], body_to_initial[3]]
        )
        tilt_accel[index] = quaternion_rotate(gravity_initial, initial_to_body)
    compensated = attitude_compensated_acceleration(
        ts, tilt_accel, tilt_gyro, gravity_initial, np.zeros(3)
    )
    assert float(np.max(np.abs(compensated))) < 1e-9

    measured = np.vstack([np.eye(3), -np.eye(3)])
    expected = measured @ np.diag([1.1, 0.9, 1.05])
    matrix, rmse = fit_linear_map(measured, expected)
    assert np.allclose(matrix, np.diag([1.1, 0.9, 1.05]))
    assert rmse < 1e-12
    print("SELF_TEST_OK: 90deg, 5cm integration, ZUPT, matrix fit")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(PROJECT / "config/devices_ubuntu.yaml"))
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--distances-cm", type=float, nargs="+", default=[5.0, 8.0])
    parser.add_argument("--distance-only", action="store_true")
    parser.add_argument("--auto-delay-s", type=float, default=0.0)
    parser.add_argument("--axes", choices=list(AXES), nargs="+", default=list(AXES))
    parser.add_argument("--capture-window-s", type=float, default=4.0)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    unit = load_config(args.config).units[0]
    if args.distance_only:
        selected_axes = "".join(args.axes).lower()
        prefix = "imu_distance" if selected_axes == "xyz" else f"imu_distance_{selected_axes}"
    else:
        prefix = "imu_known_motion"
    output = args.output or Path("imu_calibration_results") / (
        f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}.yaml"
    )
    GuidedCalibration(
        unit.imu.port,
        unit.imu.baud,
        output,
        args.distances_cm,
        distance_only=args.distance_only,
        auto_delay_s=args.auto_delay_s,
        axes="".join(args.axes),
        capture_window_s=args.capture_window_s,
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
