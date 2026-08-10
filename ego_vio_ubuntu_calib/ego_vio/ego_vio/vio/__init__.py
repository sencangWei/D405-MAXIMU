"""VIO 后端接口。

可插拔设计:
  - VIOBackend(抽象): feed(imu_sample, camera_frame) → Pose
  - StubVIO: 占位，输出伪位姿(用于跑通管线/给客户演示前测试)
  - 后续接 OpenVINS / VINS-Fusion(ROS 节点) / 自研

标定: 实际 VIO 需要先 Kalibr 标 camera-IMU 外参，喂给具体后端。
"""

from .base import VIOBackend, Pose
from .stub import StubVIO

__all__ = ["VIOBackend", "Pose", "StubVIO"]
