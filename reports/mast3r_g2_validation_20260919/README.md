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

**找到并复核了这批数据上第一条通过 ATE 门的轨迹**(口径: `lighthouse_umi_workflow`
下 5 个批次 / 15 组 / 18 项可比候选, 历史产物 0 达标 + 现役参数重跑 1 项达标),
且证明**参数换代本身就是 PASS/FAIL 的分界**:

| 候选 | 参数代 | RMSE | P95 | **max** | 10mm内 | 姿态 | 结果 |
|---|---|---|---|---|---|---|---|
| `v11b3/group2` tight | G1(09-14 产物) | — | — | **10.32 mm** | — | — | ❌ FAIL |
| `v11b3/group2` tight | **G2(现役)** | **4.11** | **6.52** | **9.79 mm** | **100%** | **1.886°** | ✅ **PASS** |

复核由流水线自带评测器 `scripts/evaluate_slam_ground_truth.py` 出具 (非自研脚本),
产物见 [verify_official_scorer.json](trajectory_passing/verify_official_scorer.json),
被评轨迹见 [trajectory_passing/](trajectory_passing/)
(`sha256 579c81e2c8cd0a5b84378e65d671a87154becb61174fa81657f16e97bd17c204`)。

> **⚠ 余量很薄, 别当"稳过"读。** 两项卡在门边:
> `max` 9.79 / 门 10.0(**余量 2.1%**), `姿态` 1.886 / 门 2.0(**余量 5.7%**)。
> 另三项余量充裕(RMSE 4.11/门10, P95 6.52/门10, 10mm内 100%/门95%)。
> 也就是说这条达标轨迹**离不过只差一点点**, 不是"已经解决"。

**并且尾段优化的空间已经用尽(§14)**: 把 5 轮扫描的 **19 个(扫描 × 配置)组合**全部汇总,
逐项取最好的那次, **18 项里只有 3 项的地板能压到 10mm 以内**, 另外 **15 项的地板在
10.36~23.70mm** —— 无论尾段参数怎么调都过不了门。所有配置的达标数都 ≤1、REJECT 数都 =9。
**卡住的是上游(VINS 度量尺度 + 输入质量门), 不是尾段。**

**新录的 09-17/09-18 `loop1` 数据(§15)暂不可用**: 10 条里 9 条有 tracker 数据的被
分支跳变门 REJECT 掉 8 条, 真值被切成不连通的段 ⇒ 那三个 `FAIL` 报告的 ~23mm 尖峰是
**真值伪影而非 SLAM 精度**。需要重录一条干净 take。

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

### 2.1 复现台自校验: 逐字节复现已提交的达标轨迹

复现台若不能重放原产物, 后续所有对照都不成立。用现役参数(A 组)重跑
`v11b3/group2` tight, 与 §0 那条已推送并**从远端回取验证过**的达标轨迹比对:

```
579c81e2c8cd0a5b84378e65d671a87154becb61174fa81657f16e97bd17c204  /tmp/.../vsw/A_g2_w0.250_sig0.008/.../tight/trajectory_fused.csv
579c81e2c8cd0a5b84378e65d671a87154becb61174fa81657f16e97bd17c204  trajectory_passing/v11b3_group2_tight_G2_trajectory_fused.csv
```

⇒ **sha256 逐位一致**, 复现台与产线同源。本目录所有参数对照都建立在同一个前端之上。

## 3. 全组对照结果 (18 项可比)

- **G1 达标 0 / G2 达标 1**
- G2 使 max 下降 9 项 / 上升 9 项; RMSE 下降 8 / 上升 10 ⇒ **总体大致持平**
- G2 明显收益: `collective_batch4/group1` max 18.67→**14.85**; `batch5/group2` 13.01→**10.66**;
  `v11b3/group2` 10.32→**9.79**(达标); `v11b3/group1` 13.60→13.06
- G2 明显变差: `v10_batch/group2` 17.50→18.61; `batch5/group3` 22.07→22.30

**判断: G2 总体不比 G1 差, 且是唯一能出达标轨迹的一代。但这不是"调参调出来的好结果"——
真正的瓶颈在下一条。**

### 3.1 样本口径: "18 项"是**以 VINS 通过为条件**的子集 (必读)

本文所有全组统计(§3/§10/§11 及后续扫描)的口径不是"全部已录数据", 而是
**preflight 通过的那 18 项**。preflight 要求 `docker2_slam/vio_corrected_stream.csv`
存在且 `run_acceptance.json: result == PASS`。被挡掉的是整批
`20260914_validation_v10_holdout_batch2` (4 项), 原因是 **VINS/Docker2 上游自己没过门**:

| 组 | 挡掉的原因 |
|---|---|
| `holdout_batch2/group1` | `result: FAIL`, failures = `runtime watchdog is SLAM_FAILED: corrected_trajectory_jump` + `pose coverage 0.9590 < 0.9800` |
| `holdout_batch2/group2` | 连 `docker2_slam/` 目录都没有 —— VINS 侧根本没产出 |

两点必须记住:

1. **扫描结论对"VINS 已通过"这一前提是条件性的。** 对 VINS 自己就失败的会话,
   融合尾段的任何参数都不适用 —— 那不是尾段能修的问题(呼应 §12)。
2. 因此**不要**把 §3 的 "G2 达标 1/18" 读成"全库达标率 1/22"。分母不同,
   后者还包含 4 项在更上游就已失败的会话。

## 4. 关键机制: G2 触发输入质量门 REJECT (9 项)

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

### 4.1 关键: 这些 REJECT **零代价** (所以"回退 local-weight 到 0"的理由不成立)

门是个对称夹逼(`assess_mast3r_fusion_input_quality.py:84-92`)—— 分歧 ≥50mm 时:

| 情形 | 判定 |
|---|---|
| 用了 onboard 分支 (weight > 0) | REJECT `..._branch_unobservable` |
| 没用 (weight == 0) 且 stereo_rmse ≥ 3.5mm | REJECT `primary_shape_not_independently_supported` |
| 没用 (weight == 0) 且 stereo_rmse < 3.5mm | PASS |

