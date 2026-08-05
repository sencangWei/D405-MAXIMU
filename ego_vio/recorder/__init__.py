"""录制子包: 三路(D405 彩色/深度 + IMU)录制，时间戳对齐。"""

from .recorder import Recorder, UnitRecorder

__all__ = ["Recorder", "UnitRecorder"]
