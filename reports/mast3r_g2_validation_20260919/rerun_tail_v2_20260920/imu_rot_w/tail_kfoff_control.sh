#!/usr/bin/env bash
# batch5/g3 tight 基准 w0.6 的尾链 —— 作为 kfoff 的同 harness 干净对照
# （compare.log 里有 20.37，但那是另一个 harness 跑出来的；只有同 harness 才可比）。
# 等待用产物哨兵，不用 pgrep（pgrep -f 会匹配到自己）。
cd "$(dirname "$0")"
SENT=out/20260914_validation_v11_holdout_batch3/group2/tight/kfoff/tail/precision.json
for i in $(seq 1 120); do [ -f "$SENT" ] && break; sleep 20; done
[ -f "$SENT" ] || { echo "❌ 等 v11b3 kfoff 尾链超时"; exit 1; }
[ -f "out/20260915_batch5_four_videos/group3/tight/w0.6/tail/precision.json" ] \
  && { echo "skip 已有"; exit 0; }
./run_tail.sh 20260915_batch5_four_videos group3 tight 0.6
echo "=== KFOFF CONTROL DONE ==="