生产 G1 走的是第三行(weight 0 + stereo 够好)⇒ PASS。但把被拒的 9 项拿回 G1 下打分:

```
G2 触发质量门 REJECT 的 9 项, 在 G1 下的成绩:
  batch5/group2      sparse  G1 max 13.01 rmse 4.21   **本身就不达标**
  batch5/group2      tight   G1 max 16.57 rmse 5.22   **本身就不达标**
  batch5/group3      sparse  G1 max 22.07 rmse 5.93   **本身就不达标**
  batch5/group3      tight   G1 max 20.11 rmse 7.35   **本身就不达标**
  batch5/group4      sparse  G1 max 15.33 rmse 4.99   **本身就不达标**
  batch5/group4      tight   G1 max 15.10 rmse 5.27   **本身就不达标**
  collective_batch4/group1 sparse G1 max 18.67 rmse 5.76 **本身就不达标**
  collective_batch4/group1 tight  G1 max 30.39 rmse 7.50 **本身就不达标**
  collective_batch4/group2 sparse G1 max 15.48 rmse 5.80 **本身就不达标**

其中 G1 下本来达标的: 0 / 9
```

⇒ **G2 的 REJECT 拒掉的全是本就不该出厂的产物**, 代价为零。
"REJECT 变多了"不能当作回退 G2 的理由 —— 该问的是 ATE 有没有变好(见 §10)。

### 4.2 为什么"把 `--docker2-local-weight` 退回 0"救不了这 9 项

直觉是: 退回 0 ⇒ "没用到 onboard 分支" ⇒ 走第三行 ⇒ PASS。**实测不成立。**

§10 的 B 组(`--docker2-local-weight 0`, 其余同现役)在**同样这 9 项上全部照拒**,
而且 reason 仍是 `..._branch_unobservable`。查被拒报告的原始字段:

```
B 组(weight 设 0)被拒项: position_branch_weight_max = 0.02   ← 根本不是 0
                         stereo_edge_rmse 2.74 / 2.95 / 2.97 / 3.07 / 3.67 mm
```

根因在 [fuse_docker2_mast3r_complementary.py:359-362](../scripts/fuse_docker2_mast3r_complementary.py):

```python
effective_weight = np.clip(
    local_weight + adaptive_weight_strength * activation * relative_roughness,
    0.02,        # ← 硬下界: 开了 --adaptive-local-weight 就再也回不到 0
    0.98,
)
```

**`--adaptive-local-weight` 给有效权重压了个 0.02 的硬下界**, 于是
"用户把 local-weight 设成 0" 与 "输出真的没用 onboard 分支" 不再等价 ——
质量门看到 `weight > 0`, 判定该分支"被用了", 照旧 REJECT。

⇒ 这 9 项 REJECT 的**直接成因是 `--adaptive-local-weight` 的 0.02 下界**,
不是 `0.25` 这个数值本身(两者任一都足以触发)。生产 G1 之所以 PASS
(`position_branch_weight_max = 0.0`), 是因为它**同时**没有 adaptive 层。
想靠回退拿到 G1 的 PASS, 必须**两个开关一起关** —— 而那等于退回 G1,
而 G1 达标 0 项(§3)。

> 附注: 上表"生产 G1 PASS"是**按当时的门**成立的。现役门多了
> `primary_shape_not_independently_supported` 分支, 按现役门重算,
> `batch5/group3` sparse/tight 的双目 `stereo_rmse` 3.93/3.67mm ≥ 3.5mm,
> 即便 weight 真的回到 0 也仍会触发该分支。
> "两个开关一起关"的净效果由 [zero_branch_check.py](zero_branch_check.py)
> 单变量实测, 结果见 §10.3。
>
> **但这条路本身不值得走**: 它等价于退回 G1, 而 G1 达标 0 项(§3)。

## 5. 根因: VINS 的度量尺度 (本次排查的核心发现)

七条针对"融合有问题"的假说全部被脚本排除 (真值跳变 / 尖峰 / 时间偏置 / 融合尺度 /
选链上界 / 真值离群 / VINS↔MASt3R 错位), 详见各 `*_scan.py`、`*_test.py`。
**融合不是瓶颈** —— 全 15 组里融合↔真值中位 RMSE **5.28 mm**,
而它的两个输入 VINS **24.21 mm**、MASt3R **15.91 mm**。融合比两条输入链好 3–5 倍。

### 5.1 后续又排除了两条"怪真值"的假说 (都不成立)

**(8) 真值 freeze-then-catchup 伪影** —— [gt_freeze_scan.py](gt_freeze_scan.py)。
真值确实有这种失效(12/15 组检出: 先卡住再超速补回来), 但**把检出的窗口全部剔除后,
22 个候选里没有一个 max 由 FAIL 转 PASS**。再精确一步: 直接定位 ATE 的 argmax,
看它落在哪 —— **22 个候选里只有 1 个**(`collective_batch4/group2/sparse`)的 max
落在伪影窗内。⇒ 真值伪影是真的, 但不是失败的原因。
(注: `batch3/group1` 的 max 落在爆发首帧 1082, 但那里的"冻结"只是相对下陷
—— 局部中位速率本身仅 1.45mm/帧, 所以 `d < 0.5×med` 的判据抓不到它。)

**(9) 真值自身的高频毛糙** —— [gt_roughness_vs_ate.py](gt_roughness_vs_ate.py)。
对真值做 Savitzky-Golay 局部二次拟合(窗 15 帧, 保直线/匀加速), 残差即真值毛糙:

```
22 项: GT 毛糙 p95 中位 0.86mm   vs   ATE p95 中位 10.41mm   (相关仅 0.37)
```

**真值毛糙比 ATE 小一个数量级 ⇒ 10mm 门测的是真实轨迹误差, 不是真值噪声。**
(改用平滑真值打分确有 3 项 max 转 PASS, 但平滑同时抹掉了真运动,
估计轨迹本来就比真值平滑, 这么比是循环论证 —— 只作线索, 不作结论。)

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

