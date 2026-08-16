"""VIO 后端抽象接口 + Pose 数据结构。"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class Pose:
    """6DoF 位姿。t 平移(米), q 旋转四元数 [x,y,z,w]。"""
    ts: float
    t: np.ndarray         # (3,) 平移
    q: np.ndarray         # (4,) xyzw 四元数
    valid: bool = True


class VIOBackend(ABC):
    """视觉惯性里程计后端。

    子类实现 feed(): 喂 IMU 样本和/或相机帧，返回最新位姿(或 None)。
    """

    name: str = "vio"

    @abstractmethod
    def feed_imu(self, sample) -> Optional[Pose]:
        """喂一个 IMU 样本，可能返回更新后的位姿。"""

    @abstractmethod
    def feed_camera(self, frame) -> Optional[Pose]:
        """喂一个相机帧，可能返回更新后的位姿。"""

    @abstractmethod
    def latest(self) -> Optional[Pose]:
        """取最新位姿(给可视化用)。"""

    def close(self):
        pass
