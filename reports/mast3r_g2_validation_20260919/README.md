# MASt3R 融合尾段 G2 参数验证 — 首条达标轨迹

日期: 2026-09-19
范围: 融合尾段 `[7][8][9]` 参数换代验证 + 全 15 组尾部误差根因排查

```yaml
slam_supervision: false
external_ground_truth_used: false
```

> 上两行针对**算法本身**: 融合/建图全程未使用 Lighthouse 真值, 真值只用于**离线打分**。
> 本目录内的 `*.json` 是评测产物, 不是算法输入。

---

## 0. 一句话结论

**找到并复核了全项目第一条通过 ATE 门的轨迹**, 且证明**参数换代本身就是 PASS/FAIL 的分界**:

| 候选 | 参数代 | RMSE | P95 | **max** | 10mm内 | 姿态 | 结果 |
|---|---|---|---|---|---|---|---|
| `v11b3/group2` tight | G1(09-14 产物) | — | — | **10.32 mm** | — | — | ❌ FAIL |
| `v11b3/group2` tight | **G2(现役)** | **4.11** | **6.52** | **9.79 mm** | **100%** | **1.886°** | ✅ **PASS** |

复核由流水线自带评测器 `scripts/evaluate_slam_ground_truth.py` 出具 (非自研脚本),
产物见 [verify_official_scorer.json](trajectory_passing/verify_official_scorer.json),
被评轨迹见 [trajectory_passing/](trajectory_passing/)
(`sha256 579c81e2c8cd0a5b84378e65d671a87154becb61174fa81657f16e97bd17c204`)。

---

## 1. 为什么要做这次验证: 现成产物全是"上一代"

盘点 `reports/lighthouse_umi_workflow/` 下 12 组可测的 `fusion/trajectory_fused.csv`,
**全部是第一代参数**的产物:

```
G1(产物): --joint-correction-cap-mode global, 无 --adaptive-local-weight,
          --docker2-local-weight 0, --docker2-scale-weight 0
G2(现役): --joint-correction-cap-mode per-node, --adaptive-local-weight,
          --docker2-local-weight 0.25, --docker2-scale-weight 0.475
```

⇒ **现役算法从未在这批数据上评过。** 直接拿现成产物打分, 打的是 G1 的分。
本次补上这个缺口。

## 2. 验证方法: 融合尾段独立复现台 (纯 CPU)

[rerun_tail.py](rerun_tail.py) 把尾段三步抽出来单独跑:

```
[7] fuse_mast3r_stereo_imu.py          → trajectory_graph.csv
[8] fuse_docker2_mast3r_complementary.py → trajectory_fused_unsmoothed.csv
[9] assess_mast3r_fusion_input_quality.py + smooth_pose_trajectory.py → trajectory_fused.csv
```

**前端与标定输入逐字节冻结** —— 复用各组现成的 `trajectory_imu_metric.csv`
与四份 stereo 报告, **只改尾段参数**。因此本对照回答的是
"现役融合尾段参数 vs 第一代参数", 不是整条流水线。
约 30 秒/组, 全部输出到 scratch, **不写真实产物目录**。

遍历脚本 [g2_sweep.py](g2_sweep.py) → 结果 [g2_sweep.json](g2_sweep.json)。
它带 `preflight()`: 无 VINS 轨迹 / 无 `graph_fusion_report` / **VINS 验收非 PASS** 的组直接跳过,
不把"输入本来就废"的组算进参数对照。

## 3. 全组对照结果 (18 项可比)

- **G1 达标 0 / G2 达标 1**
- G2 使 max 下降 9 项 / 上升 9 项; RMSE 下降 8 / 上升 10 ⇒ **总体大致持平**
- G2 明显收益: `collective_batch4/group1` max 18.67→**14.85**; `batch5/group2` 13.01→**10.66**;
  `v11b3/group2` 10.32→**9.79**(达标); `v11b3/group1` 13.60→13.06
- G2 明显变差: `v10_batch/group2` 17.50→18.61; `batch5/group3` 22.07→22.30

**判断: G2 总体不比 G1 差, 且是唯一能出达标轨迹的一代。但这不是"调参调出来的好结果"——
真正的瓶颈在下一条。**

## 4. 关键机制: G2 触发输入质量门 REJECT (6 项)

同一份底层数据, 生产报告 PASS、现役代码 REJECT:

```
position_branch_used:        生产 False  →  G2 True
position_branch_weight_max:  生产 0.0    →  G2 0.2505     (= --docker2-local-weight)
reason:                      生产 None   →  G2 independent_onboard_trajectory_branch_unobservable
input_disagreement_p95_m:    0.05310    →  0.05304        (底层量几乎逐位相同)
```

`assess_mast3r_fusion_input_quality.py` 的策略是
**"reject when a position branch used by the output is unobservable"**。
生产侧 `--docker2-local-weight 0` ⇒ "没用到该分支" ⇒ 该检查形同虚设 ⇒ PASS;
G2 把权重提到 0.25 ⇒ **真的开始用 VINS 分支** ⇒ 该分支不可观测 ⇒ 被拒。

**这不是"门变严了"**, 是 G2 更依赖 VINS 的直接后果。

## 5. 根因: VINS 的度量尺度 (本次排查的核心发现)

七条针对"融合有问题"的假说全部被脚本排除 (真值跳变 / 尖峰 / 时间偏置 / 融合尺度 /
选链上界 / 真值离群 / VINS↔MASt3R 错位), 详见各 `*_scan.py`、`*_test.py`。
**融合不是瓶颈** —— 全 15 组里融合↔真值中位 RMSE **5.28 mm**,
而它的两个输入 VINS **24.21 mm**、MASt3R **15.91 mm**。融合比两条输入链好 3–5 倍。

