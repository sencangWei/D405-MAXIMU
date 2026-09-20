#!/usr/bin/env bash
# 判定臂：tight 基底 + prior=True/weight=0.6（= 现役）+ 运动关键帧关掉。
# 3 格，只为回答「noimu 的效果来自关键帧重排（②）还是先验本身（①）」。
cd "$(dirname "$0")"
echo "=== KFOFF SWEEP START $(date +%H:%M:%S) ==="
for c in "20260914_validation_v10_batch group1" \
         "20260915_batch5_four_videos group3" \
         "20260914_validation_v11_holdout_batch3 group2"; do
  set -- $c
  [ -f "out/$1/$2/tight/kfoff/trajectory_frames.csv" ] \
    && { echo "skip $1/$2 kfoff"; continue; }
  ./run_frontend.sh "$1" "$2" tight 0.6 kfoff
done
echo "=== KFOFF SWEEP DONE $(date +%H:%M:%S) ==="