#!/usr/bin/env bash
# lw × sw × smoothing-s 全格扫描（有 [7/8] 产物的 18 格 × 7×4×2 = 1008 跑，每跑 ~1s）。
cd "$(dirname "$0")"
echo "=== GRID START $(date +%H:%M:%S) ==="
n=0
while read -r b g a; do
  for lw in 0 0.10 0.15 0.20 0.25 0.30 0.35; do
    for sw in 0.10 0.25 0.475 0.85; do
      for smo in 8 15; do
        ./stage89p.sh "$b" "$g" "$a" "$lw" "$sw" "$smo" && n=$((n+1))
      done
    done
  done
done < cells.txt
echo "=== GRID DONE $(date +%H:%M:%S) 成功 $n ==="