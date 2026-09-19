#!/usr/bin/env bash
# 用【现役生产参数】重跑一条 cell 的尾段 [7/8]→[9/9]，写进 <group>/fusion_current/<subset>/，不碰任何旧产物。
# 用法: rerun_one.sh <batch> <group> <subset>
set -uo pipefail
BATCH="$1"; GROUP="$2"; SUB="$3"
ROOT=/home/robot/ego_vio_humble
WF="$ROOT/reports/lighthouse_umi_workflow"
PY=/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/.venv/bin/python
VINS_CONFIG="/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/formal_runtime_calibration/vins_config.yaml"
IMU_CALIBRATION="$ROOT/config/imu_runtime_accel_calibrated_raw_gyro_20260816.yaml"

G="$WF/$BATCH/$GROUP"
SRC="$G/fusion/$SUB/mast3r"
OUT="$G/fusion_current/$SUB"
mkdir -p "$OUT/mast3r"

SES=$($PY -c "import json,sys;from pathlib import Path;print(Path(json.load(open('$G/lighthouse_ground_truth_provenance.json'))['clock_mapping']['d405_frames']).parent)")
VINS_TRAJ="$G/docker2_slam/vio_corrected_stream.csv"
VINS_REPORT="$G/docker2_slam/run_acceptance.json"
GT="$G/lighthouse_body_ground_truth.csv"
echo "cell=$BATCH/$GROUP/$SUB  session=$SES"

echo "[7/8] 图联合优化 (per-node cap)"
"$PY" "$ROOT/scripts/fuse_mast3r_stereo_imu.py" \
    --session "$SES" --trajectory "$SRC/trajectory_imu_metric.csv" --stream infrared_left \
    --stereo-report "$SRC/stereo_scale_bidirectional_report.json" \
    --additional-stereo-report "$SRC/stereo_scale_long_hops_report.json" \
    --additional-stereo-report "$SRC/stereo_scale_dense10hz_report.json" \
    --additional-stereo-report "$SRC/stereo_scale_multisecond_report.json" \
    --imu-scale-report "$SRC/imu_scale_report.json" \
    --vins-config "$VINS_CONFIG" --imu-calibration "$IMU_CALIBRATION" \
    --expected-td-s -0.009109323 \
    --orientation-node-stride 10 --position-node-stride 5 --minimum-stereo-sample-hop 1 \
    --keyframe-dir "$SRC/mast3r_logs/keyframes/dataset" \
    --relative-motion-trajectory "$VINS_TRAJ" --relative-motion-report "$VINS_REPORT" \
    --relative-motion-sigma-m 0.008 --auto-visual-position-sigma \
    --joint-max-correction-mm 25 --joint-correction-cap-mode per-node \
    --full-rate-imu-position-refinement --full-rate-max-correction-mm 20 \
    --metric-scale-mode joint --position-mode keyframe-graph \
    --output "$OUT/mast3r/trajectory_graph.csv" --report "$OUT/mast3r/graph_fusion_report.json" \
    > "$OUT/mast3r/graph.log" 2>&1 || { echo "❌ [7/8] rc=$?"; exit 7; }

echo "[8/9] VINS 互补融合 (lw 0.25 / sw 0.475 / adaptive / smo 8)"
"$PY" "$ROOT/scripts/fuse_docker2_mast3r_complementary.py" \
    --mast3r "$OUT/mast3r/trajectory_graph.csv" \
    --docker2 "$VINS_TRAJ" --docker2-report "$VINS_REPORT" \
    --body-t-camera-yaml "$VINS_CONFIG" \
    --scale-horizon-s 1 --smoothing-s 8 \
    --docker2-local-weight 0.25 --docker2-scale-weight 0.475 --adaptive-local-weight \
    --graph-report "$OUT/mast3r/graph_fusion_report.json" \
    --roughness-threshold-mm 9 --adaptive-weight-strength 0.45 \
    --use-docker2-orientation-for-lever-arm \
    --output "$OUT/trajectory_fused_unsmoothed.csv" --report "$OUT/fusion_report.json" \
    > "$OUT/fuse.log" 2>&1 || { echo "❌ [8/9] rc=$?"; exit 8; }

echo "[9/9] 平滑 + 质量门"
"$PY" "$ROOT/scripts/assess_mast3r_fusion_input_quality.py" \
    --graph-report "$OUT/mast3r/graph_fusion_report.json" \
    --fusion-report "$OUT/fusion_report.json" \
    --output "$OUT/input_quality_report.json" > "$OUT/quality.log" 2>&1 || true
"$PY" "$ROOT/scripts/smooth_pose_trajectory.py" \
    --input "$OUT/trajectory_fused_unsmoothed.csv" --output "$OUT/trajectory_fused.csv" \
    --method gaussian --gaussian-sigma-s 0.025 --report "$OUT/smoothing_report.json" \
    > "$OUT/smooth.log" 2>&1 || { echo "❌ [9/9] rc=$?"; exit 9; }

echo "[eval] 官方评测器"
"$PY" "$ROOT/scripts/evaluate_slam_ground_truth.py" \
    --estimate "$OUT/trajectory_fused.csv" --ground-truth "$GT" \
    --output "$OUT/precision.json" --plot "$OUT/precision.png" --report-md "$OUT/precision.md" \
    > "$OUT/eval.log" 2>&1 || true
$PY -c "
import json;d=json.load(open('$OUT/precision.json'))
print('  rmse %.3f  p95 %.3f  max %.3f  w10 %.3f%%  rot %.3f  => %s %s'%(
 d['ate_translation_rmse_m']*1000,d['ate_translation_p95_m']*1000,d['ate_translation_max_m']*1000,
 d['ate_translation_within_10mm_ratio']*100,d['ate_rotation_rmse_deg'],d['result'],d['failures']))"
echo "✅ $BATCH/$GROUP/$SUB 完成"
