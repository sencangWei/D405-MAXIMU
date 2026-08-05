"""IMU 子包: KT-EX9-2 军工 IMU 的 UART 解析 + 读取。

KT-EX9-2 帧(38 字节):
  [0..1]   帧头 0xEB 0x90
  [2]      数据包长度 0x22
  [3]      帧 ID 0x01
  [4..7]   Gx float LE (°/s)
  [8..11]  Gy
  [12..15] Gz
  [16..19] Ax float LE (g)
  [20..23] Ay
  [24..27] Az
  [28..31] 温度 float LE (℃)
  [32..35] counter uint32 LE (1..400 per PPS period)
  [36]     校验和(字节[2..35]累加低8位)

PPS 接 IMU DIO2 → counter 每秒清零规整。小电脑不捕获 PPS 边沿，
靠 counter 检测丢帧 + 系统时钟打绝对时间戳。
"""

from .imu_reader import ImuReader, ImuSample, parse_frame, verify_checksum, find_frames
from .calibration import IMUCalibration

__all__ = [
    "ImuReader",
    "ImuSample",
    "IMUCalibration",
    "parse_frame",
    "verify_checksum",
    "find_frames",
]
