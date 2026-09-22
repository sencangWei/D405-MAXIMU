---
name: mast3r-0914-no-good-result-20260921
description: 盘上不存在可复原的「09-14 好结果」——09-14 生产 0/10 PASS 且比现役更差；唯一 PASS 连作者自己都标了不可推广
metadata: 
  node_type: memory
  type: project
  originSessionId: 616ba18d-61b7-4aba-830c-5738635103f2
  modified: 2026-09-21T15:36:24.378Z
---

**2026-09-21 判决：「往 09-14 复原」这个前提被盘上的证据否掉。**
**（同日 §34 复算后判决不变**：剔掉新发现的两组坏真值后干净组 10→8，
09-14 生产仍 **0 PASS**、`max` 中位 14.59 仍差于现役 12.58–14.24。**）

工具=`imu_rot_w/family_gate.py`（**先与官方 `precision.json` 逐位对账过 4/4 组**才允许下结论）。
证据：`rerun_tail_v2_20260920/README.md` §32；已推 `sencang 8867cddc`（blob 9/9 逐位一致）。

## 三条硬事实

1. **09-14 的生产产物从没过过门**：10 个真值干净组 **0/10 PASS**，`max` 全超；
   `max` 中位 **15.41mm**，比现役的 **13.10–14.29mm 更差**。⇒ **没有"好结果"可复原。**
2. **全库 634 份 `precision.json` 只有 85 份过门，属于 09-14 的只有 1 份**：
   `20260914_validation_v11_holdout_batch3/diagnostics/adaptive_local_w035/group2`
   （max **9.006**/p95 6.500/rmse 3.900/w10 1.0000/rot 1.857）。指纹 =
   `local_weight=0.35, adaptive_local_weight=True, smoothing_s=8, scale_weight=0.0`，前端 `tight`。
   ⇒ 与现役产线**只差 `--docker2-local-weight`(0.35 vs 0) 和 `--docker2-scale-weight`(0 vs 0.25)**。
3. **但这个唯一 PASS 是 09-14 作者自己标注「不能推广」的** —— `diagnostics/over_10mm_root_cause.md`
   原话：「该候选在上一批第 2 条上会明显恶化，说明**不能直接替换正式一键脚本**」。
   我独立复现：`adaptive_local_w035/group1` 12.88 仍 FAIL。⇒ 与 [[mast3r-fusion-param-generations]]
   的 lw A/B「3胜14负」**同向**，那条判决不用改。

## 两个副产物（都可直接用）

- ★★ **十二个门禁组里有四个的真值本身不可用（两个物种）**：
  - **分支跳变 REJECT**（`scripts/lighthouse_tracker_branch_gate.py`）：
    `20260914_175017_validation_v10`（→`holdout_batch2/group1`，**63.6% 相机帧**受影响）
    与 `20260915_103540_validation_v10`（→`batch5_four_videos/group1`，**65.6%**）。
  - **时窗截断**（09-21 §34 新发现，见 [[lighthouse-gt-timing-uncertainty]]）：
    `holdout_batch2/group2`（tracker 窗短 2.42s ⇒ overlap **0.9592**）与
    `collective_batch4/group2`（短 7.45s ⇒ **0.8711**）⇒ 过不了官方
    `--min-timestamp-overlap-ratio 0.98`，**与精度无关**。
  ⇒ 这两组 ATE 不可用；我表里"最差"的格（25.58–30.33mm）**正好落在分支 REJECT 组上**。
  **凡拿 `max` 做族间/代际比较，必须先剔这两类**（这是 [[lighthouse-tracker-branch-switch]]
  第一次接到门禁组上）。⚠ `holdout_batch2` **两条 take 真值都有缺陷** ⇒ 这批拿不出可用 ATE。
- **VINS 验收门阻断的正是真值不可用的那两条 take**；`v10_holdout_batch2/group2` 根本没有
  `vio_corrected_stream.csv`。⇒ 两者有共同上游，不是巧合。

## 两条独立证据链指向同一器官

- **09-14 作者原话**（09-14，比 §31 早 6 天）：「幅值被压低，方向差约 93° …**单纯调权重不能恢复
  已经缺失或方向错误的视觉局部运动**…应优化**前端短窗对应和局部加速度方向约束**」
- **§31.6**（09-21，22 臂×4 阶段）：唯一还有量级的杠杆仍在 `[1/8]` 本身。

⇒ 下一步该动的是**前端短窗对应 / 局部加速度方向约束**，不是任何 `[8/9]` 参数。
见 [[mast3r-rerun-tail-v2-20260920]]。

## 对「复原」指令的处置

**不做整体回退。** 要复原的不是参数，是 **pipeline 结构**（**共三条真实损失，全部已定位**）：
① 启动器 `use_calib` 循环门控（[[mast3r-frontend-config-silent-disable-20260920]]，已修）；
② `--metric-scale-mode joint` 在**产品路径 `fusion)`** 是硬门（`return 3`+`set -e` ⇒ 零产物），
而 09-11 Codex 定的「尺度由双红外负责、IMU 只修姿态、冲突只作诊断」**只接进了 `compare)`**
（[[codex-gate-blindspot-history]]）；
③ **（09-21 新查实）`[9/9]` 质量门 09-15 15:12 起加严了两条按 `--docker2-local-weight` 互斥的臂**：
现役产线参数（lw=0/sw=0.25）下 **22 格中 4 格 `rc=3` 中止 ⇒ 零产物**
（`b5/g3`、`holdout_b2/g2` 各 sparse+tight），而 09-14 旧代码 **22/22 PASS**。
其自带测试即行为规格、数字取自真实格 ⇒ **不是静默回归，是策略选择**；
损失的性质是「零产物/不可观测」，**不是精度**（同格 `fusion_current` max 22.3 反正过不了 10mm 门）。
**⇒ 09-21 已实施（用户批准）**：只把调用点**降级为诊断**（只容忍 `rc=3`，其余非零码仍中止），
**门本身一字未改**；端到端验证产物与手工旁路**逐字节相同**。详见
[[mast3r-g1-vs-g2-sweep-20260919]] §33/§35、`rerun_tail_v2_20260920/README.md` §33/§35。

② 仍涉及产线开门策略（`--metric-scale-mode joint` 的尺度硬门），**需用户拍板**；
③ 里 `[7/8]` 同族第二道硬门（本语料 65/65 PASS 从未触发）、其逃生舱是否接纳
`corrected_trajectory_jump`、0.5× 回放是否做成自动重试 —— 同待拍板。