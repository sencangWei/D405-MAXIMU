"""ego_vio —— 小电脑实时双手 VIO + 可视化

三套 ego 单元(D405 + 军工IMU):
  - left_hand / right_hand: 实时 VIO + Rerun 轨迹可视化(给客户展示)
  - head: 只采集录制，后处理做 SLAM/稠密点云

时间戳: 所有单元共享系统时钟(CLOCK_MONOTONIC / time.perf_counter)，
        三路天然对齐。PPS 接各 IMU DIO2 让 counter 规整、检测丢帧。
"""

__version__ = "0.1.0"
