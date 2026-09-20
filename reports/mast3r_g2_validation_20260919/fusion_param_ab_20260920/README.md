# 融合尾段参数 A/B：09-14 的「好结果」到底是什么（2026-09-20）

**一句话**：09-14 那批「精度好」的轨迹，**融合什么都没做** —— 它是 MASt3R 图优化
轨迹本身过了一遍 `camera_to_body`。现役把 VINS 局部注入拧到 0.25 是**净负贡献**，
而且这份注入在 8 个 cell 上**单调有害**。复原 = `--docker2-local-weight 0`。

---

## 一、结论先摆（8 cell，融合链，官方评测器）

| 配置 | 中位 ATE | 中位 门 | 门过 2.0° |
|---|---|---|---|
| **现役** `lw.25 sw.475 adaptive` | 5.78 mm | 2.38° | 3/8 |
| **09-14 复原** `lw0 + --auto-docker2-scale-weight` | **5.46 mm** | **2.06°** | **4/8** |
| `lw0 + sw.25`（扫描最优） | 4.90 mm | 2.06° | 4/8 |

09-14 复原那一行**与独立算出的 `graph(→body)` 逐位吻合**（5.46 / 2.06° / 4-8），
两条互不相干的路径交叉验证通过。

**已应用**：`scripts/mast3r_slam_precision_workflow.sh` 第 [8/9] 步改回备份脚本
（`mast3r_slam_precision_workflow.sh.bak_20260918_1435_pre_l025`）的三个参数，
三条参数逐字相同。

---

## 二、代码级推导：为什么 `local_weight=0` 等于「直通」

`fuse_docker2_mast3r_complementary.py` 的核心只有三行：

```python
scaled_base   = base[0] + scale_ratio * (base - base[0])          # base = MASt3R 图
disagreement  = aligned_metric - scaled_base                      # VINS − MASt3R
fused         = scaled_base + local_weight * (disagreement - low_frequency)
```

而 `scale_ratio = docker2_scale_ratio ** docker2_scale_weight`
（`joint_log_scale_ratio`）。所以 **`docker2_scale_weight = 0.0` ⇒ `scale_ratio = x⁰ = 1.0`**，
**`local_weight = 0.0` ⇒ `fused = base`**。

`base` 就是 `camera_to_body_with_body_orientation_prior(mast3r_positions, …)`
⇒ **09-14 的 `trajectory_fused.csv` 应当逐位等于 `mast3r/trajectory_graph.csv` 过 camera_to_body。**

实测（`graph_vs_fusion.py`，8 cell）：位置差 **RMS 中位 0.187 mm**（对 5.5mm 的 ATE 是 3%），
残差来自重采样到 `common_times`；**门在 8/8 个 cell 上吻合到 0.02°**
（1.89/1.89、3.16/3.18、1.96/1.96、1.87/1.84、2.14/2.14、2.52/2.52、1.98/1.98、2.19/2.19）。
⇒ 推导成立。

**副产品**：`local_weight=0` 时 `smoothing_s` 与 `scale_horizon_s` **双双失效**
（前者只塑形被乘 0 的 `high_frequency`，后者只影响那个标量尺度）。
所以复原后的管线实质是 **「MASt3R 图 × 标量尺度 → 输出」**。

---

## 三、★ 更正：MASt3R 全程「不是度量轨迹」是坐标系错配的假象

先前记录（`mast3r-chain-topology`）称 `[6/8]` 恒等于纯缩放且**产出不是度量轨迹**
（对真值中位偏大 5.6%）。**这条是错的**，成因是我把**相机系**的
`mast3r/trajectory_*.csv` 直接拿去比 **body 系**的 `lighthouse_body_ground_truth.csv`。

两者之间差 `camera_to_body`，而该变换**不是刚性的**：

```python
body_positions = positions - body_rotations.apply(body_t_camera[:3, 3])
```

杆臂只有 **29 mm**，但它是**逐帧按姿态**减掉的 ⇒ 轨迹半径 **6.1%** 收缩
（172.29 → 161.78 mm，`similarity_align` 残差 12.2 mm 的**非刚性形变**）。

**验算**：`0.9401 / 0.9383 = 1.0019` ≈ fused 实测 `1.0016`
⇒ **图优化轨迹本来就是度量的**（`倍率` 1.001 / 1.023 / 0.989 / 1.012 / 1.004 …）。

⚠ 该 `camera_to_body` 在几何上**是对的**（`T_world_body = T_world_cam0 · T_body_cam0⁻¹`，
逐项对过），所以这不是 bug，是**评测口径错配**。凡拿 `mast3r/trajectory_*.csv`
比 body 系真值的地方都要先过这一步 —— v1 的 `ate_stage_budget.py` 就是反例。

