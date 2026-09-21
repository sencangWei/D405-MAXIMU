#!/usr/bin/env bash
# C1 驱动: 3 格 × {prod 控制臂 + 4 个 matching 臂} 串行（单 GPU，必须串行）。
# 每格先跑 prod 控制臂 —— 必须逐字节复现盘上 trajectory_frames.csv，否则该格作废。
set -uo pipefail
D="$(cd -- "$(dirname -- "$0")" && pwd)"
CELLS=(
  "20260914_validation_v11_holdout_batch3/group2"
  "20260915_batch5_four_videos/group4"
  "20260915_collective_batch4/group1"
)
ARMS=(prod match_wide match_iter match_radius match_tight3d)
for cell in "${CELLS[@]}"; do
    for arm in "${ARMS[@]}"; do
        bash "$D/frontend_arm.sh" "$cell" "$arm" 2>&1 | sed "s/^/[${arm}] /"
    done
    # 控制臂复核
    G=/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow/$cell
    SRC="$G/fusion/sparse/mast3r"; OUT="$G/frontend_matching_sweep_20260922/prod"
    if cmp -s "$SRC/trajectory_frames.csv" "$OUT/trajectory_frames.csv"; then
        echo "[CHECK] $cell 控制臂 ✅ 逐字节复现"
    else
        echo "[CHECK] $cell 控制臂 ❌ 未复现 —— 该格作废"
    fi
done
echo "[DONE] C1 全部完成"