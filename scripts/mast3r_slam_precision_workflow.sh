#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TOOL_DIR="${MAST3R_SLAM_DIR:-/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM}"
PYTHON="$TOOL_DIR/.venv/bin/python"
CONFIG="${MAST3R_SLAM_CONFIG:-$ROOT_DIR/config/mast3r_slam_d405_offline.yaml}"
VINS_CONFIG="/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/formal_runtime_calibration/vins_config.yaml"
IMU_CALIBRATION="$ROOT_DIR/config/imu_runtime_accel_calibrated_raw_gyro_20260816.yaml"
CUDA_ROOT="$TOOL_DIR/.cuda"
CHECKPOINT="${MAST3R_SLAM_CHECKPOINT:-$TOOL_DIR/checkpoints/MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric.pth}"
CROP_BOTTOM_PX="${MAST3R_CROP_BOTTOM_PX:-0}"
MASK_FIXED_SELF="${MAST3R_MASK_FIXED_SELF:-0}"

usage() {
    echo "用法:"
    echo "  $0 run <D405会话目录> <输出目录> [color|infrared_left] [最大帧数] [起始帧索引]"
    echo "  $0 fusion <D405会话目录> <VINS轨迹.csv> <VINS验收报告.json> <输出目录>"
    echo "  $0 compare <D405会话目录> <Docker2轨迹.csv> <Lighthouse-body真值.csv> <输出目录> [color|infrared_left]"
}

# 09-14 的稀疏/密运动关键帧两个候选各用一份前端配置：sparse 用 offline.yaml，
# tight 用 offline_motion_kf_tight.yaml（多出 motion_keyframe_translation: 0.15 /
# motion_keyframe_rotation_deg: 5.0）。这两个键缺失时 tracker.py 的
# `limit > 0.0` 守卫恒假，运动关键帧机制**静默消失**——不报错、不告警，
# 前端产物与 09-14 从此不可复现。所以这里按输出目录名把配置选择还原回去；
# MAST3R_SLAM_CONFIG 显式给了就尊重它。
resolve_candidate_config() {
    local output="$1"
    if [[ -n "${MAST3R_SLAM_CONFIG:-}" ]]; then
        CONFIG="$MAST3R_SLAM_CONFIG"
        return
    fi
    case "$(basename "$output")" in
        tight) CONFIG="$ROOT_DIR/config/mast3r_slam_d405_offline_motion_kf_tight.yaml" ;;
        *)     CONFIG="$ROOT_DIR/config/mast3r_slam_d405_offline.yaml" ;;
    esac
}

warn_if_motion_keyframes_disabled() {
    grep -q 'motion_keyframe_translation' "$CONFIG" && return 0
    echo "警告: $CONFIG 不含 motion_keyframe_*，MASt3R 前端的运动关键帧会被静默关闭" >&2
    echo "      (tracker.py 的 'limit > 0.0' 守卫恒假)。tight 候选需要" >&2
    echo "      config/mast3r_slam_d405_offline_motion_kf_tight.yaml。" >&2
}

check_installation() {
    [[ -x "$PYTHON" ]] || { echo "MASt3R Python环境不存在: $PYTHON" >&2; exit 2; }
    [[ -f "$CONFIG" ]] || { echo "MASt3R配置不存在: $CONFIG" >&2; exit 2; }
    [[ -f "$CHECKPOINT" ]] || {
        echo "MASt3R主模型不存在: $CHECKPOINT" >&2; exit 2;
    }
    [[ -x "$CUDA_ROOT/bin/nvcc" ]] || { echo "CUDA构建环境不存在: $CUDA_ROOT" >&2; exit 2; }
}

main_supports_checkpoint_argument() {
    (
        cd "$TOOL_DIR"
        "$PYTHON" main.py --help 2>&1 | grep -q -- '--checkpoint'
    )
}