落在 VINS 上的证据:

- 误差向量方向对齐(融合最差 50 样本): `cos(e_融合, e_VINS)` = 0.90/0.84/0.76/0.76/0.68,
  而 `cos(e_融合, e_MASt3R)` 弱得多甚至为负 ⇒ **融合继承 VINS 的误差方向, 8/11 组**
- VINS 误差**中位 ≈ RMSE**(24.36 vs 24.21), 无时间/路程结构 ⇒ 不是漂移, 是形状/尺度问题
- VINS **隐含尺度 s 中位 0.9599, 范围 [0.6265, 1.0158]**
  (`collective_batch4/group1` = 0.627, **整体短 37%**)
- 融合把尺度修回 **0.9985** ⇒ 尺度处理是有效的

**机制: VINS 的验收门对尺度失明。** `docker2_slam/run_acceptance.json` 只查自洽性 —
`pose_coverage` ≥0.98、`max_step_m` ≤0.03、`z_span_retention_ratio` ≥0.9、`endpoint_delta_m`。
**一条整体缩短 37% 的轨迹覆盖率满分、步长更小、Z 保持 1.0 —— 每一项都过。**
于是 VINS 尺度崩到 0.627 仍报 `SLAM_HEALTHY / product_usable: true`。

融合侧还有个连带问题 (`graph_fusion_report.json`): VINS 分歧大时, 策略反而把
**视觉**的 sigma 从 0.02 放宽到 0.04 (reason `independent_onboard_trajectory_branch_disagreement`),
而 VINS 自身 sigma 固定 0.008 (`--relative-motion-sigma-m 0.008`) —— **信错了人**。

## 6. 数据卫生: 一组必须 REJECT 的历史产物

`v10_holdout_batch2/group1`: VINS `result=FAIL` / `state=SLAM_FAILED` / `product_usable=false`
(报告时间 18:01), 但它的融合产物生成于 18:37/18:59
⇒ **产物早于 `fuse_mast3r_stereo_imu.py:119 validate_relative_motion_report` 这道防线**,
是历史遗留。该组是全盘点里最差的一组 (max 25.58 mm), 应判 **REJECT, 而非"算法误差"**。
`collective_batch4/group3` 同样 VINS FAIL, 但现役代码正确拒绝、无产物 —— 防线本身是好的。

## 7. 目录内容

| 文件 | 内容 |
|---|---|
| [trajectory_passing/](trajectory_passing/) | **达标轨迹本体** + 官方评测器复核结果 |
| [g2_sweep.py](g2_sweep.py) / [.json](g2_sweep.json) | G1 vs G2 全组对照 |
| [rerun_tail.py](rerun_tail.py) | 融合尾段独立复现台 |
| [survey_all.py](survey_all.py) / .json | 15 组 ATE 盘点 |
| [chain_ranking.py](chain_ranking.py) / .json | VINS / MASt3R / 融合 三链逐组打分 |
| [blame_vector.py](blame_vector.py) / .json | 误差向量方向对齐(定位元凶) |
| [vins_scale_test.py](vins_scale_test.py) / .json | 三链相似对齐隐含尺度 |
| [vins_profile.py](vins_profile.py) / .json | VINS 误差的时间/路程结构 |
| [scale_test.py](scale_test.py) / .json | 融合尺度检验(带 s=1 自校验) |
| [cross_check.py](cross_check.py) / .json | VINS↔MASt3R 互校 |
| [gt_jump_scan.py](gt_jump_scan.py) | 真值自身台阶跳变扫描 |
| [tail_shape.py](tail_shape.py) | 峰宽/峰位/加速度形状分析 |
| [time_offset_scan.py](time_offset_scan.py) / .json | 估计↔真值时间偏置扫描 |
| [vm_offset_scan.py](vm_offset_scan.py) | VINS↔MASt3R 相对错位扫描 |
| [despike_test.py](despike_test.py) | 去尖峰+插值假说检验 |
| `*_probe.py`, `f1.py`, `fps.py`, `cmp.py`, `crosscheck.py` | 排查过程中的一次性探针 |

---

## 8. 复跑方法

```bash
P=/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/.venv/bin/python
cd /home/robot/ego_vio_humble/reports/mast3r_g2_validation_20260919

# 单组复现(改参数用 --set key=value, 关布尔开关用 --set no-xxx=1)
$P rerun_tail.py \
  /home/robot/ego_vio_humble/reports/lighthouse_umi_workflow/20260914_validation_v11_holdout_batch3/group2 \
  /tmp/rerun_out --candidate tight

# 全组 G1 vs G2 对照
$P g2_sweep.py
```

## 9. 待办 / 未决

- `--docker2-local-weight` 是否回到 0? G2 用 0.25 换来 1 条达标, 代价是 6 组质量门 REJECT。
  **这是策略取舍, 需用户决定**; 任何改动都必须按"不许为单条轨迹调参"的规矩在全组重验
  (复现台已具备这个能力)。
- VINS 侧: 验收门缺尺度项 —— 建议加一条与外部/几何尺度无关的自洽检查
  (如 stereo/IMU 尺度比) 进 `run_acceptance`, 否则尺度崩了仍是绿的。
- 融合侧: `visual_position_sigma` 在 VINS 分歧时放宽视觉权重, 方向可疑, 需单独验证。
- 未做: 在用户新录数据上验证; 换干净回路数据集绕开 tracker 假象。
