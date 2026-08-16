"""占位 VIO: 纯 IMU 积分示意位姿(陀螺 AHRS + 双重积分), 跑通采集→VIO→可视化管线。

不接真实算法: 陀螺积分姿态, 加速度转到世界系扣重力后双重积分出位置;
静止时(|a|≈1g)用 Mahoney 式重力对准修正姿态, 抑制重力泄漏导致的漂移。
后续替换为 OpenVINS / VINS-Fusion。

注意:
  - 启动先采 ~1s 自标定(重力向量 + 陀螺零偏), 期间必须保持静止!
  - 世界系: 以标定时刻为基准, 重力反方向为 +Z(即 Z 轴朝上), 便于轨迹展示。
  - 偏航无观测会慢慢漂(无磁力计), 位置长时也会漂 —— stub 仅供演示链路。
"""

from __future__ import annotations
from typing import Optional
import numpy as np

from .base import VIOBackend, Pose
from ..imu.imu_reader import ImuSample
from ..camera.realsense_capture import CameraFrame

G_MS2 = 9.81


# ---------- 四元数工具(xyzw 约定, 与 visualizer 一致) ----------

def _quat_mul(q1: np.ndarray, q2: np.ndarray) -> np.ndarray:
    """q1 ⊗ q2: 先施加 q2 再施加 q1。"""
    x1, y1, z1, w1 = q1
    x2, y2, z2, w2 = q2
    return np.array([
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
    ])


def _quat_conj(q: np.ndarray) -> np.ndarray:
    return np.array([-q[0], -q[1], -q[2], q[3]])


def _quat_rotate(p: np.ndarray, q: np.ndarray) -> np.ndarray:
    """用单位四元数 q(xyzw) 旋转向量/点集 p。"""
    x, y, z, w = q
    p = np.asarray(p, dtype=float)
    uv = np.cross(np.array([x, y, z]), p)
    uuv = np.cross(np.array([x, y, z]), uv)
    return p + 2.0 * (w * uv + uuv)


def _quat_from_two_vectors(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """把单位向量 a 转到单位向量 b 的四元数。"""
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    if c < -0.999999:
        # 接近反向: 任取垂直轴转 180°
        axis = np.cross(a, np.array([1.0, 0.0, 0.0]))
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross(a, np.array([0.0, 1.0, 0.0]))
        axis /= np.linalg.norm(axis)
        return np.array([axis[0], axis[1], axis[2], 0.0])
    q = np.array([v[0], v[1], v[2], 1.0 + c])
    return q / np.linalg.norm(q)


class StubVIO(VIOBackend):
    name = "stub_vio"

    # 自标定采样数(400Hz × 1s)
    CALIB_SAMPLES = 400
    # 加速度死区(m/s²): 小于此值当作静止, 抑制零偏积分
    DEADBAND_MS2 = 0.15
    # Mahoney 重力对准增益: 每单位方向误差的角速度修正(rad/s)
    MAHONEY_KP = 0.4
    # |a| 偏离 1g 在此范围内才把加速度当重力参考(运动中不修正)
    GRAV_REF_TOL = 0.08

    def __init__(self, name: str = "stub_vio"):
        self.name = name
        self._pose: Optional[Pose] = None
        self._v = np.zeros(3)
        self._t = np.zeros(3)
        self._q = np.array([0.0, 0.0, 0.0, 1.0])    # xyzw, body→world
        self._last_ts: Optional[float] = None

        # 自标定状态
        self._calib_a: list = []
        self._calib_w: list = []
        self._gyro_bias: Optional[np.ndarray] = None  # rad/s
        self.calibrated = False

    def feed_imu(self, sample: ImuSample) -> Optional[Pose]:
        a_raw = np.array([sample.ax, sample.ay, sample.az])           # g
        w_raw = np.radians([sample.gx, sample.gy, sample.gz])         # °/s → rad/s

        # ---- 阶段1: 收集标定样本(保持静止!) ----
        if not self.calibrated:
            self._calib_a.append(a_raw)
            self._calib_w.append(w_raw)
            if len(self._calib_a) >= self.CALIB_SAMPLES:
                a_mean = np.mean(self._calib_a, axis=0)
                # 静止时加速度计测的是"向上的支持力", 该方向定义为世界 +Z
                self._q = _quat_from_two_vectors(a_mean / np.linalg.norm(a_mean),
                                                 np.array([0.0, 0.0, 1.0]))
                self._gyro_bias = np.mean(self._calib_w, axis=0)
                self.calibrated = True
                self._last_ts = sample.ts
                print(f"[{self.name}] 自标定完成: gravity={np.round(a_mean, 4).tolist()} "
                      f"(|g|={np.linalg.norm(a_mean):.3f}g) "
                      f"gyro_bias={np.round(np.degrees(self._gyro_bias), 3).tolist()}°/s")
            # 标定期输出原点位姿, 让可视化先跑起来
            self._pose = Pose(ts=sample.ts, t=self._t.copy(), q=self._q.copy())
            return self._pose

        # ---- 阶段2: AHRS + 去偏积分 ----
        dt = sample.ts - self._last_ts if self._last_ts is not None else 0.0
        self._last_ts = sample.ts
        if dt <= 0 or dt > 1.0:
            return self._pose

        w = w_raw - self._gyro_bias

        # Mahoney 重力对准: 接近静止(|a|≈1g)时, 用实测重力方向修正姿态
        mag = float(np.linalg.norm(a_raw))
        if abs(mag - 1.0) < self.GRAV_REF_TOL:
            g_meas = a_raw / mag                                   # 体坐标系实测"上"方向
            g_pred = _quat_rotate(np.array([0.0, 0.0, 1.0]), _quat_conj(self._q))
            e = np.cross(g_meas, g_pred)
            w = w + self.MAHONEY_KP * e

        # 姿态一阶积分: q ← q + 0.5·q⊗[w·dt, 0]
        self._q = self._q + 0.5 * _quat_mul(
            self._q, np.array([w[0] * dt, w[1] * dt, w[2] * dt, 0.0]))
        self._q /= np.linalg.norm(self._q)

        # 世界系加速度: 转到世界系(g→m/s²), 扣掉重力
        a_world = _quat_rotate(a_raw, self._q) * G_MS2 + np.array([0.0, 0.0, -G_MS2])

        # 死区: 小加速度当作噪声, 防止零偏缓慢积分
        a_world[np.abs(a_world) < self.DEADBAND_MS2] = 0.0

        self._v += a_world * dt
        self._t += self._v * dt

        # 阻尼: 无显著运动时快速衰减速度, 示意用
        if np.all(a_world == 0.0):
            self._v *= 0.90
        else:
            self._v *= 0.98

        self._pose = Pose(ts=sample.ts, t=self._t.copy(), q=self._q.copy())
        return self._pose

    def feed_camera(self, frame: CameraFrame) -> Optional[Pose]:
        # stub 不用视觉, 不输出新位姿, 避免在相机回调里重复 log
        return None

    def latest(self) -> Optional[Pose]:
        return self._pose
