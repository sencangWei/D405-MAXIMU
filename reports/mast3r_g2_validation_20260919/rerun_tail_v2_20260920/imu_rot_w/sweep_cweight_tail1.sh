#!/usr/bin/env bash
# Stage B：C-as-weight 在**融合后**到底动没动门。cell= v10/group1 sparse。
# 基线 w0.6 = floor 1.0（已证与上游逐字节相同），所以 Δ 全部归因于这一个旋钮。
cd "$(dirname "$0")"
echo "=== CWEIGHT TAIL1 START $(date +%F_%H:%M:%S) ==="
for t in w0.6 Cw050 Cw000; do
  ./run_tail.sh 20260914_validation_v10_batch group1 sparse 0.6 "$t" || echo "❌ tail $t"
done
echo "=== CWEIGHT TAIL1 DONE $(date +%F_%H:%M:%S) ==="
