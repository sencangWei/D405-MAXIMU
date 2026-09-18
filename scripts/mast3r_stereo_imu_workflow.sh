#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TOOL_DIR="${MAST3R_SLAM_DIR:-/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM}"
PYTHON="$TOOL_DIR/.venv/bin/python"
VINS_CONFIG="/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/formal_runtime_calibration/vins_config.yaml"
IMU_CALIBRATION="$ROOT_DIR/config/imu_runtime_accel_calibrated_raw_gyro_20260816.yaml"

set +u
source /opt/ros/humble/setup.bash
source /home/robot/ros2_ws/install/setup.bash
set -u

usage() {
    echo "用法: $0 <D405会话目录> <输出目录>"
    echo "仅运行 MASt3R + D405双红外尺度 + 400Hz IMU；不运行或读取Docker2轨迹。"
}

[[ $# -eq 2 ]] || { usage; exit 2; }
session="$(realpath "$1")"
output="$(realpath -m "$2")"
mast3r_output="$output/mast3r"

[[ -x "$PYTHON" ]] || { echo "MASt3R Python环境不存在: $PYTHON" >&2; exit 2; }
[[ -f "$session/d405_frames.csv" ]] || { echo "缺少D405帧索引: $session" >&2; exit 2; }
[[ -f "$session/external_imu/imu.bin" ]] || { echo "缺少400Hz IMU数据: $session" >&2; exit 2; }
mkdir -p "$output"

run_if_missing() {
    local label="$1"
    local stage_output="$2"
    local stage_report="$3"
    shift 3
    if [[ -s "$stage_output" && -s "$stage_report" ]]; then
        echo "复用已完成阶段 $label: $stage_output"
        return 0
    fi
    "$@"
}

echo "[1/9] MASt3R左红外视觉轨迹 + IMU旋转先验"
if [[ -s "$mast3r_output/trajectory_frames.csv" \
      && -s "$mast3r_output/run_manifest.json" ]]; then
    echo "复用已完成的MASt3R视觉轨迹: $mast3r_output/trajectory_frames.csv"
else
    if [[ -e "$mast3r_output/dataset" ]]; then
        [[ ! -L "$mast3r_output/dataset" ]] || {
            echo "拒绝归档符号链接数据集: $mast3r_output/dataset" >&2
            exit 2
        }
        incomplete_suffix="$(date +%Y%m%d_%H%M%S)_$$"
        echo "归档未完成的MASt3R数据集: dataset.incomplete_$incomplete_suffix"
        mv -- "$mast3r_output/dataset" \
            "$mast3r_output/dataset.incomplete_$incomplete_suffix"
        if [[ -e "$mast3r_output/mast3r_logs" ]]; then
            [[ ! -L "$mast3r_output/mast3r_logs" ]] || {
                echo "拒绝归档符号链接日志目录: $mast3r_output/mast3r_logs" >&2
                exit 2
            }
            mv -- "$mast3r_output/mast3r_logs" \
                "$mast3r_output/mast3r_logs.incomplete_$incomplete_suffix"
        fi
    fi
    "$ROOT_DIR/scripts/mast3r_slam_precision_workflow.sh" run \
        "$session" "$mast3r_output" infrared_left 0 0
fi

echo "[2/9] 双红外短时尺度"
run_if_missing "双红外短时尺度" \
    "$mast3r_output/trajectory_stereo_bidirectional.csv" \
    "$mast3r_output/stereo_scale_bidirectional_report.json" \
    "$PYTHON" "$ROOT_DIR/scripts/align_mast3r_scale_with_stereo.py" \
    --session "$session" \
    --trajectory "$mast3r_output/trajectory_frames.csv" \
    --trajectory-frame infrared_left \
    --motion-estimator pnp --frame-step 5 --max-hop 5 --max-depth-m 0.6 \
    --output "$mast3r_output/trajectory_stereo_bidirectional.csv" \
    --report "$mast3r_output/stereo_scale_bidirectional_report.json"

echo "[3/9] 双红外中等跨度尺度"
run_if_missing "双红外中等跨度尺度" \
    "$mast3r_output/trajectory_stereo_long_hops.csv" \
    "$mast3r_output/stereo_scale_long_hops_report.json" \
    "$PYTHON" "$ROOT_DIR/scripts/align_mast3r_scale_with_stereo.py" \
    --session "$session" \
    --trajectory "$mast3r_output/trajectory_frames.csv" \
    --trajectory-frame infrared_left \
    --motion-estimator pnp --frame-step 5 --hop-values 8,12,16 --max-depth-m 0.6 \
    --output "$mast3r_output/trajectory_stereo_long_hops.csv" \
    --report "$mast3r_output/stereo_scale_long_hops_report.json"

echo "[4/9] 双红外10Hz密集尺度"
run_if_missing "双红外10Hz密集尺度" \
    "$mast3r_output/trajectory_stereo_dense10hz.csv" \
    "$mast3r_output/stereo_scale_dense10hz_report.json" \
    "$PYTHON" "$ROOT_DIR/scripts/align_mast3r_scale_with_stereo.py" \
    --session "$session" \
    --trajectory "$mast3r_output/trajectory_frames.csv" \
    --trajectory-frame infrared_left \
    --motion-estimator pnp --frame-step 3 --max-hop 3 --max-depth-m 0.6 \
    --output "$mast3r_output/trajectory_stereo_dense10hz.csv" \
    --report "$mast3r_output/stereo_scale_dense10hz_report.json"

echo "[5/9] 双红外0.8至1.6秒跨窗几何"
run_if_missing "双红外0.8至1.6秒跨窗几何" \
    "$mast3r_output/trajectory_stereo_multisecond.csv" \
    "$mast3r_output/stereo_scale_multisecond_report.json" \
    "$PYTHON" "$ROOT_DIR/scripts/align_mast3r_scale_with_stereo.py" \
    --session "$session" \
    --trajectory "$mast3r_output/trajectory_frames.csv" \
    --trajectory-frame infrared_left \
    --motion-estimator pnp --frame-step 5 --hop-values 24,32,48 --max-depth-m 0.6 \
    --output "$mast3r_output/trajectory_stereo_multisecond.csv" \
    --report "$mast3r_output/stereo_scale_multisecond_report.json"

echo "[6/9] 400Hz IMU尺度与偏置"
run_if_missing "400Hz IMU尺度与偏置" \
    "$mast3r_output/trajectory_imu_metric.csv" \
    "$mast3r_output/imu_scale_report.json" \
    "$PYTHON" "$ROOT_DIR/scripts/align_mast3r_scale_with_imu.py" \
    --session "$session" \
    --trajectory "$mast3r_output/trajectory_frames.csv" \
    --stream infrared_left \
    --body-t-camera-yaml "$VINS_CONFIG" \
    --imu-calibration "$IMU_CALIBRATION" \
    --td-s -0.009109323 --node-stride 10 --max-hop 1 \
    --output "$mast3r_output/trajectory_imu_metric.csv" \
    --report "$mast3r_output/imu_scale_report.json"

echo "[7/9] 视觉/双目/IMU关键帧图联合优化（无Docker2相对运动）"
"$PYTHON" "$ROOT_DIR/scripts/fuse_mast3r_stereo_imu.py" \
    --session "$session" \
    --trajectory "$mast3r_output/trajectory_imu_metric.csv" \
    --stream infrared_left \
    --stereo-report "$mast3r_output/stereo_scale_bidirectional_report.json" \
    --additional-stereo-report "$mast3r_output/stereo_scale_long_hops_report.json" \
    --additional-stereo-report "$mast3r_output/stereo_scale_dense10hz_report.json" \
    --additional-stereo-report "$mast3r_output/stereo_scale_multisecond_report.json" \
    --imu-scale-report "$mast3r_output/imu_scale_report.json" \
    --vins-config "$VINS_CONFIG" \
    --imu-calibration "$IMU_CALIBRATION" \
    --expected-td-s -0.009109323 \
    --orientation-node-stride 10 --position-node-stride 5 \
    --minimum-stereo-sample-hop 1 \
    --keyframe-dir "$mast3r_output/mast3r_logs/keyframes/dataset" \
    --visual-position-sigma-m 0.020 \
    --joint-max-correction-mm 25 \
    --full-rate-imu-position-refinement --full-rate-max-correction-mm 20 \
    --metric-scale-mode stereo --position-mode keyframe-graph \
    --output "$mast3r_output/trajectory_graph_camera.csv" \
    --report "$mast3r_output/graph_fusion_report.json"

echo "[8/9] 左红外光心转换到IMU/body原点"
"$PYTHON" "$ROOT_DIR/scripts/convert_camera_trajectory_to_body.py" \
    --input "$mast3r_output/trajectory_graph_camera.csv" \
    --body-t-camera-yaml "$VINS_CONFIG" \
    --output "$output/trajectory_fused_unsmoothed.csv" \
    --report "$output/camera_to_body_report.json"

echo "[9/9] 零相位轻度平滑并写入独立运行清单"
"$PYTHON" "$ROOT_DIR/scripts/smooth_pose_trajectory.py" \
    --input "$output/trajectory_fused_unsmoothed.csv" \
    --output "$output/trajectory_fused.csv" \
    --method gaussian --gaussian-sigma-s 0.025 \
    --report "$output/smoothing_report.json"

"$PYTHON" - "$session" "$output" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

session = Path(sys.argv[1]).resolve()
output = Path(sys.argv[2]).resolve()
graph_report = json.loads(
    (output / "mast3r/graph_fusion_report.json").read_text(encoding="utf-8")
)
manifest = {
    "schema": "umi_mast3r_stereo_imu_standalone_run_v1",
    "result": graph_report["result"],
    "algorithm": "MASt3R visual SLAM + D405 stereo metric scale + 400Hz IMU",
    "session": str(session),
    "docker2_slam_run": False,
    "docker2_trajectory_used": False,
    "relative_motion_trajectory": None,
    "external_ground_truth_used": False,
    "ground_truth_policy": "Lighthouse is permitted only after trajectory generation for scoring",
    "output_frame": "body_imu_origin",
    "output": str((output / "trajectory_fused.csv").resolve()),
    "output_sha256": hashlib.sha256(
        (output / "trajectory_fused.csv").read_bytes()
    ).hexdigest(),
    "graph_report": str((output / "mast3r/graph_fusion_report.json").resolve()),
}
(output / "run_manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print(json.dumps(manifest, ensure_ascii=False, indent=2))
PY

echo "纯融合轨迹完成: $output/trajectory_fused.csv"
