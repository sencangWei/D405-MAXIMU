#!/usr/bin/env bash
# Original v1/v2 docker2 + MASt3R fusion（2026-09-12 配方），用于在三组目标数据上微调。
#
# 与 2026-09-12 的 v1/v2 融合记录逐项对齐：
#
#  1) 互补融合 = fuse_docker2_mast3r_complementary.py 默认参数，
#     即 v2 报告记录的 horizon 1.0 s / smoothing 4.0 s / local 0.5 / scale 0.5。
#     （v10 时代的 7.25 / 0.225 / 0.475 + adaptive 不是最初版。）
#  2) 中间层 = 双目短窗尺度 -> 400 Hz IMU 尺度 -> 视觉/双目/IMU 姿态融合，
#     即 --metric-scale-mode stereo --position-mode none；
#     不做 keyframe-graph、不做 Docker2 相对运动边（那些是 v6+ 才引入的）。
#     这三步的产物与 v1/v2 报告记录的输入逐字段一致。
#  3) 前端（2026-09-17 实测更正先前判断）：曾以为 9/12 的 toolchain 脏树 d06f5a8c
#     （MASt3R-SLAM-legacy-v10）+ motion_kf 配置是唯一原版前端。但在三组目标数据上
#     实测，该组合的双目尺度离散度 relative_p90_p10 = 0.89~1.26，全部 FAIL；
#     而现行树 26c0273c + config/mast3r_slam_d405_offline.yaml 给出
#     0.161 / 0.183 / 0.163，三组全 PASS。故默认改用后者。
#     （9/12 会话自身的原版前端输出无法复现：同样的树与逐位相同的输入，今天只能
#      产出 1697 帧且在 t=28.4000->31.8333 有 102 帧缺口；该会话不作为前端选择依据。）
#
# 用法: mast3r_fusion_original_v1_workflow.sh <D405会话目录> <Docker2/VINS轨迹.csv> <输出目录>
# 环境变量:
#   REUSE_FRONTEND=1        复用 <输出目录>/frontend 下已存在的前端轨迹
#   FRONTEND_TRAJECTORY=... 直接指定外部前端 trajectory_frames.csv（跳过第1阶段）
#   MAST3R_SLAM_DIR / MAST3R_SLAM_CONFIG  覆盖工具链与前端配置
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TOOL_DIR="${MAST3R_SLAM_DIR:-/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM}"
PYTHON="$TOOL_DIR/.venv/bin/python"
VINS_CONFIG="/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/formal_runtime_calibration/vins_config.yaml"
IMU_CALIBRATION="$ROOT_DIR/config/imu_runtime_accel_calibrated_raw_gyro_20260816.yaml"
CONFIG="${MAST3R_SLAM_CONFIG:-$ROOT_DIR/config/mast3r_slam_d405_offline.yaml}"
TD_S="-0.009109323"

