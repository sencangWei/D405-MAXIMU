#!/usr/bin/env bash
set -eo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

echo "=== 纯IMU水平平移三轴标定 ==="
echo "只读取外置IMU；不启动相机、ROS或VINS。"
exec /usr/bin/python3 "$ROOT/calibrate_imu_planar_axes.py" "$@"
