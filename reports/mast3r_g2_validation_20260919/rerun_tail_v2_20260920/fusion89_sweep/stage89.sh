#!/usr/bin/env bash
# 快 harness：只重跑 [8/9]+[9/9]+eval。
# 依据：`fusion/<arm>/mast3r/trajectory_graph.csv`（=[7/8] 输出）在
#   09-14 产线 / 09-20 fusion_current / 本次 harness **三族逐字节相同**
#   （md5 310e0997060c34），⇒ 分歧 100% 在 [8/9]，上游不必重跑。
#
# 用法: stage89.sh <batch> <group> <arm> <tag>
set -uo pipefail
BATCH="$1"; GROUP="$2"; ARM="$3"; TAG="$4"
ROOT=/home/robot/ego_vio_humble
WF="$ROOT/reports/lighthouse_umi_workflow"
HERE="$(cd "$(dirname "$0")" && pwd)"
PY=/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/.venv/bin/python
VINS_CONFIG="/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/formal_runtime_calibration/vins_config.yaml"

G="$WF/$BATCH/$GROUP"
SRC="$G/fusion/$ARM/mast3r"
OUT="$HERE/out89/$BATCH/$GROUP/$ARM/$TAG"
[ -f "$SRC/trajectory_graph.csv" ] || { echo "❌ 无 graph: $SRC"; exit 3; }
[ -f "$OUT/precision.json" ] && { echo "skip $BATCH/$GROUP/$ARM/$TAG"; exit 0; }
mkdir -p "$OUT/mast3r"

# [7/8] 产物原样搬过来（不改一个字节）
cp -f "$SRC/trajectory_graph.csv"      "$OUT/mast3r/"
cp -f "$SRC/graph_fusion_report.json"  "$OUT/mast3r/"

VINS_TRAJ="$G/docker2_slam/vio_corrected_stream.csv"
VINS_REPORT="$G/docker2_slam/run_acceptance.json"
GT="$G/lighthouse_body_ground_truth.csv"

# ---- 七个臂：[8/9] 参数（其余逐字相同）----
# A = 现役 workflow(mast3r_slam_precision_workflow.sh:319-332) 的逐字参数
BASE=(--scale-horizon-s 1 --smoothing-s 8
      --docker2-local-weight 0 --docker2-scale-weight 0.25
      --roughness-threshold-mm 9 --adaptive-weight-strength 0.45)
SMO=8
case "$TAG" in
  A)      EXTRA=() ;;                                                  # 现役基线
  B)      EXTRA=(--use-docker2-orientation-for-lever-arm) ;;                    # +姿态杠杆
  C)      EXTRA=(--adaptive-local-weight) ;;                                    # +自适应
  D)      EXTRA=(); BASE=(--scale-horizon-s 1 --smoothing-s 8 --docker2-local-weight 0.25 \
            --docker2-scale-weight 0.25 --roughness-threshold-mm 9 --adaptive-weight-strength 0.45) ;;
  E)      EXTRA=(); BASE=(--scale-horizon-s 1 --smoothing-s 8 --docker2-local-weight 0 \
            --docker2-scale-weight 0.475 --roughness-threshold-mm 9 --adaptive-weight-strength 0.45) ;;
  F)      BASE=(--scale-horizon-s 1 --smoothing-s 8 --docker2-local-weight 0.25 \
            --docker2-scale-weight 0.475 --roughness-threshold-mm 9 --adaptive-weight-strength 0.45)
          EXTRA=(--adaptive-local-weight --use-docker2-orientation-for-lever-arm) ;;  # = 09-20 整臂
  G)      BASE=(--scale-horizon-s 1 --smoothing-s 15 --docker2-local-weight 0.35 \
            --docker2-scale-weight 0.85 --roughness-threshold-mm 9 --adaptive-weight-strength 0.45)
          SMO=15
          EXTRA=(--adaptive-local-weight --use-docker2-orientation-for-lever-arm) ;;  # = 09-14 整臂
  *)      echo "❌ 未知 tag $TAG"; exit 2 ;;
esac

"$PY" "$ROOT/scripts/fuse_docker2_mast3r_complementary.py" \
    --mast3r "$OUT/mast3r/trajectory_graph.csv" \
    --docker2 "$VINS_TRAJ" --docker2-report "$VINS_REPORT" \
    --body-t-camera-yaml "$VINS_CONFIG" \
    "${BASE[@]}" "${EXTRA[@]}" \
    --graph-report "$OUT/mast3r/graph_fusion_report.json" \
    --output "$OUT/trajectory_fused_unsmoothed.csv" --report "$OUT/fusion_report.json" \
    > "$OUT/fuse.log" 2>&1 || { echo "❌ [8/9] rc=$? $BATCH/$GROUP/$ARM/$TAG"; tail -4 "$OUT/fuse.log"; exit 8; }

"$PY" "$ROOT/scripts/assess_mast3r_fusion_input_quality.py" \
    --graph-report "$OUT/mast3r/graph_fusion_report.json" --fusion-report "$OUT/fusion_report.json" \
    --output "$OUT/input_quality_report.json" > "$OUT/quality.log" 2>&1 || true
"$PY" "$ROOT/scripts/smooth_pose_trajectory.py" \
    --input "$OUT/trajectory_fused_unsmoothed.csv" --output "$OUT/trajectory_fused.csv" \
    --method gaussian --gaussian-sigma-s 0.025 --report "$OUT/smoothing_report.json" \
    > "$OUT/smooth.log" 2>&1 || { echo "❌ [9/9] rc=$?"; exit 9; }

"$PY" "$ROOT/scripts/evaluate_slam_ground_truth.py" \
    --estimate "$OUT/trajectory_fused.csv" --ground-truth "$GT" \
    --output "$OUT/precision.json" --report-md "$OUT/precision.md" \
    > "$OUT/eval.log" 2>&1 || true
"$PY" -c "
import json;d=json.load(open('$OUT/precision.json'))
print('  %-46s %-6s rmse %6.3f p95 %7.3f max %7.3f w10 %7.3f%% rot %5.3f => %s %s'%(
 '$BATCH/$GROUP/$ARM'[:46],'$TAG',
 d['ate_translation_rmse_m']*1000,d['ate_translation_p95_m']*1000,d['ate_translation_max_m']*1000,
 d['ate_translation_within_10mm_ratio']*100,d['ate_rotation_rmse_deg'],d['result'],d['failures']))"