#!/usr/bin/env bash
# ORB-SLAM3 RGB-D-Inertial 离线跑: 回放数据 -> 输出轨迹 CSV
# 用法: ./scripts/run_orb_rgbd_offline.sh [会话目录] [IMU对齐秒数]
# 注意: ORB 需要 IMU 领先图像, IMU对齐应为负值 (IMU提前)
set -o pipefail

SESSION="${1:-recordings/d405_720p_all_20260807_115453}"
ALIGN_S="${2:--1.52}"
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ORB_ROOT="/home/robot/ego_pipeline/work/toolchains/ORB_SLAM3"

source /opt/ros/humble/setup.sh
source /home/robot/ros2_ws/install/setup.sh
cd "$ROOT"

OUT_CSV="/tmp/orb_odom.csv"

echo "======================================"
echo " ORB-SLAM3 RGB-D-Inertial 离线跑"
echo " 会话:   $SESSION"
echo " IMU对齐: $ALIGN_S s (负=IMU提前)"
echo " 输出:   $OUT_CSV"
echo "======================================"

# 清理旧进程/文件
pkill -9 -f rgbd_inertial 2>/dev/null
pkill -9 -f replay_db3 2>/dev/null
pkill -9 -f record_odom 2>/dev/null
sleep 2
rm -f "$OUT_CSV" /tmp/orb_run.log /tmp/orb_odom.log

echo "[1/3] 启动 ORB-SLAM3..."
ros2 run ego_orbslam3_ros2 rgbd_inertial_node \
  --ros-args \
  -p vocabulary:="$ORB_ROOT/Vocabulary/ORBvoc.txt" \
  -p settings:="$ROOT/config/orbslam3_d405_rgbd_inertial_720p.yaml" \
  -p viewer:=false > /tmp/orb_run.log 2>&1 &
ORB_PID=$!
sleep 5
if ! kill -0 $ORB_PID 2>/dev/null; then
    echo "[ERROR] ORB 启动失败, 看 /tmp/orb_run.log"
    exit 1
fi

echo "[2/3] 录制里程计..."
python3 "$ROOT/scripts/record_odom_csv.py" \
  --topic /orbslam3/odom --out "$OUT_CSV" > /tmp/orb_odom.log 2>&1 &
REC_PID=$!
sleep 2

echo "[3/3] 回放数据 (IMU对齐 $ALIGN_S s)..."
python3 "$ROOT/scripts/replay_db3_to_ros2.py" \
  --session "$SESSION" --mode rgbd --rate 1.0 \
  --imu-align-s "$ALIGN_S"

echo "等待 ORB 处理完..."
sleep 5

kill $REC_PID 2>/dev/null
kill $ORB_PID 2>/dev/null
sleep 1

echo ""
echo "===== 结果 ====="
if [ -f "$OUT_CSV" ]; then
    echo "里程计: $(($(wc -l < "$OUT_CSV") - 1)) 位姿 -> $OUT_CSV"
else
    echo "无输出!"
fi
echo "ORB log: /tmp/orb_run.log"
