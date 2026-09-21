#!/usr/bin/env bash
# huber A/B：3 个剂量（0.3 / 5.0 / 20.0）× 2 个格 × sparse 臂（= 产线臂）。
# 基线 = w0.6（huber=1.345，已在盘）。实测 80% 白化残差 <1.345 ⇒ 两个方向都可能有效。
# 剂量选点：0.3 = 起始降权点压到 0.3px（更激进）；5.0 / 20.0 = 放开到 5px / 20px（更接近普通最小二乘）。
cd "$(dirname "$0")"
echo "=== HUBER SWEEP START $(date +%F_%H:%M:%S) ==="
for c in "20260914_validation_v10_batch group1" \
         "20260914_validation_v11_holdout_batch3 group2"; do
  set -- $c
  for t in h030 h500 h2000; do
    if [ -f "out/$1/$2/sparse/$t/trajectory_frames.csv" ]; then
      echo "⏭  $1/$2/sparse $t (已有)"; continue
    fi
    ./run_frontend.sh "$1" "$2" sparse 0.6 "$t"   # 注意：第5参 tag 决定读 sparse_<tag>.yaml
  done
done
echo "=== HUBER SWEEP DONE $(date +%F_%H:%M:%S) ==="
