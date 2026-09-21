#!/usr/bin/env bash
# 7 臂 × 22 格（每格 ~1s，因为只跑 [8/9]+[9/9]+eval）。
cd "$(dirname "$0")"
echo "=== 89 SWEEP START $(date +%H:%M:%S) ==="
while read -r b g a; do
  for tag in A B C D E F G; do
    ./stage89.sh "$b" "$g" "$a" "$tag"
  done
done < cells.txt
echo "=== 89 SWEEP DONE $(date +%H:%M:%S) ==="