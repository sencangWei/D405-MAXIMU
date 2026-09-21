#!/usr/bin/env bash
# C-as-weight 冒烟：先证旋钮是活的 + 基线没回归，再谈跑不跑全格。
cd "$(dirname "$0")"
echo "=== CWEIGHT SMOKE START $(date +%F_%H:%M:%S) ==="
# floor=1.0 必须与既有 w0.6 **逐字节相同**（证明默认关闭 + 无回归）
./run_frontend.sh 20260914_validation_v10_batch group1 tight 0.6 Cw100 || echo "❌ Cw100"
# floor=0.0 必须**不同**（证明权重真的进了法方程）
./run_frontend.sh 20260914_validation_v10_batch group1 tight 0.6 Cw000 || echo "❌ Cw000"
echo "=== CWEIGHT SMOKE DONE $(date +%F_%H:%M:%S) ==="
