#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# 三路 720p 采集 (RGB + Depth + 左IR) + 外置 IMU。
# 用系统 pyrealsense2: 四路全开在 D405 上掉帧~15%, 三路掉帧仅~2%。
exec python3 -u "$ROOT_DIR/scripts/capture_d405_720p_rgbd_imu.py" "$@"
