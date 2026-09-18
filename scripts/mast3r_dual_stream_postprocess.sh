#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TOOL_DIR="${MAST3R_SLAM_DIR:-/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM}"
PYTHON="$TOOL_DIR/.venv/bin/python"
VINS_CONFIG="/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/formal_runtime_calibration/vins_config.yaml"
IMU_CALIBRATION="$ROOT_DIR/config/imu_runtime_accel_calibrated_raw_gyro_20260816.yaml"

if [[ $# -ne 2 ]]; then
    echo "用法: $0 <D405会话目录> <输出目录>" >&2
    exit 2
fi

SESSION="$(realpath "$1")"
OUTPUT="$(realpath -m "$2")"
[[ -x "$PYTHON" ]] || { echo "MASt3R Python环境不存在: $PYTHON" >&2; exit 2; }
[[ -f "$SESSION/d405_frames.csv" ]] || { echo "D405会话无效: $SESSION" >&2; exit 2; }
mkdir -p "$OUTPUT"

echo "[1/5] MASt3R RGB + 400Hz IMU旋转先验"
"$ROOT_DIR/scripts/mast3r_slam_precision_workflow.sh" run \
    "$SESSION" "$OUTPUT/rgb" color

echo "[2/5] MASt3R左IR + 400Hz IMU旋转先验"
"$ROOT_DIR/scripts/mast3r_slam_precision_workflow.sh" run \
    "$SESSION" "$OUTPUT/infrared_left" infrared_left

echo "[3/5] RGB/左IR独立视觉轨迹一致性融合"
"$PYTHON" "$ROOT_DIR/scripts/fuse_mast3r_dual_stream.py" \
    --rgb "$OUTPUT/rgb/trajectory_frames.csv" \
    --infrared-left "$OUTPUT/infrared_left/trajectory_frames.csv" \
    --output "$OUTPUT/trajectory_dual_visual.csv" \
    --report "$OUTPUT/dual_stream_report.json"

set +u
source /opt/ros/humble/setup.bash
source /home/robot/ros2_ws/install/setup.bash
set -u

echo "[4/5] D405双红外米制尺度 + 400Hz IMU尺度"
"$PYTHON" "$ROOT_DIR/scripts/align_mast3r_scale_with_stereo.py" \
    --session "$SESSION" \
    --trajectory "$OUTPUT/trajectory_dual_visual.csv" \
    --frame-step 5 \
    --max-hop 5 \
    --max-depth-m 0.6 \
    --output "$OUTPUT/trajectory_stereo.csv" \
    --report "$OUTPUT/stereo_scale_report.json"
"$PYTHON" "$ROOT_DIR/scripts/align_mast3r_scale_with_imu.py" \
    --session "$SESSION" \
    --trajectory "$OUTPUT/trajectory_dual_visual.csv" \
    --stream color \
    --body-t-camera-yaml "$VINS_CONFIG" \
    --imu-calibration "$IMU_CALIBRATION" \
    --td-s -0.009109323 \
    --node-stride 10 \
    --max-hop 1 \
    --output "$OUTPUT/trajectory_imu_metric.csv" \
    --report "$OUTPUT/imu_scale_report.json"

echo "[5/5] 多段姿态、尺度与长基线双目联合后处理"
"$PYTHON" "$ROOT_DIR/scripts/fuse_mast3r_stereo_imu.py" \
    --session "$SESSION" \
    --trajectory "$OUTPUT/trajectory_imu_metric.csv" \
    --stereo-report "$OUTPUT/stereo_scale_report.json" \
    --imu-scale-report "$OUTPUT/imu_scale_report.json" \
    --vins-config "$VINS_CONFIG" \
    --imu-calibration "$IMU_CALIBRATION" \
    --expected-td-s -0.009109323 \
    --orientation-node-stride 10 \
    --minimum-stereo-sample-hop 3 \
    --metric-scale-mode stereo \
    --position-mode projected \
    --output "$OUTPUT/trajectory_stereo_imu.csv" \
    --report "$OUTPUT/stereo_imu_fusion_report.json"

echo "后处理完成: $OUTPUT/trajectory_stereo_imu.csv"
echo "全程未读取机械臂、Tracker或Lighthouse轨迹。"
