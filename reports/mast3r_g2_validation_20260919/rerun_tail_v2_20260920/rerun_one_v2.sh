#!/usr/bin/env bash
# 端到端验证：用【现役工作流 `fusion)` 里逐字拷贝的参数】重跑尾段 [7/8]→[9/9]。
#
# 与 rerun_tail_current_20260920/rerun_one.sh 的区别（就这几条，全是本次要验的对象）：
#   [8/9] --docker2-scale-weight 0.25（原 0.475）、去掉 --adaptive-local-weight、
#         去掉 --use-docker2-orientation-for-lever-arm
#   [9/9] 质量门**不再 `|| true`** —— 工作流是 `set -e`，脚本非零退出会真的中止；
#         端到端验证必须保留这个行为，否则验不出「生产路径会不会被阻断」。
#
# 参数来源：scripts/mast3r_slam_precision_workflow.sh:279-345（逐行抄，不改）。
# 产物落 <group>/${OUT_SUBDIR:-fusion_v2}/<subset>/，不碰任何旧产物。
#   ★ OUT_SUBDIR：`vins_dir` 修复（18:36）前失败的那几格产物在 fusion_v2/，
#     修复后重跑落 fusion_v3/，两处并排即为「台架选错目录 ⇒ 零产物」的 A/B 证据。
# 用法: OUT_SUBDIR=fusion_v3 rerun_one_v2.sh <batch> <group> <subset>
set -uo pipefail
BATCH="$1"; GROUP="$2"; SUB="$3"
ROOT=/home/robot/ego_vio_humble
WF="$ROOT/reports/lighthouse_umi_workflow"
PY=/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/.venv/bin/python
VINS_CONFIG="/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/formal_runtime_calibration/vins_config.yaml"
IMU_CALIBRATION="$ROOT/config/imu_runtime_accel_calibrated_raw_gyro_20260816.yaml"

G="$WF/$BATCH/$GROUP"
SRC="$G/fusion/$SUB/mast3r"
OUT="$G/${OUT_SUBDIR:-fusion_v2}/$SUB"
mkdir -p "$OUT/mast3r"

SES=$($PY -c "import json,sys;from pathlib import Path;print(Path(json.load(open('$G/lighthouse_ground_truth_provenance.json'))['clock_mapping']['d405_frames']).parent)")
# ★ 不要写死 docker2_slam/：holdout_batch2 的两条 cell 在 docker2_slam/ 里一个是
#   FAIL(['corrected_trajectory_jump'])、一个是空目录，09-14 实际用的是
#   docker2_slam_rate0p5/（PASS）。规则见 vins_dir.py。
VINS_TRAJ=$($PY -c "
import sys; sys.path.insert(0,'$(dirname "$0")')
from pathlib import Path
from vins_dir import pick_vins_dir
r = pick_vins_dir(Path('$G'))
print(r[0] if r else '')")
VINS_REPORT="$([ -n "$VINS_TRAJ" ] && echo "$(dirname "$VINS_TRAJ")/run_acceptance.json")"
[ -n "$VINS_TRAJ" ] || { echo "❌ 该 cell 没有任何验收 PASS 的 VINS 产物"; exit 6; }
GT="$G/lighthouse_body_ground_truth.csv"
echo "cell=$BATCH/$GROUP/$SUB  session=$SES"
echo "  VINS=$(basename "$(dirname "$VINS_TRAJ")")"

echo "[7/8] 多段双目/IMU关键帧图联合优化"
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
    > "$OUT/mast3r/graph.log" 2>&1 || { rc=$?; echo "❌ [7/8] rc=$rc"; tail -5 "$OUT/mast3r/graph.log"; exit 7; }

echo "[8/9] VINS姿态互补 (lw 0 / sw 0.25 / 无 adaptive / 无姿态开关)"
"$PY" "$ROOT/scripts/fuse_docker2_mast3r_complementary.py" \
    --mast3r "$OUT/mast3r/trajectory_graph.csv" \
    --docker2 "$VINS_TRAJ" --docker2-report "$VINS_REPORT" \
    --body-t-camera-yaml "$VINS_CONFIG" \
    --scale-horizon-s 1 --smoothing-s 8 \
    --docker2-local-weight 0 --docker2-scale-weight 0.25 \
    --graph-report "$OUT/mast3r/graph_fusion_report.json" \
    --roughness-threshold-mm 9 --adaptive-weight-strength 0.45 \
    --output "$OUT/trajectory_fused_unsmoothed.csv" --report "$OUT/fusion_report.json" \
    > "$OUT/fuse.log" 2>&1 || { rc=$?; echo "❌ [8/9] rc=$rc"; tail -5 "$OUT/fuse.log"; exit 8; }

echo "[9/9] 质量门 + 平滑（★ 不带 || true，与工作流 set -e 一致）"
"$PY" "$ROOT/scripts/assess_mast3r_fusion_input_quality.py" \
    --graph-report "$OUT/mast3r/graph_fusion_report.json" \
    --fusion-report "$OUT/fusion_report.json" \
    --output "$OUT/input_quality_report.json" > "$OUT/quality.log" 2>&1 \
    || { rc=$?; echo "❌ [9/9] 质量门 rc=$rc（生产路径 set -e 会在这里中止）"; exit 91; }
"$PY" "$ROOT/scripts/smooth_pose_trajectory.py" \
    --input "$OUT/trajectory_fused_unsmoothed.csv" --output "$OUT/trajectory_fused.csv" \
    --method gaussian --gaussian-sigma-s 0.025 --report "$OUT/smoothing_report.json" \
    > "$OUT/smooth.log" 2>&1 || { rc=$?; echo "❌ [9/9] 平滑 rc=$rc"; exit 9; }

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