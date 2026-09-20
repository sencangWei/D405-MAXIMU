#!/usr/bin/env bash
# Stage B：把某个 w 的前端输出（`out/<b>/<g>/<arm>/w<W>/trajectory_frames.csv`）
# 走完整尾链 [2/8]→[9/9]，产物落同目录 `tail/`。
#
# [2/8]–[6/8] 的参数逐行抄自 scripts/mast3r_slam_precision_workflow.sh:217-278；
# [7/8]–[9/9] 逐行抄自 rerun_tail_v2_20260920/rerun_one_v2.sh:42-89（那份已是
# 「现役工作流 fusion) 参数」的逐字拷贝）。本脚本不新增任何参数。
# 用法: run_tail.sh <batch> <group> <arm> <w> [tag]
set -uo pipefail
BATCH="$1"; GROUP="$2"; ARM="$3"; W="$4"; TAG="${5:-w$W}"
ROOT=/home/robot/ego_vio_humble
WF="$ROOT/reports/lighthouse_umi_workflow"
HERE="$(cd "$(dirname "$0")" && pwd)"
PY=/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/.venv/bin/python
VINS_CONFIG="/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/formal_runtime_calibration/vins_config.yaml"
IMU_CALIBRATION="$ROOT/config/imu_runtime_accel_calibrated_raw_gyro_20260816.yaml"
TD=-0.009109323

# ★ [2/8]–[6/8] 的 align_mast3r_scale_with_* 要读 session 的 db3（厂内标定话题），
#   依赖 rosbag2_py/rclpy —— 它们不在 MASt3R venv 里，在 /opt/ros/humble
#   （同为 python3.10，可直接进 venv 的 sys.path）。生产那次 shell 必然 source 过。
set +u; source /opt/ros/humble/setup.bash >/dev/null 2>&1; set -u

G="$WF/$BATCH/$GROUP"
M="$HERE/out/$BATCH/$GROUP/$ARM/$TAG"
OUT="$M/tail"
logo() { echo "[$1] $2"; }
[ -f "$M/trajectory_frames.csv" ] || { echo "❌ 无前端产物 $M/trajectory_frames.csv"; exit 3; }
mkdir -p "$OUT"