[[ $# -eq 3 ]] || { echo "用法: $0 <D405会话目录> <Docker2/VINS轨迹.csv> <输出目录>" >&2; exit 2; }
SESSION="$(realpath "$1")"
VINS_TRAJECTORY="$(realpath "$2")"
OUTPUT="$(realpath -m "$3")"

[[ -f "$SESSION/d405_frames.csv" ]] || { echo "D405会话无效: $SESSION" >&2; exit 2; }
[[ -f "$VINS_TRAJECTORY" ]] || { echo "Docker2/VINS轨迹不存在: $VINS_TRAJECTORY" >&2; exit 2; }

mkdir -p "$OUTPUT"
FRONTEND="$OUTPUT/frontend"
mkdir -p "$FRONTEND"

echo "[1/5] MASt3R前端（现行树 + d405_offline 配置）"
if [[ -n "${FRONTEND_TRAJECTORY:-}" ]]; then
    FRONTEND="$(dirname "$(realpath "$FRONTEND_TRAJECTORY")")"
    echo "使用外部前端轨迹: $FRONTEND/trajectory_frames.csv"
elif [[ "${REUSE_FRONTEND:-0}" == "1" && -s "$FRONTEND/trajectory_frames.csv" ]]; then
    echo "复用已存在的前端轨迹: $FRONTEND/trajectory_frames.csv"
else
    MAST3R_SLAM_DIR="$TOOL_DIR" \
    MAST3R_SLAM_CONFIG="$CONFIG" \
        "$ROOT_DIR/scripts/mast3r_slam_precision_workflow.sh" run \
        "$SESSION" "$FRONTEND" infrared_left 0 0
fi

echo "[2/5] D405双红外短窗双向尺度"
# 只有本阶段从 db3 读双目标定，需要 rosbag2_py（未 source ROS 时不可见）。
# 用子 shell 隔离，避免 ROS 的 PYTHONPATH 影响其余阶段的前端 venv。
(
    set +u
    source /opt/ros/humble/setup.bash
    if [[ -f /home/robot/ros2_ws/install/setup.bash ]]; then
        source /home/robot/ros2_ws/install/setup.bash
    fi
    "$PYTHON" "$ROOT_DIR/scripts/align_mast3r_scale_with_stereo.py" \
        --session "$SESSION" \
        --trajectory "$FRONTEND/trajectory_frames.csv" \
        --trajectory-frame infrared_left \
        --motion-estimator pnp \
        --frame-step 5 \
        --max-hop 5 \
        --max-depth-m 0.6 \
        --output "$OUTPUT/trajectory_stereo_bidirectional.csv" \
        --report "$OUTPUT/stereo_scale_bidirectional_report.json"
)

echo "[3/5] 400 Hz IMU尺度与加速度偏置"
"$PYTHON" "$ROOT_DIR/scripts/align_mast3r_scale_with_imu.py" \
    --session "$SESSION" \
    --trajectory "$FRONTEND/trajectory_frames.csv" \
    --stream infrared_left \
    --body-t-camera-yaml "$VINS_CONFIG" \
    --imu-calibration "$IMU_CALIBRATION" \
    --td-s "$TD_S" \
    --node-stride 10 \
    --max-hop 1 \
    --output "$OUTPUT/trajectory_imu_metric.csv" \
    --report "$OUTPUT/imu_scale_report.json"

echo "[4/5] 视觉+双目+IMU姿态融合（无关键帧图）"
"$PYTHON" "$ROOT_DIR/scripts/fuse_mast3r_stereo_imu.py" \
    --session "$SESSION" \
    --trajectory "$OUTPUT/trajectory_imu_metric.csv" \
    --stream infrared_left \
    --stereo-report "$OUTPUT/stereo_scale_bidirectional_report.json" \
    --imu-scale-report "$OUTPUT/imu_scale_report.json" \
    --vins-config "$VINS_CONFIG" \
    --imu-calibration "$IMU_CALIBRATION" \
    --expected-td-s "$TD_S" \
    --orientation-node-stride 10 \
    --position-node-stride 10 \
    --minimum-stereo-sample-hop 1 \
    --metric-scale-mode stereo \
    --position-mode none \
    --output "$OUTPUT/trajectory_stereo_bidir_imu_orientation.csv" \
    --report "$OUTPUT/stereo_bidir_imu_orientation_report.json"

echo "[5/5] 互补融合（v2 原始参数 = 脚本默认值）"
"$PYTHON" "$ROOT_DIR/scripts/fuse_docker2_mast3r_complementary.py" \
    --mast3r "$OUTPUT/trajectory_stereo_bidir_imu_orientation.csv" \
    --docker2 "$VINS_TRAJECTORY" \
    --body-t-camera-yaml "$VINS_CONFIG" \
    --output "$OUTPUT/trajectory_fused.csv" \
    --report "$OUTPUT/fusion_report.json"

python3 - "$OUTPUT" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
fusion = json.loads((root / "fusion_report.json").read_text(encoding="utf-8"))
if fusion.get("result") != "PASS":
    raise SystemExit("互补融合未通过内部质量门禁: " + str(fusion.get("failures")))
if fusion.get("external_ground_truth_used") is not False or fusion.get("slam_supervision"):
    raise SystemExit("没有证明外部真值未参与")
print("original v1 门禁通过: " + str(root / "trajectory_fused.csv"))
PY

echo "原版v1融合轨迹: $OUTPUT/trajectory_fused.csv"
echo "算法输入仅包含D405双红外、UMI 400 Hz IMU、MASt3R和Docker2/VINS轨迹。"
