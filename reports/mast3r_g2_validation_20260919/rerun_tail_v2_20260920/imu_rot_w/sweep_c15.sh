#!/usr/bin/env bash
# C_conf 并集否证：tracking.C_conf + local_opt.C_conf 一起 0.0(默认) -> 1.5
# 3 格 × 2 臂；基线 = 同 harness 的 w0.6（只差 C_conf 那一行）
cd "$(dirname "$0")"
echo "=== C15 SWEEP START $(date +%F_%H:%M:%S) ==="
for c in "20260914_validation_v10_batch group1" \
         "20260914_validation_v11_holdout_batch3 group2" \
         "20260915_batch5_four_videos group3"; do
  set -- $c
  for a in tight sparse; do
    ./run_frontend.sh "$1" "$2" "$a" 0.6 C15 || echo "❌ $1/$2/$a C15"
  done
done
echo "=== C15 SWEEP DONE $(date +%F_%H:%M:%S) ==="