**同一条路径还解释了另一件事**：`write_trajectory(…, mast3r_rotations)` 里的
`mast3r_rotations` 已在 `camera_to_body_with_body_orientation_prior` 里被**重绑成
对齐后的 VINS 姿态**，视觉姿态被整个丢弃
⇒ 这才是「fused 姿态 ≡ VINS 姿态 × 常量旋转，残差 0.0000°」的**代码级原因**
（原来只是个观测，现在知道为什么）。

---

## 四、`local_weight` 扫描：单调有害

逐 cell 调**真实脚本**（subprocess，不自己复实现），只改这一个参数：

| `--docker2-local-weight` | 0.00 | 0.05 | 0.10 | 0.15 | **0.25（现役）** | 0.40 |
|---|---|---|---|---|---|---|
| 中位 ATE (mm) | **4.98** | 5.04 | 5.16 | 5.32 | 5.79 | 6.75 |
| 中位 门 (°) | **2.06** | 2.06 | 2.07 | 2.13 | 2.39 | 2.78 |
| 门过 | **4/8** | 4/8 | 4/8 | 4/8 | 3/8 | 3/8 |

**两个指标同时单调变差**，最优就在 0。`--adaptive-local-weight` 开关几乎无影响
（两组扫描逐格差 <0.15mm / <0.06°）⇒ 该机制在本语料上是**惰性的**
（`effective_local_weight` 实测只在 0.2327–0.2505 间动）。

**机理**：VINS 单链中位 ATE **24.98 mm**，MASt3R 图 **5.46 mm** ——
把一条 25mm 的链按 25% 掺进一条 5.5mm 的链，只会把它拖坏。

`--docker2-scale-weight` 的效果**弱得多**，且**门完全不敏感**：

| `--docker2-scale-weight` | 0.00 | 0.25 | 0.475（现役） | 0.75 | 1.00 |
|---|---|---|---|---|---|
| 中位 ATE (mm) | 5.46 | **4.90** | 4.98 | 5.85 | 6.76 |
| 中位 门 (°) | 2.06 | 2.06 | 2.06 | 2.06 | 2.06 |

浅最优在 0.25，但**逐 cell 胜负 4:4 混合**（`batch5/g2` 从 5.21→3.33 大幅改善，
`v10_batch/g2` 从 7.41→9.54 变差）⇒ 效应量 0.56mm 的**中位差不足以当结论**。
**不建议**据此改生产参数；`--auto-docker2-scale-weight` 才是 09-14 的原设计。

---

## 五、为什么该用 `--auto-docker2-scale-weight` 而不是写死 0.475

`select_docker2_scale_weight` 是一道**四条件安全闸**，全过才给候选权重，否则**返回 0.0**：

1. `stereo_imu_disagreement ≤ 0.03`
2. `cross_slam_disagreement ≥ 0.02`（第二个尺度投票必须**有信息量**）
3. `input_disagreement_p95_m ≤ 0.05`
4. `stereo_edge_rmse_after_m ≤ 0.004`

09-14 的 report 记着 `automatic_selection.enabled: false`
⇒ 当时**至少一条没过**，于是选了 0.0。本语料 docker2 与 MASt3R 只差 **0.44%**
⇒ 条件 2 判「无信息量」⇒ 自动选 0.0，这是**设计意图**。

**现役把 0.475 写死 = 绕过这道闸。** 复原 `--auto-docker2-scale-weight` 即恢复原设计。

---

## 六、★ `correction_cap_mode` 那一代改动：**只在裁切时才生效**

先前记录说 `[7/8]` 的 `correction_cap_mode` 被有意从 `global` 改成 `per-node`。
两份 `graph_fusion_report.json` 一比 —— **逐字段完全相同**（连
`position_correction_requested_max_m = 0.017721039795965936` 都逐位一致），
**只有 `correction_cap_mode` 一项不同**。

再逐个 cell 比产物哈希：

| cell | graph 产物 | `clipped`（global→per-node） | `req_max` |
|---|---|---|---|
| v10_batch/g1 | 逐字节相同 | 0 / 0 | 9.80 mm |
| **v10_batch/g2** | **不同** | **1799 / 53** | **37.36 mm** |
| v11b3/g2 | 逐字节相同 | 0 / 0 | 17.72 mm |
| **batch5/g1** | **不同** | **1799 / 26** | **31.73 mm** |
| batch5/g2 / g3 / g4、coll4/g1 | 逐字节相同 | 0 / 0 | 14–22 mm |

