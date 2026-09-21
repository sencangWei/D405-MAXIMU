#!/usr/bin/env bash
# 只跑前端 [1/8]：复用现成 dataset，只换 --config（一行之差）。
# 用法: run_frontend.sh <batch> <group> <arm> <w>
set -uo pipefail
BATCH="$1"; GROUP="$2"; ARM="$3"; W="$4"; TAG="${5:-w$W}"
ROOT=/home/robot/ego_vio_humble
WF="$ROOT/reports/lighthouse_umi_workflow"
HERE="$(cd "$(dirname "$0")" && pwd)"
TOOL_DIR=/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM
PY="$TOOL_DIR/.venv/bin/python"
CHECKPOINT="$TOOL_DIR/checkpoints/MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric.pth"
CUDA_ROOT="$TOOL_DIR/.cuda"

G="$WF/$BATCH/$GROUP"
DS="$G/fusion/$ARM/mast3r/dataset"
OUT="$HERE/out/$BATCH/$GROUP/$ARM/$TAG"
mkdir -p "$OUT"
[ -f "$DS/calibration.yaml" ] || { echo "❌ 无 dataset: $DS"; exit 3; }

export CUDA_HOME="$CUDA_ROOT"
export PATH="$CUDA_HOME/bin:$PATH"
export LD_LIBRARY_PATH="$CUDA_HOME/lib64:${LD_LIBRARY_PATH:-}"
# 逐帧匹配埋点（tracker.py:32 log_match_stats）—— 默认零行为影响，仅在设了路径时写盘。
# 每跑一个独立文件，避免多次追加进同一 CSV。
export MAST3R_MATCH_LOG="$OUT/match_log.csv"
S=$SECONDS
( cd "$TOOL_DIR" && "$PY" main.py \
    --dataset "$DS" --config "$HERE/${ARM}_${TAG}.yaml" \
    --save-as "$OUT/mast3r_logs" --no-viz --no-reconstruction \
    --checkpoint "$CHECKPOINT" --calib "$DS/calibration.yaml" ) > "$OUT/mast3r.log" 2>&1
RC=$?
E=$((SECONDS-S))
[ $RC -eq 0 ] || { echo "❌ main.py rc=$RC"; tail -5 "$OUT/mast3r.log"; exit 4; }
"$PY" "$ROOT/scripts/convert_mast3r_slam_trajectory.py" \
    --trajectory "$OUT/mast3r_logs/dataset_full.txt" --frames "$DS/frames.csv" \
    --output "$OUT/trajectory_frames.csv" \
    --dense-output "$OUT/trajectory_all_frames_interpolated.csv" >> "$OUT/mast3r.log" 2>&1 \
    || { echo "❌ convert rc=$?"; exit 5; }
echo "$E" > "$OUT/elapsed_s.txt"
echo "✅ $BATCH/$GROUP/$ARM $TAG  ${E}s"