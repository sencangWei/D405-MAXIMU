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
| [zero_branch_check.py](zero_branch_check.py) | 把 onboard 分支真正压到 0 的单变量实测(见 §10.1) |
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
- 融合侧: `visual_position_sigma` 在 VINS 分歧时放宽视觉权重, 方向可疑, 需单独验证。
- 未做: 在用户新录数据上验证; 换干净回路数据集绕开 tracker 假象。
