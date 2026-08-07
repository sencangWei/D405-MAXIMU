#!/usr/bin/env bash
# 方案1: 双IR Stereo-IMU (VINS-Fusion) 回放评测
# 用法: ./run_slam_stereo_vins.sh <会话目录> [倍速]
set -eo pipefail

SESSION="${1:?用法: $0 <会话目录> [倍速]}"
RATE="${2:-1.0}"

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VINS_CONFIG="/home/robot/ros2_ws/install/vins_fusion_ros2/share/vins_fusion_ros2/config/d405_stereo_imu/d405_stereo_imu_config.yaml"

source /opt/ros/humble/setup.sh
source /home/robot/ros2_ws/install/setup.sh

echo "[方案1] VINS-Fusion Stereo-IMU 回放: $SESSION (x$RATE)"

mkdir -p /home/robot/vins_output/pose_graph

timeout 400 ros2 run vins_fusion_ros2 vins_fusion_ros2_node --ros-args \
  -p use_sim_time:=false \
  -p config_file:="$VINS_CONFIG" &
NODE_PID=$!
sleep 4

python3 "$ROOT/scripts/replay_db3_to_ros2.py" \
  --session "$SESSION" --mode stereo --rate "$RATE" || true

sleep 2
kill $NODE_PID 2>/dev/null || true
echo "[方案1] 结束, 轨迹输出在 /home/robot/vins_output/"
