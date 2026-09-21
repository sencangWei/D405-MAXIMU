#!/usr/bin/env bash
# C1: 前端 [1/8] `matching` 调参面扫描 —— 单格单臂前端单跑。
#
# 逐字复刻 scripts/mast3r_slam_precision_workflow.sh:103-134 的前端调用，
# 只换 --config 指向扫描臂。产物落 <cell>/frontend_matching_sweep_20260922/<arm>/，
# 不碰任何旧产物、不碰工具链。
#
# 用法: frontend_arm.sh <cell相对路径> <arm>
#   <arm> ∈ prod | match_wide | match_iter | match_radius | match_tight3d
#   prod = 现役 config（控制臂：必须逐字节复现盘上 trajectory_frames.csv）
set -uo pipefail
CELL="$1"; ARM="$2"
ROOT=/home/robot/ego_vio_humble
TOOL="${MAST3R_SLAM_DIR:-/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM}"
PYTHON="$TOOL/.venv/bin/python"
CUDA_ROOT="$TOOL/.cuda"
CKPT="${MAST3R_SLAM_CHECKPOINT:-$TOOL/checkpoints/MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric.pth}"

if [[ "$ARM" == "prod" ]]; then
    CFG="$ROOT/config/mast3r_slam_d405_offline.yaml"
else
    CFG="$ROOT/config/mast3r_slam_d405_offline_${ARM}.yaml"
fi
[[ -f "$CFG" ]] || { echo "❌ 无此 config: $CFG" >&2; exit 2; }

G="$ROOT/reports/lighthouse_umi_workflow/$CELL"
SRC="$G/fusion/sparse/mast3r"
OUT="$G/frontend_matching_sweep_20260922/$ARM"
[[ -d "$SRC/dataset" ]] || { echo "❌ 无前端输入: $SRC/dataset" >&2; exit 2; }
mkdir -p "$OUT"

export CUDA_HOME="$CUDA_ROOT"
export PATH="$CUDA_HOME/bin:$PATH"
export LD_LIBRARY_PATH="$CUDA_HOME/lib64:${LD_LIBRARY_PATH:-}"

started=$SECONDS
(
    cd "$TOOL"
    "$PYTHON" main.py \
        --dataset "$SRC/dataset" \
        --config "$CFG" \
        --save-as "$OUT/mast3r_logs" \
        --no-viz \
        --no-reconstruction \
        --checkpoint "$CKPT" \
        --calib "$SRC/dataset/calibration.yaml"
) > "$OUT/mast3r.log" 2>&1
rc=$?
[[ $rc -eq 0 ]] || { echo "❌ 前端 rc=$rc"; tail -8 "$OUT/mast3r.log"; exit 3; }

"$PYTHON" "$ROOT/scripts/convert_mast3r_slam_trajectory.py" \
    --trajectory "$OUT/mast3r_logs/dataset_full.txt" \
    --frames "$SRC/dataset/frames.csv" \
    --output "$OUT/trajectory_frames.csv" \
    --dense-output "$OUT/trajectory_all_frames_interpolated.csv" \
    > "$OUT/convert.log" 2>&1 || { echo "❌ convert rc=$?"; tail -5 "$OUT/convert.log"; exit 4; }

echo "✅ $CELL/$ARM  用时 $((SECONDS-started))s  -> $OUT/trajectory_frames.csv"
echo "   dataset_full sha256: $(sha256sum "$OUT/mast3r_logs/dataset_full.txt" | cut -c1-16)"