融合侧还有个可疑策略 (`select_visual_position_sigma`,
`fuse_mast3r_stereo_imu.py:587`): VINS 与视觉分歧 p95 ≥ 50mm 时, 把
**视觉**的 sigma 从 0.02 放宽到 0.04(即降低视觉权重), reason
`independent_onboard_trajectory_branch_disagreement`; 而 VINS 自身 sigma 固定 0.008。

**我曾猜这个触发量被 VINS 的尺度误差污染(无尺度对齐 ⇒ 缩短 37% 的 VINS 必然巨残差),
但实测否掉了** —— [scale_confound_check.py](scale_confound_check.py):

```
触发(rigid_p95 ≥ 50mm)的 10 项:
  扣掉尺度后残差中位: 90.4mm  (原 92.9mm)      ← 几乎不降
  隐含尺度中位: 1.009  范围 [0.908, 1.592]     ← 典型触发项的尺度是正常的
  batch5/group4: rigid 94.3 / sim 94.4, scale 0.999, 比 1.0   ← 纯形状分歧
```

⇒ **触发量是真实的形状分歧, 不是尺度假象。** 仅 `collective_batch4/group1`(scale 1.592)与
`v11b3/group2/tight`(scale 0.908, 比 1.4)两项里尺度确有可观贡献。

> **本段"该策略方向存疑"的判断已被 §11 实测推翻, 保留原文以示更正轨迹。**
> 我曾据"MASt3R 15.91mm 优于 VINS 24.21mm, 分歧时却降视觉权重"推断方向反了;
> §11 配置 E(关掉该策略)实测**更差**(max 最差 24.16→24.56)。
> ⇒ **该策略在帮忙, 保留**。教训: "某条链全局更准"推不出"分歧时该听它"。`

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
| [scale_confound_check.py](scale_confound_check.py) / .json | 检验质量门触发量是否被 VINS 尺度污染(**结果: 否**) |
| [gt_freeze_scan.py](gt_freeze_scan.py) / .json | 真值 freeze-then-catchup 伪影检测(**结果: 不是失败原因**) |
| [gt_roughness_vs_ate.py](gt_roughness_vs_ate.py) / .json | 真值毛糙度 vs ATE(**结果: 毛糙 0.86mm ≪ ATE 10.4mm**) |
| [vins_weight_sweep.py](vins_weight_sweep.py) / .json(scratch) | VINS 分支取舍跨全组扫描(见 §10) |
| [sigma_policy_sweep.py](sigma_policy_sweep.py) | 质量门策略 / 尺度权重 / 姿态节点密度扫描(见 §11) |
| [zero_branch_check.py](zero_branch_check.py) | 把 onboard 分支真正压到 0 的单变量实测(见 §10.3) |
| [cap_sweep.py](cap_sweep.py) | 修正幅度上限扫描(见 §13) |
| [scale_sweep.py](scale_sweep.py) | `--docker2-scale-weight` 上界 + `--auto-docker2-scale-weight` 条件式投票(见 §13) |
| [rerun_q.py](rerun_q.py) | 只重跑 Q 一组; 兼作确定性校验(18/18 逐位一致) |
| [spike_floor.py](spike_floor.py) | **19 个配置的逐项 max 地板** —— 回答"尖峰还能压到多少"(见 §14) |
| [vins_global_scale_gap.py](vins_global_scale_gap.py) / [.json](vins_global_scale_gap.json) | VINS 全局 vs 局部尺度缺口, 含 Umeyama 自校验(见 §12.1) |
| [despike_test.py](despike_test.py) | 去尖峰+插值假说检验 |
| `*_probe.py`, `f1.py`, `fps.py`, `cmp.py`, `crosscheck.py` | 排查过程中的一次性探针 |

> `zero_branch_check.py` 与 `cap_sweep.py` 是薄包装: 只覆盖 `sigma_policy_sweep` 的
> `SCRATCH` 与 `CONFIGS` 再调它的 `main()`。因此它们的逐项 JSON 沿用了上游的文件名
> `spol.json`, 但各自落在自己的 scratch 目录(`.../zb/`、`.../cap/`)下, 不会串。

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

## 10. VINS 分支取舍: 现役 A 组就是最优解 (已实测, 不再是"待决")

问题(原 §9 第 1 条): 现役用 `--docker2-local-weight 0.25` 换来 1 条达标轨迹,
代价是 9 项质量门 REJECT。**把权重退回 0 是不是更好?**

[vins_weight_sweep.py](vins_weight_sweep.py) 在全 18 项上单变量扫了 4 个配置
(前端/标定输入逐字节冻结, 只动尾段参数):

| 配置 | 可比 | **达标** | **REJECT** | max 中位 | rmse 中位 | **max 最差** |
|---|---|---|---|---|---|---|
| **A: `--docker2-local-weight 0.25`(现役)** | 18 | **1** | 9 | 14.57 | 5.53 | **24.16** |
| B: `--docker2-local-weight 0` | 18 | 0 | 9 | 14.41 | 4.99 | 29.79 |
| C: `--docker2-local-weight 0.125` | 18 | 0 | 9 | 14.46 | 5.22 | 26.43 |
| D: `--relative-motion-sigma-m 0.020`(放松 VINS 运动约束) | 18 | **1** | 9 | 15.09 | 5.57 | 23.70 |

### 10.1 三条决定性证据

**(1) 唯一那条达标轨迹对 VINS 权重是单调的** —— 权重越低越差:

```
v11b3/group2 tight   A(w=0.25)  max  9.79  rot 1.89  → PASS
                     C(w=0.125) max 10.31  rot 1.92  → FAIL
                     B(w=0)     max 10.77  rot 1.96  → FAIL
