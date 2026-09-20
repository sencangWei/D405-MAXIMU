#!/usr/bin/env bash
# no-IMU 臂（`imu_rotation_prior: False` + `weight: 0.0`）6 格。
#
# ★ 等待条件用 sweep.log 的哨兵，而不是 pgrep：`pgrep -f "sweep.sh"` 会匹配到
#   本脚本自己（sweep_noimu.sh）⇒ 死循环；`pkill -f` 则会匹配到调用它的
#   shell 自己的命令行 ⇒ 自杀。哨兵法两者都避开。
cd "$(dirname "$0")"
while ! grep -q "SWEEP DONE" sweep.log 2>/dev/null; do sleep 30; done

# 补跑：weight sweep 期间 handler 被我的编辑撞成半截文件（EOF 语法错误），这次没跑成
[ -f "out/20260915_batch5_four_videos/group3/tight/w0.6/trajectory_frames.csv" ] \
  || ./run_frontend.sh 20260915_batch5_four_videos group3 tight 0.6

for c in "20260914_validation_v10_batch group1" \
         "20260915_batch5_four_videos group3" \
         "20260914_validation_v11_holdout_batch3 group2"; do
  set -- $c
  for arm in tight sparse; do
    [ -f "out/$1/$2/$arm/noimu/trajectory_frames.csv" ] \
      && { echo "skip $1/$2/$arm noimu"; continue; }
    ./run_frontend.sh "$1" "$2" "$arm" 0.0 noimu
  done
done
echo "=== NOIMU SWEEP DONE ==="