SES=$($PY -c "import json;from pathlib import Path;print(Path(json.load(open('$G/lighthouse_ground_truth_provenance.json'))['clock_mapping']['d405_frames']).parent)")
VINS_TRAJ=$($PY -c "
import sys; sys.path.insert(0,'/home/robot/ego_vio_humble/reports/mast3r_g2_validation_20260919/rerun_tail_v2_20260920/imu_rot_w/..')
from pathlib import Path
from vins_dir import pick_vins_dir
r = pick_vins_dir(Path('$G'))
print(r[0] if r else '')")
VINS_REPORT="$([ -n "$VINS_TRAJ" ] && echo "$(dirname "$VINS_TRAJ")/run_acceptance.json")"
[ -n "$VINS_TRAJ" ] || { echo "❌ 无验收 PASS 的 VINS 产物"; exit 6; }
echo "cell=$BATCH/$GROUP/$ARM/w$W  VINS=$(basename "$(dirname "$VINS_TRAJ")")"

stereo() {  # $1=tag $2..=extra args
    local tag="$1"; shift
    logo "$tag" "D405双红外尺度"
    "$PY" "$ROOT/scripts/align_mast3r_scale_with_stereo.py" \
        --session "$SES" --trajectory "$M/trajectory_frames.csv" \
        --trajectory-frame infrared_left --motion-estimator pnp \
        --max-depth-m 0.6 --output "$M/trajectory_stereo_$tag.csv" \
        --report "$M/stereo_scale_$tag""_report.json" "$@" > "$OUT/$tag.log" 2>&1 \
        || { echo "❌ $tag rc=$?"; tail -3 "$OUT/$tag.log"; exit 4; }
}
stereo bidirectional --frame-step 5 --max-hop 5
stereo long_hops     --frame-step 5 --hop-values 8,12,16
stereo dense10hz     --frame-step 3 --max-hop 3
stereo multisecond   --frame-step 5 --hop-values 24,32,48

logo "[6/8]" "400Hz IMU米制尺度"
"$PY" "$ROOT/scripts/align_mast3r_scale_with_imu.py" \
    --session "$SES" --trajectory "$M/trajectory_frames.csv" --stream infrared_left \
    --body-t-camera-yaml "$VINS_CONFIG" --imu-calibration "$IMU_CALIBRATION" \
    --td-s "$TD" --node-stride 10 --max-hop 1 \
    --output "$M/trajectory_imu_metric.csv" --report "$M/imu_scale_report.json" \
    > "$OUT/imu.log" 2>&1 || { echo "❌ [6/8] rc=$?"; tail -3 "$OUT/imu.log"; exit 6; }

logo "[7/8]" "多段双目/IMU关键帧图联合优化"
"$PY" "$ROOT/scripts/fuse_mast3r_stereo_imu.py" \
    --session "$SES" --trajectory "$M/trajectory_imu_metric.csv" --stream infrared_left \
    --stereo-report "$M/stereo_scale_bidirectional_report.json" \
    --additional-stereo-report "$M/stereo_scale_long_hops_report.json" \
    --additional-stereo-report "$M/stereo_scale_dense10hz_report.json" \
    --additional-stereo-report "$M/stereo_scale_multisecond_report.json" \
    --imu-scale-report "$M/imu_scale_report.json" \
    --vins-config "$VINS_CONFIG" --imu-calibration "$IMU_CALIBRATION" \
    --expected-td-s "$TD" \
    --orientation-node-stride 10 --position-node-stride 5 --minimum-stereo-sample-hop 1 \
    --keyframe-dir "$M/mast3r_logs/keyframes/dataset" \
    --relative-motion-trajectory "$VINS_TRAJ" --relative-motion-report "$VINS_REPORT" \
    --relative-motion-sigma-m 0.008 --auto-visual-position-sigma \
    --joint-max-correction-mm 25 --joint-correction-cap-mode per-node \
    --full-rate-imu-position-refinement --full-rate-max-correction-mm 20 \
    --metric-scale-mode joint --position-mode keyframe-graph \
    --output "$OUT/trajectory_graph.csv" --report "$OUT/graph_fusion_report.json" \
    > "$OUT/graph.log" 2>&1 || { echo "❌ [7/8] rc=$?"; tail -5 "$OUT/graph.log"; exit 7; }

logo "[8/9]" "VINS姿态互补"
"$PY" "$ROOT/scripts/fuse_docker2_mast3r_complementary.py" \
    --mast3r "$OUT/trajectory_graph.csv" \
    --docker2 "$VINS_TRAJ" --docker2-report "$VINS_REPORT" \
    --body-t-camera-yaml "$VINS_CONFIG" \
    --scale-horizon-s 1 --smoothing-s 8 \
    --docker2-local-weight 0 --docker2-scale-weight 0.25 \
    --graph-report "$OUT/graph_fusion_report.json" \
    --roughness-threshold-mm 9 --adaptive-weight-strength 0.45 \
    --output "$OUT/trajectory_fused_unsmoothed.csv" --report "$OUT/fusion_report.json" \
    > "$OUT/fuse.log" 2>&1 || { echo "❌ [8/9] rc=$?"; tail -5 "$OUT/fuse.log"; exit 8; }

logo "[9/9]" "质量门 + 平滑"
"$PY" "$ROOT/scripts/assess_mast3r_fusion_input_quality.py" \
    --graph-report "$OUT/graph_fusion_report.json" --fusion-report "$OUT/fusion_report.json" \
    --output "$OUT/input_quality_report.json" > "$OUT/quality.log" 2>&1 || echo "⚠ [9/9] 质量门 rc=$?"
"$PY" "$ROOT/scripts/smooth_pose_trajectory.py" \
    --input "$OUT/trajectory_fused_unsmoothed.csv" --output "$OUT/trajectory_fused.csv" \
    --method gaussian --gaussian-sigma-s 0.025 --report "$OUT/smoothing_report.json" \
    > "$OUT/smooth.log" 2>&1 || { echo "❌ [9/9] 平滑 rc=$?"; exit 9; }

logo "[eval]" "官方评测器（融合链）"
"$PY" "$ROOT/scripts/evaluate_slam_ground_truth.py" \
    --estimate "$OUT/trajectory_fused.csv" --ground-truth "$G/lighthouse_body_ground_truth.csv" \
    --output "$OUT/precision.json" --report-md "$OUT/precision.md" \
    > "$OUT/eval.log" 2>&1 || true
$PY -c "
import json;d=json.load(open('$OUT/precision.json'))
print('  rmse %.3f  p95 %.3f  max %.3f  w10 %.3f%%  rot %.3f  => %s %s'%(
 d['ate_translation_rmse_m']*1000,d['ate_translation_p95_m']*1000,d['ate_translation_max_m']*1000,
 d['ate_translation_within_10mm_ratio']*100,d['ate_rotation_rmse_deg'],d['result'],d['failures']))"
echo "✅ $BATCH/$GROUP/$ARM w=$W 尾链完成"