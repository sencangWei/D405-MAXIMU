#!/usr/bin/env bash
# 温和梯度探针 + 确定性对照；一格（v10_batch/group1 tight），埋点打开。
cd "$(dirname "$0")"
echo "=== CGATE2 START $(date +%F_%H:%M:%S) ==="
for t in base0 uC102 uC110; do
  ./run_frontend.sh 20260914_validation_v10_batch group1 tight 0.6 "$t" || echo "❌ $t"
done
echo "=== CGATE2 DONE $(date +%F_%H:%M:%S) ==="
