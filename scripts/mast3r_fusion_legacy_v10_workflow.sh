#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TOOL_DIR="/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM-legacy-v10"
PYTHON="$TOOL_DIR/.venv/bin/python"
VINS_CONFIG="/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/formal_runtime_calibration/vins_config.yaml"
IMU_CALIBRATION="$ROOT_DIR/config/imu_runtime_accel_calibrated_raw_gyro_20260816.yaml"

usage() {
    echo "用法: $0 <D405会话目录> <Docker2/VINS轨迹.csv> <输出目录>" >&2
}

[[ $# -eq 3 ]] || { usage; exit 2; }
SESSION="$(realpath "$1")"
VINS_TRAJECTORY="$(realpath "$2")"
OUTPUT="$(realpath -m "$3")"

[[ -f "$SESSION/d405_frames.csv" ]] || {
    echo "D405会话无效: $SESSION" >&2
    exit 2
}
[[ -f "$VINS_TRAJECTORY" ]] || {
    echo "Docker2/VINS轨迹不存在: $VINS_TRAJECTORY" >&2
    exit 2
}

mkdir -p "$OUTPUT"
"$PYTHON" "$ROOT_DIR/scripts/verify_mast3r_fusion_legacy_v10.py" \
    --output "$OUTPUT/legacy_v10_verification.json"

run_candidate() (
    set -euo pipefail
    local name="$1"
    local config="$2"
    local smoothing_s="$3"
    local local_weight="$4"
    local scale_weight="$5"
    local candidate="$OUTPUT/$name"
    local frontend="$candidate/frontend"
    mkdir -p "$candidate"

    echo "[$name 1/6] MASt3R视觉前端 + 400 Hz IMU旋转先验"
    MAST3R_SLAM_DIR="$TOOL_DIR" \
    MAST3R_SLAM_CONFIG="$ROOT_DIR/$config" \
        "$ROOT_DIR/scripts/mast3r_slam_precision_workflow.sh" run \
        "$SESSION" "$frontend" infrared_left 0 0

    echo "[$name 2/6] D405双红外短窗尺度"
    "$PYTHON" "$ROOT_DIR/scripts/align_mast3r_scale_with_stereo.py" \
        --session "$SESSION" \
        --trajectory "$frontend/trajectory_frames.csv" \
        --trajectory-frame infrared_left \
        --motion-estimator pnp \
        --frame-step 5 \
        --max-hop 5 \
        --max-depth-m 0.6 \
        --output "$candidate/trajectory_stereo_bidirectional.csv" \
        --report "$candidate/stereo_scale_bidirectional_report.json"

    echo "[$name 3/6] D405双红外8/12/16帧跨窗尺度"
    "$PYTHON" "$ROOT_DIR/scripts/align_mast3r_scale_with_stereo.py" \
        --session "$SESSION" \
        --trajectory "$frontend/trajectory_frames.csv" \
        --trajectory-frame infrared_left \
        --motion-estimator pnp \
        --frame-step 5 \
        --hop-values 8,12,16 \
        --max-depth-m 0.6 \
        --output "$candidate/trajectory_stereo_long_hops.csv" \
        --report "$candidate/stereo_scale_long_hops_report.json"

    echo "[$name 4/6] 400 Hz IMU米制尺度和偏置"
    "$PYTHON" "$ROOT_DIR/scripts/align_mast3r_scale_with_imu.py" \
        --session "$SESSION" \
        --trajectory "$frontend/trajectory_frames.csv" \
        --stream infrared_left \
        --body-t-camera-yaml "$VINS_CONFIG" \
        --imu-calibration "$IMU_CALIBRATION" \
        --td-s -0.009109323 \
        --node-stride 10 \
        --max-hop 1 \
        --output "$candidate/trajectory_imu_metric.csv" \
        --report "$candidate/imu_scale_report.json"

    echo "[$name 5/6] v10双目/IMU/短时相对运动图优化"
    "$PYTHON" "$ROOT_DIR/scripts/fuse_mast3r_stereo_imu_legacy_v10.py" \
        --session "$SESSION" \
        --trajectory "$candidate/trajectory_imu_metric.csv" \
        --stream infrared_left \
        --stereo-report "$candidate/stereo_scale_bidirectional_report.json" \
        --additional-stereo-report "$candidate/stereo_scale_long_hops_report.json" \
        --imu-scale-report "$candidate/imu_scale_report.json" \
        --vins-config "$VINS_CONFIG" \
        --imu-calibration "$IMU_CALIBRATION" \
        --expected-td-s -0.009109323 \
        --orientation-node-stride 10 \
        --position-node-stride 10 \
        --minimum-stereo-sample-hop 1 \
        --keyframe-dir "$frontend/mast3r_logs/keyframes/dataset" \
        --relative-motion-trajectory "$VINS_TRAJECTORY" \
        --relative-motion-sigma-m 0.012 \
        --full-rate-imu-position-refinement \
        --full-rate-max-correction-mm 20 \
        --metric-scale-mode stereo \
        --position-mode keyframe-graph \
        --output "$candidate/trajectory_graph.csv" \
        --report "$candidate/graph_report.json"

    echo "[$name 6/6] v10长期形状与VINS短时运动互补"
    "$PYTHON" "$ROOT_DIR/scripts/fuse_docker2_mast3r_complementary_legacy_v10.py" \
        --mast3r "$candidate/trajectory_graph.csv" \
        --docker2 "$VINS_TRAJECTORY" \
        --body-t-camera-yaml "$VINS_CONFIG" \
        --scale-horizon-s 1 \
        --smoothing-s "$smoothing_s" \
        --docker2-local-weight "$local_weight" \
        --docker2-scale-weight "$scale_weight" \
        --adaptive-local-weight \
        --roughness-threshold-mm 9 \
        --adaptive-weight-strength 0.45 \
        --use-docker2-orientation-for-lever-arm \
        --output "$candidate/trajectory_fused.csv" \
        --report "$candidate/fusion_report.json"
)

run_candidate \
    sparse \
    config/mast3r_slam_d405_offline_motion_kf.yaml \
    8.0 0.20 0.475

tight_available=true
if ! run_candidate \
    tight \
    config/mast3r_slam_d405_offline_motion_kf_tight.yaml \
    15.0 0.35 0.85; then
    tight_available=false
    echo "密关键帧候选内部质量门禁失败，按v10策略退回稀疏候选。" >&2
fi

"$PYTHON" - \
    "$OUTPUT" \
    "$tight_available" <<'PY'
import json
import shutil
import sys
from pathlib import Path

root = Path(sys.argv[1])
tight_available = sys.argv[2].lower() == "true"
sparse_graph_path = root / "sparse" / "graph_report.json"
sparse_fusion_path = root / "sparse" / "fusion_report.json"
sparse_graph = json.loads(sparse_graph_path.read_text(encoding="utf-8"))
sparse_fusion = json.loads(sparse_fusion_path.read_text(encoding="utf-8"))

if sparse_graph.get("result") != "PASS" or sparse_graph.get("slam_supervision"):
    raise SystemExit("稀疏候选图优化未通过内部质量门禁")
if sparse_graph.get("inputs", {}).get("external_ground_truth_used") is not False:
    raise SystemExit("稀疏候选没有证明外部真值未参与")
if sparse_fusion.get("result") != "PASS" or sparse_fusion.get("external_ground_truth_used") is not False:
    raise SystemExit("稀疏候选互补融合未通过无监督门禁")

scale_difference = float(
    sparse_graph["metric_scale_consistency"]["relative_difference"]
)
sparse_rmse = float(
    sparse_graph["stereo_translation_fusion"]["stereo_edge_rmse_after_m"]
)
selected = "sparse"
evidence = {
    "sparse_stereo_imu_scale_relative_difference": scale_difference,
    "maximum_scale_relative_difference": 0.15,
    "scale_is_observable": scale_difference <= 0.15,
    "sparse_stereo_edge_rmse_after_m": sparse_rmse,
    "tight_available": tight_available,
}

if tight_available:
    tight_graph_path = root / "tight" / "graph_report.json"
    tight_fusion_path = root / "tight" / "fusion_report.json"
    tight_graph = json.loads(tight_graph_path.read_text(encoding="utf-8"))
    tight_fusion = json.loads(tight_fusion_path.read_text(encoding="utf-8"))
    tight_valid = (
        tight_graph.get("result") == "PASS"
        and not tight_graph.get("slam_supervision")
        and tight_graph.get("inputs", {}).get("external_ground_truth_used") is False
        and tight_fusion.get("result") == "PASS"
        and tight_fusion.get("external_ground_truth_used") is False
    )
    tight_rmse = float(
        tight_graph["stereo_translation_fusion"]["stereo_edge_rmse_after_m"]
    )
    improvement = (sparse_rmse - tight_rmse) / sparse_rmse
    evidence.update(
        {
            "tight_valid": tight_valid,
            "tight_stereo_edge_rmse_after_m": tight_rmse,
            "tight_stereo_edge_rmse_improvement_ratio": improvement,
            "minimum_tight_stereo_improvement_ratio": 0.10,
            "tight_improves_geometry": improvement >= 0.10,
        }
    )
    if tight_valid and scale_difference <= 0.15 and improvement >= 0.10:
        selected = "tight"

source = root / selected / "trajectory_fused.csv"
output = root / "trajectory_fused.csv"
shutil.copyfile(source, output)
report = {
    "schema": "umi_mast3r_fusion_candidate_selection_v1",
    "result": "PASS",
    "profile": "legacy_v10_20260912",
    "slam_supervision": False,
    "external_ground_truth_used": False,
    "selection_policy": (
        "tight only when onboard stereo/IMU scale is observable and tight "
        "keyframes materially reduce stereo edge residual"
    ),
    "selected_candidate": selected,
    "evidence": evidence,
    "output": str(output.resolve()),
}
(root / "selection_report.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps(report, ensure_ascii=False, indent=2))
PY

echo "v10融合轨迹: $OUTPUT/trajectory_fused.csv"
echo "选择报告: $OUTPUT/selection_report.json"
echo "算法输入仅包含D405双红外、UMI 400 Hz IMU、MASt3R和Docker2/VINS轨迹。"
