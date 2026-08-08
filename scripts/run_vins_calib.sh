#!/usr/bin/env bash
# 用标定录制会话(imucam)跑 VINS, 输出轨迹 CSV
# 用法: ./scripts/run_vins_calib.sh <会话目录> <输出CSV>
set -e
SESSION="${1:?需要会话目录}"
OUT="${2:?需要输出CSV}"
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="/home/robot/ros2_ws/src/vins_fusion_ros2/config/d405_stereo_imu/d405_stereo_imu_config.yaml"

source /opt/ros/humble/setup.sh
source /home/robot/ros2_ws/install/setup.sh
cd "$ROOT"

pkill -9 -f vins_fusion_ros2_node 2>/dev/null || true
pkill -9 -f replay_calib 2>/dev/null || true
sleep 1
rm -f "$OUT"

ros2 run vins_fusion_ros2 vins_fusion_ros2_node \
  --ros-args -p use_sim_time:=false -p config_file:="$CONFIG" \
  > /tmp/vins_calib.log 2>&1 &
VINS_PID=$!
sleep 5
kill -0 $VINS_PID 2>/dev/null || { echo "VINS 启动失败"; exit 1; }

python3 "$ROOT/scripts/record_odom_csv.py" --topic /odometry --out "$OUT" &
REC_PID=$!
sleep 2

python3 "$ROOT/scripts/replay_calib_to_ros2.py" --session "$SESSION" --rate 1.0 --imu-shift-ms 11.86
sleep 3
kill -9 $REC_PID 2>/dev/null || true
kill -9 $VINS_PID 2>/dev/null || true
pkill -9 -f vins_fusion 2>/dev/null || true
sleep 1
echo "输出: $OUT ($(($(wc -l < "$OUT") - 1)) poses)"
