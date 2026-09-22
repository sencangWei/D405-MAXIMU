---
name: slam-tail-error-elimination-20260919
description: "2026-09-19 全 15 组尾部误差排查: 七条假说(真值跳变/尖峰/时间偏置/尺度/选链上界/真值离群/VINS-MASt3R错位)全部排除; 融合是最好的那条链(5.28mm vs VINS 24.2 / MASt3R 15.9); 元凶=VINS 度量尺度(VINS 误差向量方向 cos 0.9)"
metadata: 
  node_type: memory
  type: project
  originSessionId: 616ba18d-61b7-4aba-830c-5738635103f2
  modified: 2026-09-18T19:24:50.062Z
---

## 结论一句话

**融合不是瓶颈**。全 15 组里融合 ↔ 真值 中位 RMSE **5.28mm**，而它的两个输入
VINS 24.21mm、MASt3R 15.91mm。**连"每组事后挑最好的那条链"这个上界都是 0/15 达标**
⇒ 10mm 的 max 门在这批数据上任何单链都过不了，不是融合调参能解决的。

## 七条假说, 全部排除(每条都有脚本可复跑, 见 /tmp/claude-1000/stereoab/)

| # | 假说 | 判据 | 结果 |
|---|---|---|---|
| 1 | 真值有 tracker 分支跳变 | 真值自身台阶 vs VINS/MASt3R 同期位移 | **0 条真值独有跳变/15 组** |
| 2 | 尖峰 → 去尖峰+插值能救 | 峰宽(ATE>max/2 的连续跨度) | 全 18–106 帧的**"包"**, 无"尖" |
| 3 | 估计↔真值时间偏置 | 扫 δ ±60ms | δ* 中位 **+20ms**, 只降 1–5mm, **0/15 达标** |
| 4 | 融合尺度错 | 相似对齐(带尺度) vs 刚体对齐 | 只降 **0.08mm**; 隐含 s=**0.9985** |
| 5 | 选链上界 | 逐组取 min(VINS,MASt3R,融合) | **0/15** |
| 6 | 真值是离群那个 | VINS↔MASt3R 互校 | V↔M **28.45** > V↔GT 24.21 ⇒ 两条独立链**不抱团**, 真值不离群 |
| 7 | VINS↔MASt3R 相对错位 | 扫 τ ±100ms | 曲线**平的**, τ* 中位收益 **0.02mm** |

⇒ 误差是**真实的形状误差**, 与快速运动段相关(峰在中段 21–78%, 非末端漂移)。

## 又补排两条"怪真值"的(都不成立, 别重走)

**#8 真值 freeze-then-catchup 伪影**(`gt_freeze_scan.py`): 真值确实会先卡住再超速补回来
(12/15 组检出), 但**剔除检出的窗口后 22 个候选无一 FAIL→PASS**; 再直接定位 ATE argmax,
**22 个里只有 1 个**落在伪影窗内。⇒ 伪影真实存在, 但不是失败原因。
⚠ 陷阱: `batch3/group1` 的 max 落在爆发首帧 1082, 但那段"冻结"只是相对下陷
(局部中位速率本身仅 1.45mm/帧), `d < 0.5×med` 的判据抓不到。要检测得用"爆发前有相对下陷"。

**#9 真值高频毛糙**(`gt_roughness_vs_ate.py`): 真值做 Savitzky-Golay 局部二次拟合
(窗15帧, 保直线/匀加速)后的残差 = **p95 中位仅 0.86mm**, 而 ATE p95 中位 **10.41mm**,
相关仅 0.37。⇒ **10mm 门测的是真实轨迹误差, 不是真值噪声。**

**采样率**: 估计与真值都是 **30Hz / 58.1s**, 长度一致 ⇒ 不是抽帧/插值假象(顺带排除)。

## 元凶: VINS

误差向量方向对齐(融合最差 50 样本): `cos(e_融合, e_VINS)` = 0.90/0.84/0.76/0.76/0.68…
而 `cos(e_融合, e_MASt3R)` 弱得多甚至为负 ⇒ **融合继承 VINS 的误差方向, 8/11 组**。

- VINS 全轨迹 max **27–174mm**; MASt3R 23–53mm; 融合 10–26mm
- VINS 误差**中位≈RMSE**(24.36 vs 24.21), 无时间/路程结构 ⇒ 不是漂移
- VINS **隐含尺度 s 可低到 0.627**(collective_batch4/group1), 普遍 0.92–1.02 偏小
- MASt3R 隐含尺度 0.905–1.078(相似对齐降 14.2%)
- **融合把尺度修回 0.9985**, 尺度处理有效

## 关键机制: VINS 的验收门对尺度失明

`docker2_slam/run_acceptance.json` 的检查项全是**自洽性**: `pose_coverage`(≥0.98)、
`max_step_m`(≤0.03)、`z_span_retention_ratio`(≥0.9)、`endpoint_delta_m`。
**一条整体缩短 37% 的轨迹覆盖率满分、步长更小、Z 保持 1.0 —— 每项都过。**
所以 VINS 尺度崩到 0.627 仍报 `SLAM_HEALTHY / product_usable: true`。

融合侧的连带问题(`mast3r/graph_fusion_report.json`):
```
position_fusion_selection.visual_position_sigma:
    requested 0.02 → selected 0.04  (放宽视觉权重)
    reason = independent_onboard_trajectory_branch_disagreement
    position_disagreement_p95_m = 0.1662
```
**VINS 分歧大时反而放宽"视觉"的 sigma**, 而 VINS 自身 sigma 固定 0.008
(`--relative-motion-sigma-m 0.008`)。

⚠ **我曾猜这个触发量被 VINS 尺度污染(无尺度对齐 ⇒ 缩短 37% 的 VINS 必然巨残差),
实测否掉了**(`scale_confound_check.py`): 触发项(rigid_p95 ≥ 50mm)扣掉尺度后残差中位
只从 92.9 → 90.4mm, 隐含尺度中位 1.009, `batch5/group4` 是 94.3/94.4、scale 0.999。
⇒ **触发量是真实的形状分歧, 不是尺度假象。** 该策略"方向对不对"仍存疑
(已证 MASt3R 15.91 优于 VINS 24.21, 分歧时降视觉权重与证据相反), 但**不能拿尺度当理由**。

## 已修好的防线(历史遗留产物)

`fuse_mast3r_stereo_imu.py:119 validate_relative_motion_report` 现在会拒绝
未通过验收的 VINS 报告。两个 VINS FAIL 的组:
- `collective_batch4/group3`: 正确拒绝, 无产物
- `v10_holdout_batch2/group1`: **产物是历史遗留**(报告 18:01 FAIL, 融合 18:37/18:59 生成),
  该组应 **REJECT** 而非判"算法误差" —— 而它恰是我盘点里最差的一组(max 25.58)

相关:[[mast3r-fusion-param-generations]]、[[lighthouse-tracker-branch-switch]]、[[vins-config-optimal]]
