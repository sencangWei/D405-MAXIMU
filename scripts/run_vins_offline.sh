#!/usr/bin/env bash
# VINS-Fusion Stereo-IR 离线跑: 回放数据 -> 输出轨迹 CSV
# 用法: ./scripts/run_vins_offline.sh [会话目录] [对齐秒数]
set -o pipefail

SESSION="${1:-recordings/d405_720p_rgb_stereo_ir_20260807_115333}"
ALIGN_S="${2:-auto}"
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

source /opt/ros/humble/setup.sh
source /home/robot/ros2_ws/install/setup.sh
cd "$ROOT"

CONFIG="/home/robot/ros2_ws/src/vins_fusion_ros2/config/d405_stereo_imu/d405_stereo_imu_config.yaml"
OUT_CSV="/tmp/vins_odom.csv"

echo "======================================"
echo " VINS-Fusion Stereo-IR 离线跑"
echo " 会话:   $SESSION"
echo " 对齐:   --imu-align-s $ALIGN_S"
echo " 输出:   $OUT_CSV"
echo "======================================"

# 清理旧进程/文件
pkill -9 -f vins_fusion 2>/dev/null
pkill -9 -f replay_db3 2>/dev/null
pkill -9 -f record_odom 2>/dev/null
sleep 1
rm -f "$OUT_CSV" /tmp/vins_run.log /tmp/odom.log

# 1. 启动 VINS
echo "[1/3] 启动 VINS-Fusion..."
ros2 run vins_fusion_ros2 vins_fusion_ros2_node \
  --ros-args -p use_sim_time:=false -p config_file:="$CONFIG" \
  > /tmp/vins_run.log 2>&1 &
VINS_PID=$!
sleep 5
if ! kill -0 $VINS_PID 2>/dev/null; then
    echo "[ERROR] VINS 启动失败, 看 /tmp/vins_run.log"
    exit 1
fi

# 2. 启动里程计录制
echo "[2/3] 录制里程计..."
python3 "$ROOT/scripts/record_odom_csv.py" \
  --topic /odometry --out "$OUT_CSV" \
  > /tmp/odom.log 2>&1 &
REC_PID=$!
sleep 2

# 3. 回放数据
if [ "$ALIGN_S" = "auto" ]; then
    echo "[3/3] 回放数据 (skip 1.5s warmup, 自动对齐)..."
    python3 "$ROOT/scripts/replay_db3_to_ros2.py" \
      --session "$SESSION" --mode stereo --rate 1.0 --skip-s 1.5
else
    echo "[3/3] 回放数据 (skip 1.5s warmup, align $ALIGN_S s)..."
    python3 "$ROOT/scripts/replay_db3_to_ros2.py" \
      --session "$SESSION" --mode stereo --rate 1.0 \
      --skip-s 1.5 --imu-align-s "$ALIGN_S"
fi

echo "等待 VINS 处理完..."
sleep 5

kill -9 $REC_PID 2>/dev/null
kill -9 $VINS_PID 2>/dev/null
pkill -9 -f vins_fusion 2>/dev/null
sleep 1

echo ""
echo "===== 结果 ====="
if [ -f "$OUT_CSV" ]; then
    echo "里程计: $(($(wc -l < "$OUT_CSV") - 1)) 位姿 -> $OUT_CSV"
else
    echo "无输出!"
fi
echo "VINS log: /tmp/vins_run.log"
