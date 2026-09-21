#!/usr/bin/env bash
# C-as-weight 全对照：3 个可用格（第4格无 w0.6 基线）× 2 臂 × 2 个 floor。
# 既有产物自动跳过（v10/g1 tight Cw000 冒烟已跑）。
cd "$(dirname "$0")"
echo "=== CWEIGHT SWEEP START $(date +%F_%H:%M:%S) ==="
for c in "20260914_validation_v10_batch group1" \
         "20260914_validation_v11_holdout_batch3 group2" \
         "20260915_batch5_four_videos group3"; do
  set -- $c
  for a in tight sparse; do
    for t in Cw050 Cw000; do
      if [ -f "out/$1/$2/$a/$t/trajectory_frames.csv" ]; then
        echo "⏭  $1/$2/$a $t (已有)"
        continue
      fi
      ./run_frontend.sh "$1" "$2" "$a" 0.6 "$t" || echo "❌ $1/$2/$a $t"
    done
  done
done
echo "=== CWEIGHT SWEEP DONE $(date +%F_%H:%M:%S) ==="