```

**B 与 C 都丢掉这条轨迹。** 注意这与 §5 "元凶是 VINS" 并不矛盾:
VINS 在**全局**上是最差的一条链(尺度可差 37%), 但在融合的鲁棒机制
(节点图 + 尺度修正 + 上限截断)兜住之后, 在**这条好轨迹**上多给一点
VINS 位置权重反而是净收益。全局结论不能外推到单条。

**(2) 退回 0 一个 REJECT 也省不掉**: 四组配置的 REJECT 数**全是 9**。
机制见 §4.2 —— `--adaptive-local-weight` 的 `np.clip(..., 0.02, 0.98)`
把有效权重顶在 0.02, 质量门照样判定"用了 onboard 分支"。
[zero_branch_check.py](zero_branch_check.py) 把 local-weight 与 adaptive **两个开关
一起关**(等价于 G1 的 `weight == 0`)再测一遍, 结果见 §10.3。

**(3) B 的最差情况明显更坏**: max 最差 29.79mm vs A 的 24.16mm(差 5.6mm)。
B 只是把中位数磨平了一点点(14.41 vs 14.57) —— 典型的"用尾部风险换中位数",
对本项目不可接受。

### 10.2 结论与建议

- **保持现役 A 组不变**(`--docker2-local-weight 0.25`)。
  回退到 B 的收益是 0(REJECT 不减、中位几乎不动), 代价是丢掉唯一达标轨迹
  且最差情况恶化 5.6mm。**§9 原第 1 条"待用户决定"就此关闭。**
- **D 组可作为备选**: 达标数与 A 持平(同一条轨迹, max 9.80 vs 9.79),
  max 最差最好(23.70), 姿态略优(1.85 vs 1.89), 但中位三项都略差。
  若后续更看重"最坏组别"而非"典型组别", 可考虑切 D; 本次**不改**。
- 质量门的策略问题(`visual_position_sigma` 方向、姿态节点密度)另见 §11。

### 10.3 实测: 把两个开关**一起**关掉 (B2) —— 省掉 7 个 REJECT, 但赔掉唯一达标轨迹

§4.2 断言"9 个 REJECT 的成因是 `--adaptive-local-weight` 的 0.02 地板, 不是 0.25 这个值"。
[zero_branch_check.py](zero_branch_check.py) 直接把它证了: 同时关掉 `--docker2-local-weight`
(设 0)与 `--adaptive-local-weight`, 有效权重变成**真 0**(G1 的语义), 全 18 项重跑。

| 指标 | A(现役) | **B2(w=0 且无自适应层)** |
|---|---|---|
| **达标数** | **1** | **0** ← 丢掉唯一那条 |
| **质量门 REJECT** | 9 | **2** |
| max 中位 | 14.57 | 14.49 |
| rmse 中位 | 5.53 | 4.97 |
| max 最差 | 24.16 | **30.48** |
| 逐项 Δmax | — | 改善 10 / 恶化 8, 中位 **−0.39**, 最差 **+6.32** |

**预判命中**: 跑之前就由代码判据推出"REJECT 应从 9 降到 2, 且剩下的两项是
`batch5/group3` 的 sparse+tight" —— 实测**逐项一致**。剩下那 2 项是被另一条分支
`primary_shape_not_independently_supported` 拒的(双目 rmse 3.93/3.67mm ≥ 门 0.875×4mm=3.5mm),
**该分支根本不看 `position_branch_weight`** ⇒ 与权重无关, 关开关救不了。

三项读数:

1. **§4.2 的机制断言成立**: 9 个 REJECT 里 **7 个**由自适应层的 0.02 地板造成,
   与 0.25 这个取值无关。这条从"读代码推断"升级为"实测确认"。
2. **但省 REJECT ≠ 变好**。丢掉的那条正是 §10.1 里对权重单调的 `v11b3/group2/tight`:
   9.79 → **10.86**(+1.07, 越过 10mm 门)。这是"为了少 7 个 REJECT 而赔掉全部达标轨迹"。
   最差情况同时恶化到 30.48(`collective_batch4/group1/tight` 24.16 → 30.48, **+6.32**)。
3. **姿态确实普遍变好, 但不是免费的**: 原先被 REJECT 的 7 项姿态显著下降
   (2.62→1.99、3.09→2.19、2.58→1.90、2.41→1.98), 因为去掉 VINS 分支后姿态只由
   视觉+IMU 决定; 但**原先 OK 的项姿态反而略升**(1.95→2.03、1.87→1.89、1.89→1.96)。
   误差与姿态是两笔账, 不能互相顶替。

**结论: 不采纳 B2。** 有效权重保持 0.25 + 自适应层。现役 A 组在 18 项上仍是唯一
能出达标轨迹的配置; 9 个 REJECT 是"贵但值得"的代价, 不是 bug。

---

## 11. 其余策略扫描: 尾段已收敛 (7 配置 × 18 项)

§10 的 [vins_weight_sweep.py](vins_weight_sweep.py) 只动了 VINS 权重一条轴。
[sigma_policy_sweep.py](sigma_policy_sweep.py) 再把 §5 里标为"方向存疑"的策略、
尺度权重、G2↔G1 的 cap 模式差异、姿态节点密度各单变量扫一遍。
每个配置都已回读 `graph_fusion_report.json` 确认**覆盖真的生效**
(`correction_cap_mode` / `orientation_node_stride` / `visual_position_sigma.enabled`)。

| 配置 | 可比 | 达标 | REJECT | max 中位 | rmse 中位 | max 最差 | rot 中位 |
|---|---|---|---|---|---|---|---|
| A 现役基线 | 18 | 1 | 9 | 14.57 | 5.53 | 24.16 | 2.29 |
| E 关 `--auto-visual-position-sigma` | 18 | 1 | 9 | 14.28 | 5.57 | 24.56 | **2.17** |
| F `--docker2-scale-weight 0` | 18 | 1 | 9 | **15.64** | 5.45 | 24.08 | 2.29 |
| G E+F | 18 | 1 | 9 | 14.86 | 5.72 | 24.46 | **2.17** |
| H `cap-mode global`(=G1 的模式) | 18 | 1 | 9 | 14.57 | 5.65 | 24.16 | 2.29 |
| I `--orientation-node-stride 5` | 18 | 1 | 9 | 14.48 | 5.48 | 24.13 | 2.26 |
| J `--orientation-node-stride 20` | 17 | 1 | 8 | 14.13 | 5.02 | 24.04 | 2.24 |

**七个配置的达标数全是 1** —— 都是 §0 那条 `v11b3/group2` tight。
达标轨迹对这个参数面**完全稳健**(最好/最差配置下 max 9.35–9.96, 全在门下)。

逐项(相对 A)看每个旋钮到底动了什么, 比看中位数清楚:

| 配置 | max 好/同/差 | 姿态 好/同/差 | max 中位变化 |
|---|---|---|---|
| E 关 auto-sigma | 5 / 7 / 6 | **8 / 7 / 3** | +0.000 |
| F scale_w=0 | 9 / 0 / 9 | 0 / 16 / 2 | +0.006 |
| G E+F | 7 / 0 / 11 | **8 / 7 / 3** | +0.165 |
| H cap=global | **0 / 15 / 3** | 2 / 15 / 1 | +0.000 |
| I stride=5 | 6 / 1 / 11 | **10 / 1 / 7** | +0.030 |
| J stride=20 | 8 / 1 / 8 | 7 / 6 / 4 | −0.000 |

四条读数:

1. **cap 模式几乎没有影响** —— H 组 18 项里 **15 项 max 逐位相同**, 姿态 15 项相同。
   ⇒ §3 里 G1→G2 的 PASS/FAIL 翻转,**不是 `--joint-correction-cap-mode` 造成的**,
   而是第 [8] 步(`--docker2-local-weight` 0→0.25 + `--adaptive-local-weight`)。这条把 §3 钉死了。
2. **`--auto-visual-position-sigma` 的方向是对的, 我此前的怀疑不成立。**
   §5/§9 里我把"分歧时放宽视觉 sigma"标为"与证据相反、方向存疑", 实测**关掉它更差**:
   E 组虽把姿态中位从 2.29 拉到 2.17(8 好 3 差), 但 max 最差从 24.16 恶化到 **24.56**,
   rmse 中位也略升。⇒ **该策略在帮忙, 保留**。(这条更正写在这里, 原判断见 §5 末。)
3. **尺度权重是有用的**: F 把 `--docker2-scale-weight` 归零后 max 中位 **15.64**(比 A 差 1.1mm),
   且 max 是纯洗牌(9 好 9 差), 姿态几乎不动(0 好 16 同)。
   ⇒ 融合是在**修正** VINS 的尺度, 把这条通道关掉等于放弃修正 —— 与"VINS 尺度不可信"
   的直觉相反, 但机制上讲得通。
4. **姿态节点密度是唯一对姿态有实质作用的旋钮**(I: 10 好 1 同 7 差), 但幅度很小
   (rot 中位 −0.007°), 不足以把 `v11b3/group2/sparse` 的 **2.08°** 推过 2.0 的门。

**结论: 没有任何一个配置是全面更优的。** 七个异构配置的中位数全落在 ~1mm 带内,
达标数一个不增不减。**尾段已经收敛** —— §3 的瓶颈不在融合尾段参数, 在 §5 的 VINS 上游。

> 数据卫生: J 组在 `batch5/group3/sparse` 上第 [7] 步返回 rc=3 且未落盘(stderr 为空),
> 该组因此只有 17 项可比。这是**未查明**的异常, 单列在此不掩饰; J 未被采纳, 不影响结论。

---

## 12. 待办 / 未决

- **VINS 侧: 验收门缺尺度项** —— 建议加一条进 `run_acceptance`。
  为什么现在漏得掉, 值得记一笔: 报告里与 VINS 有关的诊断量都是**短时相对运动**的
  (`relative_motion_rmse_before_m` 1.7–6.2mm, `relative_motion_initial_disagreement_p95_m`
  3.7–12.8mm, 全部远低于 50mm 门) —— **VINS 的短时相对运动拟合得很紧,
  坏的是全局度量尺度**(可差 37%)。整条链路都在查相对运动, 没人拿全局尺度去比一个
  度量源。而 `metric_scale_consistency` 只比 IMU↔双目(`relative_difference` 2.3%/门15%),
  **完全不含 VINS**。
  ⇒ 建议: 加一条 VINS 全局尺度 vs 双目米制尺度的自洽检查。

  **补强证据(2026-09-19 复核 `graph_fusion_report.json`)**: 现有尺度检查
  `metric_scale_consistency` 比的 `imu_scale` 与 `stereo_scale` 是**同一条米制链**的两种
  估计(都以 MASt3R 单位为单位), 二者几乎完全一致 ——

  | 口径 | n | 相对差中位 | 相对差最大 | 超门(0.15) | imu/stereo 比值范围 |
  |---|---|---|---|---|---|
  | 18 项扫描样本内 | 18 | 0.024 | 0.062 | **0/18** | 0.944 ~ 1.065 |
  | 全部有报告的 | 22 | 0.024 | 0.093 | **0/22** | 0.911 ~ 1.065 |

  (两行都给, 因为 §3.1 的 18 项口径不含 `holdout_batch2`; 两行结论相同。)

  也就是说 **IMU 与双目在尺度上互相印证得很牢** —— 它们俩谁都不飘, 飘的是
  **VINS/Docker2 那条独立的相对链**。真正跨度大的量是 MASt3R 单位本身
  (0.302→0.690, 极差/中位 0.89), 但那只是"单位换算", 不影响几何。
  ⇒ 建议加的那条检查, 比对对象应当是
  **VINS 全局尺度 ↔ 上述已自洽的 IMU/双目米制尺度**, 而不是再在 IMU↔双目内部加码。

- ~~融合侧: `visual_position_sigma` 在 VINS 分歧时放宽视觉权重, 方向可疑, 需单独验证。~~
  **已结案(§11 配置 E)**: 实测关掉它反而更差(max 最差 24.16→24.56) ⇒ 该策略有效,
  我原先的"方向可疑"判断是错的, 已就地更正(见 §5 末尾的反悔块)。
- **`--auto-docker2-scale-weight`(条件式尺度投票)已测** —— 见 §13。
- **修正幅度上限(cap)已测** —— 见 §13。
- 未做: 在用户新录数据上验证; 换干净回路数据集绕开 tracker 假象。

### 12.1 把建议落成判据: VINS 全局尺度缺口 (已离线标定)

[vins_global_scale_gap.py](vins_global_scale_gap.py) 把这个量算出来了。
**全程不碰真值**(Lighthouse 仅评测用), 只用两样**在机**产物:

```
s = 相似变换(VINS 的 vio_corrected_stream.csv  →  MASt3R 的米制轨迹) 的尺度因子
```

Umeyama 每次运行都先做合成数据自校验(历史上这里踩过转置坑): 真值 s=1.37
回收 1.370000000、旋转误差 1.5e-14°、残差 1.8e-15 m ✓。全 19 项结果:

| 指标 | 值 |
|---|---|
| 中位 | **1.0011** |
| 范围 | 0.6282 ~ 1.1012 |
| 偏离 >5% | 10/19 |
| 偏离 >10% | **3/19** |

**决定性的一组是 `collective_batch4/group1`** —— 同一组里, 全局与局部给出完全相反的读法:

```
                        全局尺度   局部(1s窗)比中位   拟合 p95