run_mast3r() {
    local session="$1"
    local output="$2"
    local stream="$3"
    local max_frames="$4"
    local start_index="$5"
    mkdir -p "$output"
    set +u
    source /opt/ros/humble/setup.bash
    source /home/robot/ros2_ws/install/setup.bash
    set -u
    local prepare_args=(
        --session "$session"
        --output "$output/dataset"
        --stream "$stream"
        --start-index "$start_index"
        --crop-bottom-px "$CROP_BOTTOM_PX"
    )
    if [[ "$MASK_FIXED_SELF" == "1" ]]; then
        prepare_args+=(--mask-fixed-self)
    fi
    if [[ "$max_frames" -gt 0 ]]; then
        prepare_args+=(--max-frames "$max_frames")
    fi
    if [[ "$stream" == "infrared_left" ]]; then
        prepare_args+=(--include-stereo-right)
    fi
    python3 "$ROOT_DIR/scripts/prepare_mast3r_slam_dataset.py" "${prepare_args[@]}"
    python3 "$ROOT_DIR/scripts/prepare_mast3r_imu_rotation_priors.py" \
        --session "$session" \
        --dataset "$output/dataset" \
        --stream "$stream" \
        --vins-config "$VINS_CONFIG" \
        --imu-calibration "$IMU_CALIBRATION" \
        --expected-td-s -0.009109323

    export CUDA_HOME="$CUDA_ROOT"
    export PATH="$CUDA_HOME/bin:$PATH"
    export LD_LIBRARY_PATH="$CUDA_HOME/lib64:${LD_LIBRARY_PATH:-}"
    local started=$SECONDS
    local mast3r_args=(
        --dataset "$output/dataset"
        --config "$CONFIG"
        --save-as "$output/mast3r_logs"
        --no-viz
        --no-reconstruction
    )
    if main_supports_checkpoint_argument; then
        mast3r_args+=(--checkpoint "$CHECKPOINT")
    fi
    # --calib 是打开 config["use_calib"] 的唯一途径(main.py 里由它反向赋值),所以
    # 不能拿 config["use_calib"] 当传参前提:那是循环条件,配置写成 False 时永远为假,
    # 前端会静默丢掉 D405 内参(K=None、点图不落到真实射线上、后端走 solve_GN_rays),
    # 深度方向失去约束 —— 静止场景 frame1 即漂 3.6 mm、前 300 帧漂 31.5 mm。
    if [[ -f "$output/dataset/calibration.yaml" ]]; then
        mast3r_args+=(--calib "$output/dataset/calibration.yaml")
    fi
    (
        cd "$TOOL_DIR"
        "$PYTHON" main.py "${mast3r_args[@]}"
    ) 2>&1 | tee "$output/mast3r.log"
    local elapsed=$((SECONDS - started))
    local conversion_args=()
    if grep -Eq '^[[:space:]]*reverse_order:[[:space:]]*(true|True|1)' "$CONFIG"; then
        conversion_args+=(--reverse-order)
    fi
    "$PYTHON" "$ROOT_DIR/scripts/convert_mast3r_slam_trajectory.py" \
        --trajectory "$output/mast3r_logs/dataset_full.txt" \
        --frames "$output/dataset/frames.csv" \
        --output "$output/trajectory_frames.csv" \
        --dense-output "$output/trajectory_all_frames_interpolated.csv" \
        "${conversion_args[@]}"
    if [[ -f "$output/mast3r_logs/dataset_online.txt" ]]; then
        "$PYTHON" "$ROOT_DIR/scripts/convert_mast3r_slam_trajectory.py" \
            --trajectory "$output/mast3r_logs/dataset_online.txt" \
            --frames "$output/dataset/frames.csv" \
            --output "$output/trajectory_online_frames.csv" \
            --dense-output "$output/trajectory_online_all_frames_interpolated.csv" \
            "${conversion_args[@]}"
    fi
    "$PYTHON" - "$TOOL_DIR" "$output" "$elapsed" "$CHECKPOINT" "$CONFIG" <<'PY'
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

tool = Path(sys.argv[1])
output = Path(sys.argv[2])
elapsed = int(sys.argv[3])
checkpoint = Path(sys.argv[4]).resolve()
config = Path(sys.argv[5]).resolve()

def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

manifest = {
    "schema": "umi_mast3r_run_v1",
    "slam_supervision": False,
    "algorithm": "MASt3R-SLAM monocular visual SLAM",
    "kernel_release": platform.release(),
    "config": str(config),
    "config_sha256": file_sha256(config),
    "toolchain": str(tool),
    "toolchain_commit": subprocess.check_output(
        ["git", "-C", str(tool), "rev-parse", "HEAD"], text=True
    ).strip(),
    "toolchain_dirty_diff_sha256": hashlib.sha256(
        subprocess.check_output(["git", "-C", str(tool), "diff"])
    ).hexdigest(),
    "lietorch_commit": subprocess.check_output(
        ["git", "-C", str(tool / "thirdparty" / "lietorch"), "rev-parse", "HEAD"],
        text=True,
    ).strip(),
    "checkpoint": str(checkpoint),
    "checkpoint_sha256": file_sha256(checkpoint),
    "elapsed_s": elapsed,
    "trajectory": str((output / "trajectory_frames.csv").resolve()),
    "all_frames_interpolated_trajectory": str(
        (output / "trajectory_all_frames_interpolated.csv").resolve()
    ),
}
(output / "run_manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
PY
}

command="${1:-}"
case "$command" in
    run)
        [[ $# -ge 3 ]] || { usage; exit 2; }
        check_installation
        run_mast3r "$(realpath "$2")" "$(realpath -m "$3")" "${4:-color}" "${5:-0}" "${6:-0}"
        ;;
    fusion)
        [[ $# -eq 5 ]] || { usage; exit 2; }
        session="$(realpath "$2")"
        vins_trajectory="$(realpath "$3")"
        vins_report="$(realpath "$4")"
        output="$(realpath -m "$5")"
        resolve_candidate_config "$output"
        warn_if_motion_keyframes_disabled
        check_installation
        mast3r_output="$output/mast3r"
        mkdir -p "$output"

        echo "[1/8] MASt3R左红外视觉轨迹 + 400Hz IMU旋转先验"
        run_mast3r "$session" "$mast3r_output" infrared_left 0 0

        echo "[2/8] D405双红外短时双向尺度"
        "$PYTHON" "$ROOT_DIR/scripts/align_mast3r_scale_with_stereo.py" \
            --session "$session" \
            --trajectory "$mast3r_output/trajectory_frames.csv" \
            --trajectory-frame infrared_left \
            --motion-estimator pnp \
            --frame-step 5 \
            --max-hop 5 \
            --max-depth-m 0.6 \
            --output "$mast3r_output/trajectory_stereo_bidirectional.csv" \
            --report "$mast3r_output/stereo_scale_bidirectional_report.json"

        echo "[3/8] D405双红外中等跨度尺度"
        "$PYTHON" "$ROOT_DIR/scripts/align_mast3r_scale_with_stereo.py" \
            --session "$session" \
            --trajectory "$mast3r_output/trajectory_frames.csv" \
            --trajectory-frame infrared_left \
            --motion-estimator pnp \
            --frame-step 5 \
            --hop-values 8,12,16 \
            --max-depth-m 0.6 \
            --output "$mast3r_output/trajectory_stereo_long_hops.csv" \
            --report "$mast3r_output/stereo_scale_long_hops_report.json"

        echo "[4/8] D405双红外10Hz密集尺度"
        "$PYTHON" "$ROOT_DIR/scripts/align_mast3r_scale_with_stereo.py" \
            --session "$session" \
            --trajectory "$mast3r_output/trajectory_frames.csv" \
            --trajectory-frame infrared_left \
            --motion-estimator pnp \
            --frame-step 3 \
            --max-hop 3 \
            --max-depth-m 0.6 \
            --output "$mast3r_output/trajectory_stereo_dense10hz.csv" \
            --report "$mast3r_output/stereo_scale_dense10hz_report.json"

        echo "[5/8] D405双红外0.8至1.6秒跨窗几何"
        "$PYTHON" "$ROOT_DIR/scripts/align_mast3r_scale_with_stereo.py" \
            --session "$session" \
            --trajectory "$mast3r_output/trajectory_frames.csv" \
            --trajectory-frame infrared_left \
            --motion-estimator pnp \
            --frame-step 5 \
            --hop-values 24,32,48 \
            --max-depth-m 0.6 \
            --output "$mast3r_output/trajectory_stereo_multisecond.csv" \
            --report "$mast3r_output/stereo_scale_multisecond_report.json"

        echo "[6/8] 400Hz IMU米制尺度与偏置"
        "$PYTHON" "$ROOT_DIR/scripts/align_mast3r_scale_with_imu.py" \
            --session "$session" \
            --trajectory "$mast3r_output/trajectory_frames.csv" \
            --stream infrared_left \
            --body-t-camera-yaml "$VINS_CONFIG" \
            --imu-calibration "$IMU_CALIBRATION" \
            --td-s -0.009109323 \
            --node-stride 10 \
            --max-hop 1 \
            --output "$mast3r_output/trajectory_imu_metric.csv" \
            --report "$mast3r_output/imu_scale_report.json"

        echo "[7/8] 多段双目/IMU关键帧图联合优化"
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
            --orientation-node-stride 10 \
            --position-node-stride 5 \
            --minimum-stereo-sample-hop 1 \
            --keyframe-dir "$mast3r_output/mast3r_logs/keyframes/dataset" \
            --relative-motion-trajectory "$vins_trajectory" \
            --relative-motion-report "$vins_report" \
            --relative-motion-sigma-m 0.008 \
            --auto-visual-position-sigma \
            --joint-max-correction-mm 25 \
            --joint-correction-cap-mode per-node \
            --full-rate-imu-position-refinement \
            --full-rate-max-correction-mm 20 \
            --metric-scale-mode joint \
            --position-mode keyframe-graph \
            --output "$mast3r_output/trajectory_graph.csv" \
            --report "$mast3r_output/graph_fusion_report.json"

        echo "[8/9] VINS姿态互补、自动尺度门控与异常平移隔离"
        "$PYTHON" "$ROOT_DIR/scripts/fuse_docker2_mast3r_complementary.py" \
            --mast3r "$mast3r_output/trajectory_graph.csv" \
            --docker2 "$vins_trajectory" \
            --docker2-report "$vins_report" \
            --body-t-camera-yaml "$VINS_CONFIG" \
            --scale-horizon-s 1 \
            --smoothing-s 8 \
            --docker2-local-weight 0.25 \
            --docker2-scale-weight 0.475 \
            --adaptive-local-weight \
            --graph-report "$mast3r_output/graph_fusion_report.json" \
            --roughness-threshold-mm 9 \
            --adaptive-weight-strength 0.45 \
            --use-docker2-orientation-for-lever-arm \
            --output "$output/trajectory_fused_unsmoothed.csv" \
            --report "$output/fusion_report.json"

        echo "[9/9] 独立双目/视觉惯性质量门控与零相位平滑"
        "$PYTHON" "$ROOT_DIR/scripts/assess_mast3r_fusion_input_quality.py" \
            --graph-report "$mast3r_output/graph_fusion_report.json" \
            --fusion-report "$output/fusion_report.json" \
            --output "$output/input_quality_report.json"

        "$PYTHON" "$ROOT_DIR/scripts/smooth_pose_trajectory.py" \
            --input "$output/trajectory_fused_unsmoothed.csv" \
            --output "$output/trajectory_fused.csv" \
            --method gaussian \
            --gaussian-sigma-s 0.025 \
            --report "$output/smoothing_report.json"

        echo "融合后处理完成: $output/trajectory_fused.csv"
        echo "算法阶段未读取机械臂、Tracker或Lighthouse轨迹。"
        ;;
    compare)
        [[ $# -ge 5 ]] || { usage; exit 2; }
        check_installation
        session="$(realpath "$2")"
        docker2="$(realpath "$3")"
        ground_truth="$(realpath "$4")"
        output="$(realpath -m "$5")"
        stream="${6:-color}"
        run_mast3r "$session" "$output/mast3r" "$stream" 0 0
        set +e
        "$PYTHON" "$ROOT_DIR/scripts/align_mast3r_scale_with_stereo.py" \
            --session "$session" \
            --trajectory "$output/mast3r/trajectory_frames.csv" \
            --frame-step 5 \
            --max-hop 5 \
            --max-depth-m 0.6 \
            --output "$output/mast3r/trajectory_stereo.csv" \
            --report "$output/mast3r/stereo_scale_report.json"
        stereo_status=$?
        "$PYTHON" "$ROOT_DIR/scripts/align_mast3r_scale_with_imu.py" \
            --session "$session" \
            --trajectory "$output/mast3r/trajectory_frames.csv" \
            --stream "$stream" \
            --body-t-camera-yaml "$VINS_CONFIG" \
            --imu-calibration "$IMU_CALIBRATION" \
            --td-s -0.009109323 \
            --node-stride 10 \
            --max-hop 1 \
            --output "$output/mast3r/trajectory_imu_metric.csv" \
            --report "$output/mast3r/imu_scale_report.json"
        imu_scale_status=$?
        set -e
        fusion_status=3
        if [[ "$stereo_status" -eq 0 && "$imu_scale_status" -eq 0 ]]; then
            set +e
            "$PYTHON" "$ROOT_DIR/scripts/fuse_mast3r_stereo_imu.py" \
                --session "$session" \
                --trajectory "$output/mast3r/trajectory_imu_metric.csv" \
                --stereo-report "$output/mast3r/stereo_scale_report.json" \
                --imu-scale-report "$output/mast3r/imu_scale_report.json" \
                --vins-config "$VINS_CONFIG" \
                --imu-calibration "$IMU_CALIBRATION" \
                --expected-td-s -0.009109323 \
                --orientation-node-stride 10 \
                --metric-scale-mode stereo \
                --position-mode auto \
                --output "$output/mast3r/trajectory_stereo_imu.csv" \
                --report "$output/mast3r/stereo_imu_fusion_report.json"
            fusion_status=$?
            set -e
        fi
        "$PYTHON" "$ROOT_DIR/scripts/resample_pose_trajectory.py" \
            --source "$docker2" \
            --query "$output/mast3r/trajectory_frames.csv" \
            --output "$output/docker2_common_timestamps.csv" \
            --allow-partial
        "$PYTHON" "$ROOT_DIR/scripts/resample_pose_trajectory.py" \
            --source "$output/mast3r/trajectory_frames.csv" \
            --query "$output/docker2_common_timestamps.csv" \
            --output "$output/mast3r_common_timestamps.csv" \
            --allow-partial
        # The second interpolation can lose a boundary sample to floating-point
        # rounding.  Re-query Docker2 on the final MASt3R timestamps so every
        # report is evaluated on exactly the same sample set.
        "$PYTHON" "$ROOT_DIR/scripts/resample_pose_trajectory.py" \
            --source "$docker2" \
            --query "$output/mast3r_common_timestamps.csv" \
            --output "$output/docker2_common_timestamps.csv"
        if [[ "$stereo_status" -eq 0 ]]; then
            "$PYTHON" "$ROOT_DIR/scripts/resample_pose_trajectory.py" \
                --source "$output/mast3r/trajectory_stereo.csv" \
                --query "$output/docker2_common_timestamps.csv" \
                --output "$output/mast3r_stereo_common_timestamps.csv" \
                --allow-partial
        fi
        if [[ "$fusion_status" -eq 0 ]]; then
            "$PYTHON" "$ROOT_DIR/scripts/resample_pose_trajectory.py" \
                --source "$output/mast3r/trajectory_stereo_imu.csv" \
                --query "$output/docker2_common_timestamps.csv" \
                --output "$output/mast3r_stereo_imu_common_timestamps.csv" \
                --allow-partial
        fi
        set +e
        mast3r_estimate_frame_args=(
            --estimate-camera-to-body-yaml "$VINS_CONFIG"
        )
        if [[ "$stream" == "color" ]]; then
            [[ -f "$output/mast3r/stereo_scale_report.json" ]] || {
                echo "缺少RGB→body所需的D405工厂外参报告" >&2
                exit 2
            }
            mast3r_estimate_frame_args+=(
                --estimate-camera-adjustment-report "$output/mast3r/stereo_scale_report.json"
            )
        fi
        "$PYTHON" "$ROOT_DIR/scripts/evaluate_slam_ground_truth.py" \
            --estimate "$output/docker2_common_timestamps.csv" \
            --ground-truth "$ground_truth" \
            --output "$output/docker2_precision.json" \
            --plot "$output/docker2_precision.png" \
            --report-md "$output/docker2_precision.md"
        docker_status=$?
        "$PYTHON" "$ROOT_DIR/scripts/evaluate_slam_ground_truth.py" \
            --estimate "$output/mast3r_common_timestamps.csv" \
            --ground-truth "$ground_truth" \
            "${mast3r_estimate_frame_args[@]}" \
            --output "$output/mast3r_precision.json" \
            --plot "$output/mast3r_precision.png" \
            --report-md "$output/mast3r_precision.md"
        mast3r_status=$?
        if [[ "$stereo_status" -eq 0 ]]; then
            "$PYTHON" "$ROOT_DIR/scripts/evaluate_slam_ground_truth.py" \
                --estimate "$output/mast3r_stereo_common_timestamps.csv" \
                --ground-truth "$ground_truth" \
                "${mast3r_estimate_frame_args[@]}" \
                --output "$output/mast3r_stereo_precision.json" \
                --plot "$output/mast3r_stereo_precision.png" \
                --report-md "$output/mast3r_stereo_precision.md"
            mast3r_stereo_status=$?
        else
            mast3r_stereo_status=3
        fi
        if [[ "$fusion_status" -eq 0 ]]; then
            "$PYTHON" "$ROOT_DIR/scripts/evaluate_slam_ground_truth.py" \
                --estimate "$output/mast3r_stereo_imu_common_timestamps.csv" \
                --ground-truth "$ground_truth" \
                "${mast3r_estimate_frame_args[@]}" \
                --output "$output/mast3r_stereo_imu_precision.json" \
                --plot "$output/mast3r_stereo_imu_precision.png" \
                --report-md "$output/mast3r_stereo_imu_precision.md"
            fusion_precision_status=$?
        else
            fusion_precision_status=3
        fi
        set -e
        compare_args=(
            --docker2 "$output/docker2_precision.json" \
            --mast3r "$output/mast3r_precision.json" \
            --output "$output/comparison.json" \
            --report-md "$output/comparison.md"
        )
        if [[ "$fusion_status" -eq 0 ]]; then
            compare_args+=(--fusion "$output/mast3r_stereo_imu_precision.json")
        fi
        "$PYTHON" "$ROOT_DIR/scripts/compare_slam_precision_reports.py" "${compare_args[@]}"
        echo "Docker2门禁: $docker_status; MASt3R: $mast3r_status; MASt3R-Stereo求解/精度: $stereo_status/$mast3r_stereo_status; IMU尺度: $imu_scale_status; 第三套融合求解/精度: $fusion_status/$fusion_precision_status"
        echo "对比报告: $output/comparison.md"
        ;;
    *)
        usage
        exit 2
        ;;
esac
