#!/usr/bin/env bash
# 参数化版：把 [8/9] 的 lw / sw / smoothing-s 当自变量扫（每格 ~1s）。
# 用法: stage89p.sh <batch> <group> <arm> <lw> <sw> <smo> [extra...]
set -uo pipefail
BATCH="$1"; GROUP="$2"; ARM="$3"; LW="$4"; SW="$5"; SMO="$6"; shift 6
ROOT=/home/robot/ego_vio_humble
WF="$ROOT/reports/lighthouse_umi_workflow"
HERE="$(cd "$(dirname "$0")" && pwd)"
PY=/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/.venv/bin/python
VINS_CONFIG="/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/formal_runtime_calibration/vins_config.yaml"

G="$WF/$BATCH/$GROUP"
SRC="$G/fusion/$ARM/mast3r"
TAG="lw${LW}_sw${SW}_s${SMO}$([ $# -gt 0 ] && echo "_$(echo "$*"|tr ' -' '__')")"
OUT="$HERE/out89p/$BATCH/$GROUP/$ARM/$TAG"
[ -f "$SRC/trajectory_graph.csv" ] || exit 3
[ -f "$OUT/precision.json" ] && exit 0
mkdir -p "$OUT/mast3r"
cp -f "$SRC/trajectory_graph.csv"     "$OUT/mast3r/"
cp -f "$SRC/graph_fusion_report.json" "$OUT/mast3r/"

"$PY" "$ROOT/scripts/fuse_docker2_mast3r_complementary.py" \
    --mast3r "$OUT/mast3r/trajectory_graph.csv" \
    --docker2 "$G/docker2_slam/vio_corrected_stream.csv" \
    --docker2-report "$G/docker2_slam/run_acceptance.json" \
    --body-t-camera-yaml "$VINS_CONFIG" \
    --scale-horizon-s 1 --smoothing-s "$SMO" \
    --docker2-local-weight "$LW" --docker2-scale-weight "$SW" \
    --roughness-threshold-mm 9 --adaptive-weight-strength 0.45 \
    --graph-report "$OUT/mast3r/graph_fusion_report.json" "$@" \
    --output "$OUT/trajectory_fused_unsmoothed.csv" --report "$OUT/fusion_report.json" \
    > "$OUT/fuse.log" 2>&1 || { echo "❌ [8/9] $BATCH/$GROUP/$ARM $TAG"; exit 8; }
"$PY" "$ROOT/scripts/smooth_pose_trajectory.py" \
    --input "$OUT/trajectory_fused_unsmoothed.csv" --output "$OUT/trajectory_fused.csv" \
    --method gaussian --gaussian-sigma-s 0.025 --report "$OUT/smoothing_report.json" \
    > "$OUT/smooth.log" 2>&1 || exit 9
"$PY" "$ROOT/scripts/evaluate_slam_ground_truth.py" \
    --estimate "$OUT/trajectory_fused.csv" --ground-truth "$G/lighthouse_body_ground_truth.csv" \
    --output "$OUT/precision.json" --report-md "$OUT/precision.md" \
    > "$OUT/eval.log" 2>&1 || true