collective_batch4/g1 sparse   0.6282      0.9863        0.160 m
collective_batch4/g1 tight    0.6450      1.0166        0.165 m
```

**局部比值 ≈ 1**(VINS 的短时尺度是对的), **全局尺度 0.63**(整体延伸差 37%)。
现有验收门里所有与 VINS 有关的量都不是短时就是无尺度
(`relative_motion_alignment` 的 `alignment` 字段写死 `..._no_scale`,
互补段的 `docker2_to_mast3r_ratio` 是 `--scale-horizon-s 1` 的短窗量, 对同组报 1.005)
⇒ **它们结构上就看不见这个缺口**, 不是阈值调得松。

**交叉验证**: `vins_scale_test.json` 里用真值相似对齐独立算出的 VINS 隐含尺度是
**0.6265**, 与本脚本的 0.6282 吻合到 **0.3%**。(真值只用于**验证**这条判据,
判据本身不需要真值。)

> **但不要直接当硬门用**, 两条实测反证:
> 1. **融合段本来就兜得住它** —— 同一组 `collective_batch4/group1` 的**融合产物**
>    隐含真值尺度是 1.0094(正常)。硬拒这一组会扔掉一个融合后没问题的会话。
> 2. **唯一达标的 `v11b3/group2/tight` 这条, 全局尺度是 1.1012**(偏 10%)。
>    若把门限拍在 10%, 第一个被拒的就是当前唯一能达标的轨迹。
>
> ⇒ 建议分两步: 先作为 `run_acceptance` 里的**诊断量与告警带**上线(阈值在这份
> 19 项分布上标定, 不拍脑袋); 等累积到"某组确实因此产出坏轨迹"的证据, 再谈升级为硬拒。

---

## 13. 最后两根没扫过的轴: 修正幅度上限 与 尺度权重上界

§11 扫完七条策略后, 尾段还剩两根**从未动过**的轴。本节把它们扫完, 用来回答
"尾段到底是真的收敛了, 还是只是没找对旋钮"。

### 13.1 修正幅度上限 (`--joint-max-correction-mm` / `--full-rate-max-correction-mm`)

现役默认 `joint 25` / `full-rate 20`。[cap_sweep.py](cap_sweep.py) 两个方向各推一档:

| 配置 | 可比 | 达标 | REJECT | max 中位 | rmse 中位 | max 最差 | rot 中位 |
|---|---|---|---|---|---|---|---|
| **A: 默认 joint 25 / full-rate 20** | 18 | **1** | 9 | 14.57 | 5.53 | 24.16 | 2.29 |
| K: `joint 40`(放松) | 18 | 1 | 9 | 14.57 | 5.54 | 24.16 | 2.29 |
| L: `joint 15`(收紧) | 18 | 1 | 9 | 14.54 | 5.80 | 25.38 | 2.29 |
| M: `full-rate 10`(收紧) | 18 | 1 | 9 | 14.29 | 5.54 | 24.16 | 2.29 |

逐项 delta vs A:

```
K(joint 40)      18 项: 改善 0 / 恶化 3 / 持平 15   中位 +0.00   最差 +0.11
L(joint 15)      18 项: 改善 7 / 恶化 6 / 持平  5   中位 +0.00   最差 +6.84
M(full-rate 10)  18 项: 改善 3 / 恶化 0 / 持平 15   中位 +0.00   最差 +0.00
```

三条读数:

1. **默认值坐在一段"平区"里**。把 joint 上限从 25 **放松到 40**, 18 项里 **15 项逐位相同**,
   另外 3 项只差 0.0x~0.1mm 且全是变差 ⇒ **默认的 25 根本没被顶到**, 不是一个紧约束。
2. **收紧到 15 才真正顶到**(L: 6 项变差, 最差 **+6.84**), 而且是净亏。
   ⇒ 修正幅度的合理区间在 25 以上, 现役取值安全。
3. **M 是唯一略有收益的**: 把 full-rate 上限从 20 收到 10, 15 项逐位相同、3 项变好
   (`batch5/group4/sparse` −0.61、`group1/sparse` −0.29、`group3/sparse` −0.05),
   **无一项变差**; 但那 3 项的姿态同时略升(0 改善 / 3 恶化) ⇒ 又是一笔平移换姿态的账,
   幅度都在亚毫米/百分之几度, 不构成采纳理由。

**结论: 与 §11 一致 —— 上限轴也是平的。 四组配置的达标数(1)与 REJECT 数(9)
一个不动, 再次说明尾段参数动不了 §3 的瓶颈。**

### 13.2 尺度权重上界 (`--docker2-scale-weight` 0.475 → 0.70 / 1.00) 与条件化投票

§11 的 F 组只测了尺度权重**归零**(w=0), 结果更差(max 中位 14.57→15.64)⇒ 借 VINS
尺度这件事本身是赚钱的。于是剩下唯一没测的方向: **借得更多会不会更好**。
[scale_sweep.py](scale_sweep.py) 补上这根轴, 外加代码里存在、现网未启用的
`--auto-docker2-scale-weight`(条件化尺度投票)。

| 配置 | 可比 | 达标 | REJECT | max 中位 | rmse 中位 | max 最差 | rot 中位 |
|---|---|---|---|---|---|---|---|
| **A: 无条件借 0.475 (现役)** | 18 | **1** | 9 | **14.57** | 5.53 | 24.16 | 2.29 |
| N: 借 0.70 | 18 | **0** | 9 | 14.72 | 5.81 | 24.20 | 2.29 |
| P: 借 1.00(用 VINS 短时窗尺度) | 18 | **0** | 9 | 15.83 | 6.33 | 24.25 | 2.28 |
| Q: `--auto-docker2-scale-weight` | 18 | 1 | 9 | 15.64 | 5.50 | 24.08 | 2.29 |

逐项 delta vs A(max, mm; 负 = 更好):

```
N (w=0.70)   18 项: 改善 5 / 恶化 13 / 持平 0   中位 +0.11   最好 -0.95   最差 +1.50
P (w=1.00)   18 项: 改善 5 / 恶化 13 / 持平 0   中位 +0.38   最好 -1.73   最差 +3.76
Q (auto)     18 项: 改善 8 / 恶化  9 / 持平 1   中位 +0.01   最好 -2.28   最差 +2.33
```

**读数一: "借更多尺度"单调更差。** N 与 P 都**丢掉了唯一的达标项**(`v11b3/group2/tight`
9.79 → 超门), P 的中位从 14.57 劣化到 15.83、rmse 中位 5.53→6.33。
⇒ 0.475 坐在良好的一侧, **不是"还没调够", 而是已经过了最优点往另一边走了**。
这也和 §12 的根因自洽: VINS 的**全局**尺度可低到 0.628, 借得越多就把越多 VINS 的
尺度误差灌进融合结果。

**读数二: `--auto-docker2-scale-weight` 的条件在 18 项里只放行 1 项。**

| | 触发条件 | 18 项实测 |
|---|---|---|
| `stereo_imu_consistent` | 双目/IMU 尺度相对差 ≤ 0.03 | **4 项不过** |
| `second_vote_is_informative` | 跨链尺度相对差 ≥ 0.02 | **16 项不过** ← 主要卡点 |
| `trajectory_shapes_are_compatible` | 两链形状差 ≤ 0.05 m | 全部通过 |
| `stereo_geometry_is_reliable` | 双目几何 rmse ≤ 0.004 m | 全部通过 |

唯一放行的是 `20260915_batch5_four_videos/group1/sparse`, 且它选出的权重正好是
**0.475** —— 与现役默认值相同。**逐项比对确认: Q 在 17 项上与"尺度归零"(F 组)逐位相同,
在 1 项上与 A 逐位相同, 无一例外**:

```
Q == F_scale_w0      : 17/18
Q == A_g2_baseline   :  1/18
两者都不是            :  0
```

⇒ 这个开关在当前数据上的**实际语义 = "把尺度权重几乎全面归零"**, 而 §11 已实测
归零在中位(+1.07)与最差(+1.22)上都更差。**结论: 不启用。** 其判据(要求跨链尺度差
≥2% 才认为 VINS 那一票有信息量)与 §12 的发现——跨链尺度的**全局**分歧普遍远大于 2%
而**短时窗**分歧很小——正好错位: 它用短时窗量去判断一件全局的事。

### 13.3 上限轴总结

§11(七条策略)+ §13(两轮上限扫描, 共 11 个新配置 × 18 项)合起来:
**尾段融合的超参已经被扫穿**。没有任何一个单配置能把达标数抬过 1, 或把 REJECT
压到 9 以下; 所有"改善"都伴随同量级的"恶化", 属于噪声而非信号。

---

## 14. 尖峰(max 误差)到底能压到多少 —— 逐项地板

这是"还能优化到什么程度"的直接答案。[spike_floor.py](spike_floor.py) 把本目录**全部
19 个(扫描 × 配置)组合**汇总, 对每一项取"所有配置里最好的一次 max":

| 项 | 地板 | 现役 A | 最差配置 | 地板由谁取得 |
|---|---|---|---|---|
| `v11b3/group2/sparse` | **8.27** | 8.33 | 8.77 | vsw:D (w=0.250) |
| `v11b3/group2/tight` | **9.35** | 9.79 | 10.86 | spol:F (尺度归零) |
| `batch5/group2/sparse` | **9.96** | 10.66 | 11.63 | vsw:C (w=0.125) |
| `batch5/group2/tight` | 10.36 | 11.31 | 15.27 | scale:N (w=0.70) |
| `batch5/group4/tight` | 11.26 | 13.19 | 16.95 | zb:B2 (两开关同关) |
| `v10/group1/sparse` | 12.53 | 12.96 | 13.24 | zb:B2 |
| `batch5/group4/sparse` | 12.83 | 14.29 | 15.77 | zb:B2 |
| `v11b3/group1/sparse` | 13.02 | 13.06 | 13.57 | spol:J (stride 20) |
| `v10/group1/tight` | 13.39 | 13.74 | 14.00 | zb:B2 |
| `collective/group2/sparse` | 14.40 | 16.08 | 16.23 | spol:E |
| `collective/group1/sparse` | 14.60 | 14.85 | 18.81 | spol:I (stride 5) |
| `batch5/group1/tight` | 16.26 | 18.54 | 25.38 | spol:F |
| `batch5/group1/sparse` | 17.01 | 19.32 | 22.07 | spol:F |
| `v10/group2/sparse` | 17.30 | 18.61 | 20.03 | zb:B2 |
| `v10/group2/tight` | 17.41 | 19.08 | 22.73 | zb:B2 |
| `batch5/group3/tight` | 18.54 | 20.37 | 21.75 | zb:B2 |
| `batch5/group3/sparse` | 20.21 | 22.30 | 23.85 | zb:B2 |
| `collective/group1/tight` | **23.70** | 24.16 | 30.48 | vsw:D |

门限 10mm。**地板 ≤10mm 的只有 3/18 项**; 另外 **15 项的地板在 10.36 ~ 23.70mm**,
即**无论尾段参数怎么调都过不了门**。地板中位 13.89mm。

三条结论:

1. **尖峰的可优化空间已经基本用尽。** 现役 A 到地板的平均余量只有 ~1.4mm,
   而地板本身离门限还差得很远(中位 13.89 vs 门 10)。想靠尾段再压, 最多再挤出
   一点边角料, **不可能把 18 项做达标**。
2. **"每项各挑最好配置"是不可部署的过拟合。** 看上表最后一列: 地板分别由
   `zb:B2`(8 项)、`spol:F`(3 项)、`vsw:D`/`vsw:C`(3 项)、`spol:I`/`spol:J`(2 项)
   等**互相矛盾**的配置取得 —— B2 是"两个开关都关", F 是"尺度归零", D/C 是"改 VINS 权重",
   它们不可能同时生效。上表只是回答"还有没有余地", **不是**一个可选方案。
3. **真正的瓶颈在上游, 不在尾段。** 与 §3/§5/§12 完全一致: 卡住的是 VINS 的度量尺度
   与 REJECT 掉 9 项的输入质量门, 那都是**融合之前**的事。尾段 19 个配置的达标数
   全部 ≤1, 已经把这条路的尽头画出来了。

**当前唯一达标轨迹仍是 `v11b3/group2/tight`, max 9.79mm(余量 2.1%)、姿态 1.886°
(余量 5.7%)** —— 而且它的地板只有 9.35mm, 说明这**一条**也是贴着门限过的, 不是稳的。

---

## 15. 新录制(09-17/09-18 的 loop1 会话)能不能用?

上面 §3~§14 全部跑在 09-14/09-15 那批老数据上。**09-17、09-18 新录了一批 `loop1` 闭环
会话**(共 10 条, 见 `reports/lighthouse_umi_sessions/*loop1*`), 本节回答"新录制跑了没、
精度如何"。

### 15.1 处理状态

10 条里 **5 条被送进流水线**、**3 条产出了 `precision.md`**:

| 处理产物 | ATE RMSE | ATE P95 | ATE 最大 | 姿态 RMSE | Sim(3) 尺度 | 判定 |
|---|---|---|---|---|---|---|
| `233028_720p_loop1_720p_arm` | 9.977 | 15.935 | 22.888 | 5.118° | 0.999222 | **FAIL** |
| `233028_..._arm_defaultcfg` | 10.173 | 16.281 | 22.904 | 5.184° | 0.999119 | **FAIL** |
| `235329_720p_loop1_..._defaultcfg` | 10.179 | 20.094 | 23.997 | 5.028° | 1.001250 | **FAIL** |

`225755_848x480_loop1_848_arm` 与 `232734_848x480_loop1_848_arm` 只有会话目录、
没有精度报告; 其余 5 条从未处理。

### 15.2 但这三个数字**不可采信** —— 真值本身是坏的

用 §14/§12 之外的另一道门 `scripts/lighthouse_tracker_branch_gate.py` 扫全部 loop1
会话, **9 条有 tracker 数据的会话里 8 条 REJECT**:

| 会话 | 平移步长 P95 | 最大单步 | 判候选阈值 | 结论 |
|---|---|---|---|---|
| `224105_848x480_loop1` | 1.567 | 15.615 | 4.700 | REJECT |
| `225755_848x480_loop1` | 1.685 | 9.326 | 5.054 | REJECT |
| `231323_848x480_loop1` | 1.719 | 19.600 | 5.157 | REJECT |
| `232734_848x480_loop1` | 1.840 | 6.514 | 5.520 | REJECT |
| `233028_720p_loop1` ★ | 1.905 | 13.028 | 5.715 | REJECT |
| `235329_720p_loop1` ★ | 2.050 | 16.920 | 6.151 | REJECT |
| `002711_720p_loop1` | 1.557 | 10.911 | 4.670 | REJECT |
| `003358_720p_loop1` | 1.449 | 8.901 | 4.346 | REJECT |
| `232851_720p_loop1` | — | — | — | 缺 `tracker.csv`, 无法判 |
| `222403_848x480_loop1` | 0.007 | 0.911 | 3.000 | PASS(降级判定, 见下) |

★ = 上表有精度报告的那三条。

这正是 [lighthouse-tracker-branch-switch](../../../../.claude/projects/-home-robot----ego-vio-calib-kit/memory/lighthouse-tracker-branch-switch.md)
记录的失效模式: tracker 求解器在某一帧跳到永久不同的位姿分支, 8ms 内平移 6~20mm 且不回位,
把真值切成互不连通的两段。**单次刚体 SE(3) 对齐只能在两段之间折中, 于是整条 ATE 被抬到
~10mm、最大被抬到 ~23mm** —— 那 22.9/23.0/24.0 的尖峰是**真值伪影, 不是 SLAM 的误差**。
三个 `precision.md` 的 FAIL 判定里, **姿态 5.0~5.2°(门 2°)这一条最重**, 而姿态分支跳变
同样会直接污染姿态真值, 所以姿态数也不能用。

**唯一的 PASS 也是降级的**: `222403_848x480_loop1` 缺 `d405_frames.csv`, 门脚本只能
退化成"扫整条 tracker + 固定阈值 3.000mm"(*"无法把跳变映射到相机帧"*), 等于**没有做
真值-相机窗口的映射** —— 而 §12 的结论恰恰是"判 take 真值必须把姿态跳变映射到相机窗口"。
而且它只有 **3571 个 tracker 采样**(其余都是 7400~7580), 是个明显更短的 take。
⇒ **目前没有任何一条 loop1 会话拥有可信的真值。**

### 15.3 结论与下一步

- **新录制跑了, 但精度数字不可用** —— 不是 SLAM 变差了, 是这批 take 的真值被 tracker
  分支跳变污染(9 条里 8 条)。
- Sim(3) 最优尺度都在 0.999~1.001, 说明**这几条的融合链本身的全局尺度是好的**;
  这与 §12 里 VINS 全局尺度可低到 0.628 是两回事(VINS 是上游输入, 不是融合输出)。
- **要拿到可信的新精度数字, 只有两条路**:
  1. **重录一条干净 take** —— 用 `lighthouse_tracker_branch_gate.py` 当场验, 过了再跑流水线;
  2. 接受真值噪声, 但把 ATE 对齐**按分支分段做**(每段各自 SE(3)), 而不是全条一次对齐。
- 在此之前, **本报告 §3~§14 的 18 项结论仍然只建立在 09-14/09-15 那批老数据上**,
  新数据暂不并入。
