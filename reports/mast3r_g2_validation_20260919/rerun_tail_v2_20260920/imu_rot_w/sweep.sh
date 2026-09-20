#!/usr/bin/env bash
cd "$(dirname "$0")"
for c in "20260914_validation_v10_batch group1" \
         "20260915_batch5_four_videos group3" \
         "20260914_validation_v11_holdout_batch3 group2"; do
  set -- $c
  for arm in tight sparse; do
    for w in 0.6 0.3 0.0; do
      [ -f "out/$1/$2/$arm/w$w/trajectory_frames.csv" ] && { echo "skip $1/$2/$arm w$w"; continue; }
      ./run_frontend.sh "$1" "$2" "$arm" "$w"
    done
  done
done
echo "=== SWEEP DONE ==="
