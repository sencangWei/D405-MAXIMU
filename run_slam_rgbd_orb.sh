#!/usr/bin/env bash
# 方案2: RGB-D-Inertial (ORB-SLAM3) 回放评测
# 用法: ./run_slam_rgbd_orb.sh <会话目录> [倍速]
set -eo pipefail

SESSION="${1:?用法: $0 <会话目录> [倍速]}"
RATE="${2:-1.0}"

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ORB_ROOT="/home/robot/ego_pipeline/work/toolchains/ORB_SLAM3"

source /opt/ros/humble/setup.sh
source /home/robot/ros2_ws/install/setup.sh

echo "[方案2] ORB-SLAM3 RGB-D-Inertial 回放: $SESSION (x$RATE)"

timeout 400 ros2 run ego_orbslam3_ros2 rgbd_inertial_node --ros-args \
  -p vocabulary:="$ORB_ROOT/Vocabulary/ORBvoc.txt" \
  -p settings:="$ROOT/config/orbslam3_d405_rgbd_inertial_720p.yaml" \
  -p viewer:=false &
NODE_PID=$!
sleep 4

python3 "$ROOT/scripts/replay_db3_to_ros2.py" \
  --session "$SESSION" --mode rgbd --rate "$RATE" || true

sleep 2
kill $NODE_PID 2>/dev/null || true
echo "[方案2] 结束"
