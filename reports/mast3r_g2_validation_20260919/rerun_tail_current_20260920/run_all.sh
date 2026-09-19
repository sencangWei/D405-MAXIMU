#!/usr/bin/env bash
# 全量：用现役生产参数重跑 22 条 cell 的尾段，产物落进真实报告树（fusion_current/）。
cd "$(dirname "$0")"
ok=0; bad=0
for b in 20260914_validation_v10_batch 20260914_validation_v10_holdout_batch2 \
         20260914_validation_v11_holdout_batch3 20260915_batch5_four_videos \
         20260915_collective_batch4; do
  for s in sparse tight; do
    for g in $(ls /home/robot/ego_vio_humble/reports/lighthouse_umi_workflow/$b 2>/dev/null | grep '^group'); do
      [ -f "/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow/$b/$g/fusion/$s/trajectory_fused.csv" ] || continue
      echo "===== $b/$g/$s"
      if ./rerun_one.sh "$b" "$g" "$s"; then ok=$((ok+1)); else bad=$((bad+1)); fi
    done
  done
done
echo "全部完成: 成功 $ok / 失败 $bad"