⇒ **完美相关**：cap mode 改变产物 **当且仅当** `clipped > 0`，
而裁切只在 `req_max > correction_limit (25 mm)` 时发生。**6/8 个 cell 是空转。**

两种模式的裁切语义差别很大：
* `global`：把**整个修正场等比缩小**直到最大值等于上限 ⇒ **1799 帧全中**（= 全部帧）；
* `per-node`：只裁越界的节点 ⇒ 53 / 26 帧。

**★ 由此得出一个可验证的推论**：09-14 的图（`global`）经 lw=0 融合 = **5.72 mm**，
09-20 的图（`per-node`）经 lw=0 = **5.46 mm**
⇒ **`per-node` 那一代是改进，不是回归。** 所以最优组合是**混合体**：
**保留新的 per-node 图 + 复原旧的 lw=0 融合** —— 正是本次应用的配置。

---

## 七、对「下一步」的直接含义

`local_weight=0` 之后，管线 = **「MASt3R 图 × 标量尺度 → 输出」**，
残余 **4.90–5.46 mm 全部来自 [7/8] 图优化 + 前端**，融合侧已无杠杆可动。

⇒ **要再往下压，只能攻 `fuse_mast3r_stereo_imu.py`（[7/8]）或前端。**
目标仍是（来自旋转门证伪检验）：**fused ATE ≲ 4 mm**。

[7/8] 现有可调项：
`--joint-max-correction-mm 25`、`--joint-correction-cap-mode per-node`、
`--full-rate-max-correction-mm 20`、`--relative-motion-sigma-m 0.008`、
`--orientation-node-stride 10`、`--position-node-stride 5`、`--metric-scale-mode joint`。

★ **一条可直接做的判决实验**：09-14 的 `trajectory_graph.csv` 就在盘上，
而 [7/8] 的输入（`trajectory_imu_metric.csv` + 四份 stereo report）也都在
⇒ 用候选参数集重跑 [7/8]，看哪一套能**复现**那份产物，就能定住 09-14 的代际
（`correction_cap_mode` global→per-node 那条改动到底落在哪一代）。

---

## 文件

| 文件 | 作用 |
|---|---|
| `ab_0914_vs_now.py` | 09-14 原始产物 vs 09-20 现役产物，逐 cell 比 ATE/门 |
| `ate_stage_budget.py` | ⚠ **v1，坐标系错配**（保留作反例）：把相机系阶段直接比 body 系真值 |
| `ate_stage_budget_v2.py` | 修好坐标系后的阶段预算；证明 MASt3R 图（→body）本身 5.46mm |
| `graph_vs_fusion.py` | 验证「09-14 fused ≡ 图优化轨迹」，门 8/8 吻合到 0.02° |
| `local_weight_sweep.py` | `--docker2-local-weight` 扫描（真实脚本 subprocess） |
| `scale_weight_sweep.py` | 由上一个 sed 生成，改扫 `--docker2-scale-weight` |
| `restore_0914_config.py` | 三套配置对照：现役 / 09-14 复原 / 扫描最优 |
| `error_axis_split.py` | 误差按轨迹主平面拆垂直/面内 |
| `scale_share_of_ate.py` | 残余尺度误差占 ATE 多少（中位 4%，不是杠杆） |

### 两个踩过的坑（都写进注释了）

* `np.mean(x**2)` 对 `(N,3)` 数组是**按分量**平均（除以 3N），不是按行取模
  ⇒ 会把总误差压低 √3 倍（曾误得 1.59 而非 2.76）。`error_axis_split.py` 里写成函数了。
* 自写 Umeyama 容易漏除/多除一个 n（曾得尺度 1731 倍）。
  **仓库自带 `E.similarity_align`，且已单元测试**（刚性→s=1.000000，0.9/1.1 精确）——
  直接用，别自己写。

## 复现

```bash
cd /home/robot/ego_vio_humble/reports/mast3r_g2_validation_20260919/fusion_param_ab_20260920
python3 graph_vs_fusion.py          # 09-14 fused ≡ 图优化轨迹
python3 local_weight_sweep.py       # 单调有害（约 8 cell × 6 权重 × 2 = 几分钟）
python3 restore_0914_config.py      # 三套配置对照
```

⚠ 只报融合链精度。所有 `pose_errors` 调用都用的官方评测器
`evaluate_slam_ground_truth.py`，门的定义是 `ate_rotation_rmse_deg`（**位置口径**的
`rigid_align`），已另有证伪检验证明它主要量的是位置
（见 `../rotation_gate_20260920/README.md`）。