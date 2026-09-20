# 端到端验证（现役 [8/9] 新参数）+ [7/8] 专项

**日期** 2026-09-20 · **目的** ①把 09-20 改过的 `[8/9]` 参数放进**真实工作流阶段序列**里端到端跑一遍，
确认不阻断、并量出生产数字；②把当时唯一还卡着的门（`ate_translation_max`）打开。

`[8/9]` 的改动见提交 `e0fca6bb`（`--docker2-scale-weight 0.25`、去掉 `--use-docker2-orientation-for-lever-arm`）。

---

## 0. 文件

| 文件 | 作用 |
|---|---|
| `rerun_one_v2.sh` / `run_all_v2.sh` | ★ **端到端**：`[7/8]→[8/9]→[9/9]` 参数**逐字抄自** `mast3r_slam_precision_workflow.sh:279-345`，产物落 `fusion_v2/`。与 `rerun_tail_current_20260920/rerun_one.sh` 的差别只有本实验要验的那几项（`sw`、无 adaptive、无姿态开关、质量门**不带 `|| true`**）。 |
| `vins_dir.py` | 选该用哪份 VINS 产物（见 §2）。**这是本目录最重要的一处更正。** |
| `g7_ablation.py` | `--metric-scale-mode` ∈ {joint, stereo, imu} + 一条「跳过整个 [7/8]」臂，看尺度模式与 [7/8] 的净贡献。 |
| `g7_knob_sweep.py` | 瞄准 `ate_translation_max` 的 `[7/8]` 旋钮：`--relative-motion-sigma-m` 扫描 + 完全不用 VINS 相对运动。 |
| `max_frame_forensics.py` | 把超 `max` 门的帧逐个列出来（时间、误差、邻帧、真值速度、误差方向）。 |
| `bump_trace.py` | 同一条 cell 同一帧窗，**逐级**在 `[6/8]→[7/8]→[8/9]` 上量误差，定位鼓包来自哪一段。**所有段都先过同一次 camera→body**（原因见 §7）。 |

---

## 1. 端到端结果（`rerun_tail_v2_20260920/run_all_v2.log`）

11 条 cell × 2 臂 = 22 次；**成功 16 / 失败 6**。

| cell | 臂 | RMSE | p95 | **MAX** | w10% | rot | 失败项 |
|---|---|---|---|---|---|---|---|
| v10_batch/g1 | sparse | 3.23 | 5.03 | **12.58** | 99.89 | 1.911 | 仅 max |
| v10_batch/g1 | tight | 2.62 | 4.27 | **13.46** | 99.89 | 1.764 | 仅 max |
| v10_batch/g2 | sparse | 6.35 | 10.39 | 17.71 | 92.03 | 2.526 | p95+max+w10+rot |
| v10_batch/g2 | tight | 7.75 | 12.42 | 16.78 | 83.25 | 3.059 | p95+max+w10+rot |
| v11_batch3/g1 | sparse | 4.87 | 7.10 | **13.61** | 99.71 | 1.814 | 仅 max |
| v11_batch3/g2 | sparse | 5.16 | 8.51 | 9.64 | 100.00 | **2.017** | 仅 rot（超 0.017°） |
| v11_batch3/g2 | tight | 4.67 | 7.37 | **10.57** | 99.83 | 1.882 | 仅 max（超 0.57mm） |
| batch5/g1 | sparse | 4.96 | 8.34 | **16.56** | 98.11 | 1.869 | 仅 max |
| batch5/g1 | tight | 5.38 | 9.58 | **19.18** | 95.30 | 1.868 | 仅 max |
| batch5/g2 | sparse | 3.86 | 7.80 | **11.85** | 99.14 | 1.957 | 仅 max |
| batch5/g4 | sparse | 4.51 | 6.76 | **12.22** | 98.16 | 1.799 | 仅 max |
| batch5/g4 | tight | 4.75 | 6.80 | **11.41** | 99.48 | 1.892 | 仅 max |
| coll4/g1 | sparse | 5.70 | 11.05 | 18.43 | 91.74 | 1.917 | p95+max+w10 |
| coll4/g1 | tight | 7.54 | 12.99 | 30.20 | 89.79 | 2.121 | p95+max+w10+rot |
| coll4/g2 | sparse | 5.64 | 10.08 | 15.11 | 94.89 | 2.105 | 全部 + overlap |
| holdout_b2/g1,g2 ×2 臂 | | | | | | | §2（台架选错目录） |
| batch5/g3 ×2 臂 | | | | | | | §3.2（**[9/9] 门以 rc=3 中止整条**） |

### ★★ 结论一：唯一的主导失败项是 `ate_translation_max`

**16 次成功里 9 次「只」失败在 max**：RMSE 2.6–5.4mm、p95 多数 <10mm、w10 98–100%、
rotation 多数 <2.0°，**却被一个几十帧宽的鼓包顶掉**。
⇒ 当下提升通过率最直接的杠杆不是降 RMSE，是**削掉那个峰**。

### ★ 结論二：旋转门已经不再是主约束

去掉 `--use-docker2-orientation-for-lever-arm` 之后，16 次里只有 4 次 rot 超 2.0°
（此前记忆里的判断是「rot 门地板 2.11°、16cm 桌面轨迹上这道门是掷硬币」——
现在多数落在 1.76–1.96°，**该判断需要在新配置下重估**）。

---

## 2. ★ 更正：「holdout_batch2 两条 cell 跑不了」是**台架选错目录**，不是产品阻断

上一轮的 README / 记忆里写着：`holdout_b2/g1` 重跑失败于
`docker2_position_policy: Docker2 run is not safe ... corrected_trajectory_jump`，
并把这个当成「现役 VINS 验收门拦住了产品路径」。

**真相**：这两条 cell 盘上有**两份** VINS 产物。

| 目录 | group1 | group2 |
|---|---|---|
| `docker2_slam/` | `FAIL ['corrected_trajectory_jump']` | **空目录**（那次跑什么都没产出） |
| `docker2_slam_rate0p5/` | **PASS** | **PASS** |

而 09-14 的 `graph_fusion_report.json` 里 `inputs.relative_motion_trajectory` 记的是
**`docker2_slam_rate0p5/vio_corrected_stream.csv`**，`source_validation.source_result = PASS`
—— 09-14 对这两条正是回退到 **0.5× 回放速率**那份跑的（两份 `vins_auto_loop_config.yaml`
除 `output_path` 外逐字节相同 ⇒ 差别只在回放速率，不是配置）。
证据：`docker2_slam/` 的验收是 09-14 **18:01** 写的，而 `fusion/sparse/mast3r/trajectory_graph.csv`
是 **18:37** 产出的 —— 36 分钟后仍能跑通。

而 `rerun_tail_current_20260920/rerun_one.sh` 把路径**写死**成 `$G/docker2_slam/...`
⇒ `[7/8] validate_relative_motion_report` 抛 `input report did not pass`，
`[8/9] docker2_position_policy` 抛 `Docker2 run is not safe for complementary fusion`。

**⇒ 那不是「产品路径被阻断」，是测试台架选错了目录。** 修正见 `vins_dir.py`
（优先验收 PASS 的 `docker2_slam/`，否则取 `docker2_slam_*/` 里第一份 PASS 的）。
按该规则 13 条 cell 里 12 条选 `docker2_slam`、2 条选 `docker2_slam_rate0p5`，与 09-14 逐条一致。

---

## 3. ★★ 三处**真的会中止产品路径**的地方

### 3.1 `[7/8] validate_relative_motion_report`（`fuse_mast3r_stereo_imu.py:119`）

验收不是 `PASS` 就直接 `raise ValueError("input report did not pass")`。
落在 §2 的两条 cell 上——**但只要选对 VINS 目录就不发生**。

### 3.2 ★★ `[9/9] assess_mast3r_fusion_input_quality.py` 以 **rc=3** 中止整条（batch5/group3 两臂）

```
result: REJECT   reason: primary_shape_not_independently_supported   rc=3
```

工作流 `mast3r_slam_precision_workflow.sh:335-338` **没有 `|| true`**，而顶部是 `set -euo pipefail`
⇒ **`skill` 一退出 3，整条 `fusion)` 立刻中止，连 `trajectory_fused.csv` 都不会产生。**

⚠ **这推翻了此前的记忆结论「[9/9] 质量门不阻断（工作流从不读 `input_quality_report.json`）」**：
工作流确实不*读*那个 JSON，但 `set -e` 会因脚本的**非零退出码**中止。

### 3.3 ★★ 而且这道门是**被收紧过**的 —— 同样的输入 09-15 判 PASS，今天判 REJECT

`batch5/group3` 09-15 14:29 的 `fusion/sparse/input_quality_report.json` 是 **`result: PASS`,
`reason: null`**，而它记录的 `input_disagreement_p95_m = 0.08054` 与今天（0.08061）
**只差 7e-5**。逐字段对照，两份报告只差三处：

| 字段 | 09-15 14:29 | 今天 |
|---|---|---|
| `result` | **PASS** | **REJECT** |
| `reason` | `null` | `primary_shape_not_independently_supported` |
| `primary_shape_independently_supported` | **（字段不存在）** | `False` |
| `policy` | 「…unobservable, **when D405/stereo and visual-inertial geometry both fail**,…」 | 「…unobservable, **when an isolated primary branch also has marginal stereo geometry**, when D405/stereo and…」 |

⇒ **`assess_mast3r_fusion_input_quality.py` 在 09-15 14:29 之后新增了一条判据**
（`primary_shape_not_independently_supported`，代码在 `:88-92`）：

```python
primary_shape_not_independently_supported = (
    input_disagreement >= max_severe_input_disagreement_p95_m   # 0.05
    and position_branch_weight == 0.0
    and stereo_rmse >= 0.875 * max_stereo_edge_rmse_m           # 0.875 × 0.004 = 0.0035
)
```

batch5/g3：0.0806 ≥ 0.05 ✓、branch weight 0 ✓、stereo RMSE 0.003934 ≥ 0.0035 ✓ ⇒ **REJECT**。
注意第三条把触发带压到 `stereo RMSE ∈ [3.5mm, 4mm)` 这条**很窄的边缘带**上。

（对照：另外两条判据在此 cell 上均**不**触发，已核对 ——
`severe_shape_disagreement` 要求 `stereo_rmse ≥ 0.004` 而实测 0.003934；
`metric_and_inertial_estimates_conflict` 要求 `scale ≥ 0.12` 而实测 0.0426。）

#### ★★ 这条判据**是什么时候、为什么**加的 —— 已用 Codex 原始记录定位到秒

`scripts/` 直到 09-19 才进 git（`e419c7c7`），git **查不到**这次改动。改用 Codex 的
rollout 记录（`~/.codex/sessions/`，需**流式扫 `rollout-*.jsonl`**，
`thread_history_1.sqlite` 是投影表会漏 —— [[codex-gate-blindspot-history]]）：

* 该字符串**首次出现**：`rollout-2026-08-01T19-03-34-…jsonl` **第 151987 行**，
  `2026-09-15T07:12:32.814Z` = **本地 09-15 15:12:32**（该 rollout 单文件跨 08-01→09-16 18:24）。
  ⇒ 比 14:29 那份 PASS 报告**晚 43 分钟**，与「字段当时还不存在」**完全一致**。
* 首现动作是给 `tests/test_assess_mast3r_fusion_input_quality.py` **加测试**，
  字面量写死 `stereo_rmse_m=0.00393, disagreement_p95_mm=80.5,
  metric_scale_disagreement=0.043, full_rate_correction_m=0.0174`
  —— **正是 batch5/group3 实测的那四个数**（本目录实测 0.003934 / 80.6 / 0.0426 / 0.0174）。
  随后 `07:13:08Z` 才改 `scripts/assess_mast3r_fusion_input_quality.py` 本体。

#### ★★ 但它**不是「没人认领的静默收紧」** —— Codex 当时明确说了，且是用户授意的

Codex 在 `2026-09-15T07:12:27Z`（本地 15:12:27，**加判据前 5 秒**）的原话：

> 「第四条最终盲选完成，选择 `sparse`，结果为 **RMSE 4.989 mm、P95 8.205 mm、
> 最大 15.333 mm、97.65% 在 10 mm 内**，位置与姿态门限全部通过。四条里目前只有第三条未达标；
> 它也是唯一同时出现三项独立风险的样本：次级分支分歧 80.5 mm、双目图残差接近上限 3.934 mm、
> IMU形状修正请求 17.41 mm。21 个历史/当前候选回放中，只有这一条同时满足三项风险且 P95 超标。
> 因此我会把这一"多证据同时失效"加入发布质量门：不伪造重合轨迹，直接拒绝这类高风险样本，
> **符合你之前说的"数据本身有问题就舍弃"**。」

⇒ **结论要分两半说，不要混**：

| 说它是「回归」 | 不对 |
|---|---|
| 说它「加得没人知道」 | **不对** —— 09-15 当天就当面告知了用户，且引用了用户的既有指示 |
| 说它「有 21 组回放做特异性验证」 | 属实（Codex 的原话），且**与本目录独立复现吻合**：22 次端到端里 `[9/9]` 只挡下 batch5/g3 的两臂 |
| ★ 说它「**会把整条 `fusion)` 以 rc=3 打停、零产物**」 | **这才是真正有后果的地方**（§3.2）——「拒绝这条高风险样本」的实现方式是**中止整条流水线**，而不是「跳过该 cell 继续」或「出产物但标记 REJECT」 |
| ★ 说它「**让 09-14/15 的『跑通』在今天的同一 cell 上变成跑不通**」 | 属实 —— 谁按 cell 名回溯都会看到一个**假的回归**，因为它其实是**有意收紧**，而 git 里查不到（`scripts/` 09-19 才入库） |

**⇒ 对「复原到 09-14」这件事的实际影响**：这不是算法坏了，是**发布质量门按用户意愿收紧了**。
要复原 09-14 的**行为**，正当做法是**改采集/算法让 batch5/g3 不再同时命中三条件**，
或者**明确决定这条 cell 该不该被拒**；**不是**把判据删掉。

### 3.4 `[7/8] merge_stereo_reports`：`v11_holdout_batch3/group1/tight`

```
ValueError: stereo report scales disagree by more than 5%      (fuse_mast3r_stereo_imu.py:1244)
```

该 cell 的 **tight** 前端产物（四份 `stereo_scale_*_report.json`）尺度互相差 >5%。
`fusion/tight/` 里确实**没有** fused 产物 ⇒ 09-14 的 tight 臂在这一条上也没跑出来，
不是回归，是「tight 前端配置在这条 cell 上产不出自洽的双目尺度」。

---

## 4. tracker 位姿分支门：9/10 PASS ⇒ **真值伪影假说基本排除**

`lighthouse_tracker_branch_gate.py`（`tracker` 路径取自 `lighthouse_ground_truth_provenance.json`
的 `inputs.tracker`）在本批 10 条上：

| cell | 结果 |
|---|---|
| v10_batch/g1, g2 | PASS（0 次切换） |
| v11_batch3/g1, g2 | PASS（0 次切换） |
| batch5/g2, g3, g4 | PASS（0 次切换） |
| coll4/g1, g2 | PASS（0 次切换） |
| **batch5/g1** | **REJECT** —— 1 次切换，影响 **65.6%** 相机帧（步长 5.15mm，回跳 5.88mm @ t=618474.260s） |

**⇒ §1 那一大片 `max` 失败不能被「真值换分支」解释掉**（只有 batch5/g1 这一条是）。
真值伪影这条假说在本批上**基本关闭**，剩下的是算法侧的真实误差。

---

## 5. ★ 鼓包来自 `[6/8]`，`[7/8]` 修好了整体却**放大了峰**

`max_frame_forensics.py`（v10_batch/g1/tight）：1743 帧里**只有 2 帧**超 10mm
（idx 468 / 469 = 12.40 / 13.46mm），而相邻帧已经 9.5mm、整条 RMSE 才 2.62mm
⇒ 是**几十帧宽的局部鼓包**，不是单帧尖峰。该处真值速度 129mm/s，
误差量级 ≈ **96ms 的真值位移**。

`bump_trace.py` 同一窗口（**各段都过了同一次 camera→body**）：

| idx | `[6/8] imu_metric` | `[7/8] graph` | `[8/9] fused` |
|---|---|---|---|
| 460 | 8.47 | **1.86** | 1.86 |
| 467 | 7.45 | 9.54 | 9.54 |
| **468** | 9.45 | **12.40** | 12.40 |
| **469** | 10.14 | **13.46** | 13.46 |
| 475 | 6.23 | **0.34** | 0.34 |
| **RMS** | **3.58** | **2.62** | 2.62 |
| **MAX** | **10.62** | **13.46** | 13.46 |

* `fused` 与 `graph` 在该窗口**逐位相同** ⇒ `lw=0` 时 `[8/9]` 确为直通（与既有结论一致）。
* **`[7/8]` 整体有益**（RMS 3.58 → 2.62，−27%，与 `g7_ablation` 的「跳过 [7/8]」臂 3.58 吻合），
  **但对鼓包过度修正**：把峰从 10.62 抬到 13.46。
* 鼓包在 `[6/8]`（前端 × IMU 标量）**就已经存在**。

⇒ **`[7/8]` 是「总体降误差、局部放大最坏点」**。要找的是能让它保留 RMS 收益、
又不抬高峰的那个旋钮 —— 见 `g7_knob_sweep.py`。

---

## 6. `[7/8]` 尺度模式：有影响，但不是主约束（`g7_ablation.log`，进行中）

| cell | 臂 | joint（现役） | **stereo** | imu | 跳过 [7/8] |
|---|---|---|---|---|---|
| v10_batch/g1 | tight | **2.62** / 1.76 | 2.67 / 1.77 | 2.81 / 1.76 | 3.58 / 1.74 |
| v10_batch/g2 | tight | 7.75 / 3.06 | **6.01** / 3.07 | 11.44 / 3.06 | 14.78 / 3.25 |
| v11_batch3/g2 | tight | 4.67 / 1.88 | **4.16** / 1.89 | 5.47 / 1.84 | 7.92 / 2.09 |
| batch5/g1 | tight | 5.38 / 1.87 | 5.47 / 1.84 | **4.81** / 2.02 | 10.70 / 2.18 |
| holdout_b2/g1 | tight | —（台架，见 §2） | — | — | **8.39** / 2.38 |

（每格 `RMSE mm / rot °`）

* **`[7/8]` 净收益明确**：跳过它一律更差（3.58 vs 2.62、14.78 vs 7.75、7.92 vs 4.67、10.70 vs 5.38）。
* **`--metric-scale-mode` 的胜负不一致**：joint 赢 v10b/g1 与 batch5/g1，stereo 赢 v10b/g2 与
  v11b3/g2，且效应量在最大那两条上才明显（7.75→6.01 = −22%）。**不足以据此改生产参数**，
  等全量结果。
* 注意 `--metric-scale-mode stereo` 在代码上还会**跳过 `imu_stereo_metric_scale_disagreement` 硬门**
  （`fuse_mast3r_stereo_imu.py:2770-2775`）——那正是 Codex 2026-09-11 定过、只接进 `compare)`
  而从没接进产品路径的策略。**这条留作候选，不是本轮的结论。**

---

## 7. ⚠ 复用的坑：相机系 / body 系（差点又误判一次）

`fusion/<arm>/mast3r/trajectory_imu_metric.csv` 与 `trajectory_graph.csv` 都在**左红外相机系**，
真值在 **body 系**。29mm 杆臂逐帧按姿态减掉 ⇒ **直接比会凭空多出 ~17mm RMS 的坐标系错配假象**
（第一版 `bump_trace.py` 就是这么写错的：`[6/8]` 一列显示 RMS 16.51mm，
而同一条 cell 的 fused 只有 2.62mm，看起来像「融合把 16mm 压到 2.6mm」的荒谬结论）。

**正确做法**：各段都过**同一次** `[8/9]`（`lw=0, sw=0.25` ⇒ 只做 camera→body + 一个标量）再比。
已记录在 [[mast3r-chain-topology]]，本目录脚本已按此实现。

另一个同源坑：`evaluate_slam_ground_truth.py` 要求**估计与真值等长**，
CLI 的做法是 `estimate_position[inside][valid]`（`:115-116`）。
少一步就会撞 `matmul` 形状不匹配 —— 这也是 `coll4/group2/sparse` 那条
未解崩溃（`1508 vs 1732`）的**同一类**症状，值得顺着查。

---

## 8. ★ 负结果：把误差「按航向分解」**部分否证**了此前的 A/B 二分

`heading_decomposition.py`，全语料 16 条（只在真值速度 > 30mm/s 的帧上分解）：

| 量 | 观测 |
|---|---|
| **沿航向占比**（RMS 口径） | **51.5% – 78.3%**，中位 ~65% —— **几乎每条都是这个数，没有双峰** |
| `corr(沿航向占比, MAX)` | **−0.258**（弱、且**符号与假说相反**） |
| `corr(鼓包宽度, MAX)` | **+0.752** ← 唯一稳的关系 |
| `corr(鼓包宽度, 沿航向占比)` | −0.036（两个量基本独立） |
| 峰值处的垂直占比 | 20.5% → 100% 跨 cell 乱跳，**无普适签名** |

⚠ **一个必须说清的口径**：峰值方向的分解**只在峰落在运动中时才有意义**。
本批 16 条里有 4 条峰落在 ≤30mm/s 处（`coll4/g1` 两臂峰速只有 **2–3 mm/s**、
`batch5/g1/tight` 20mm/s、`v11b3/g1/sparse` 30mm/s）——此时航向本身是噪声，
那条 cell 的「峰垂直 100%」**不可解读**。

**判读**：
1. 此前的「A 类=垂直位移型 / B 类=沿航向速度型」**不成立**。RMS 口径下
   沿航向成分处处 ~2/3 ⇒ 那是**全语料共有的残余速度/尺度偏置**，
   而它在过的 cell 和不过的 cell 上**差不多**，所以**分不开这两类**。
2. 真正与 `MAX` 相关的只有**鼓包宽度**（+0.752）：坏 cell 的 `>10mm` 连续帧数是
   **13–47 帧**，好 cell 是 0–5 帧。⇒ 卡门的**不是尖峰，是「宽出界」**。
3. 结论对药方的指向：想提通过率，要压的是**误差停留在 10mm 以上的时长**，
   不是峰值高度。这与 §5（鼓包来自 `[6/8]`、`[7/8]` 放大峰）和 §9（平滑/时移都无效）互印。

---

## 9. ★ 两个负结果（各扫了参数，都没救）

### 9.1 `--relative-motion-sigma-m` **惰性**

`g7_knob_sweep.py`（`rel` 臂，sigma 从 8mm 一路放到 200mm = 25×）：

| cell | .008（现役） | .02 | .06 | .2 |
|---|---|---|---|---|
| v10_batch/g1 tight | **2.62 / 13.5** | 2.65 / 13.5 | 2.66 / 13.5 | 2.66 / 13.5 |
| v10_batch/g2 tight | **7.75 / 16.8** | 7.81 / 16.9 | 7.83 / 16.9 | 7.83 / 16.9 |

（每格 `RMSE mm / MAX mm`；两列都动不了 0.1mm）
⇒ **[7/8] 里给 VINS 相对运动的 sigma 不是那个旋钮**，
`bump_trace.py` 说的「[7/8] 对鼓包过度修正」**不是由它造成的**。

⚠ 附带的**代码耦合**（踩过两次，两条都记下来）：

1. `--auto-visual-position-sigma` **依赖** `--relative-motion-trajectory`：
   无轨迹时 `fuse_mast3r_stereo_imu.py:609` 直接
   `raise ValueError: automatic visual sigma requires an aligned relative-motion trajectory`。
   所以「不用 VINS 相对运动」的 `[7/8]` 臂**必须同时**去掉 `--auto-visual-position-sigma`
   并显式给一个 `--visual-position-sigma-m`（已改，`G7_FIXED` / `AUTO_SIGMA` 分开）。
2. ★★ **`[8/9]` 结构性地无法脱离 Docker2 链** —— `fuse_docker2_mast3r_complementary.py`
   的 `--docker2` 是 **required** 参数：
   ```
   fuse_docker2_mast3r_complementary.py: error: the following arguments are required: --docker2
   ```
   实测 `[7/8]` 在**没有 VINS 相对运动**时能跑通（产出了 `g.csv`/`g.json`），
   但 `[8/9]` 一定需要 `--docker2` ⇒ **「完全不用 VINS」这条臂在架构上不成立**，已放弃。

   ⇒ 想量「VINS 对最终 fused 的净贡献」，正确口径不是拔掉它，而是
   **保留 `--docker2` 但把 `--docker2-local-weight 0 --docker2-scale-weight 0`**
   （`scale_ratio = x^0 = 1` ⇒ `fused = graph`，即纯图优化轨迹）。
   这与 §5 量到的 `lw=0` 时 `fused ≡ graph` **逐位相同**是同一条机理。
   叠加 `g7_ablation.py` 的 `no_g7` 臂（跳过整个 `[7/8]`）即可把
   「VINS 相对运动在 `[7/8]` 里的贡献」和「它在 `[8/9]` 里的贡献」分开看：
   **`[8/9]` 侧净贡献 ≈ 0**（`lw=0` 已是直通，`sw=0.25` 只是一个标量），
   **`[7/8]` 侧净贡献为正且显著**（跳过它一律更差，见 §6）。

### 9.2 ★ 加重平滑**更糟**（这是 09-14 自己扫出来的，不是我们重跑）

`20260914_validation_v10_batch/group1/diagnostic_smoothing_*` 四点
（**本目录重算过**，`trajectory.csv` vs `lighthouse_body_ground_truth.csv`，n=1743）：

| σ (s) | 0.025 | 0.075 | 0.100 | 0.150 |
|---|---|---|---|---|
| **MAX** (mm) | **13.84** | 13.92 | 14.05 | **14.99** |
| RMSE (mm) | **3.17** | 3.24 | 3.36 | **3.92** |
| rot (°) | 1.882 | 1.882 | 1.881 | 1.880 |

⇒ **单调变差**，而且**最坏点被平滑抬高**。这条本身就说明鼓包
**不是高频噪声**（否则平滑必然改善），与 §8 的「宽出界」结论一致。
顺带：**平滑对旋转毫无影响**（1.880–1.882 全平）⇒ 旋转与位置是两条独立的通道。

### 9.3 时移也压不掉

`bump_shift_analysis.py`（v10b/g1/tight，峰 idx 469）：峰处误差
**13.46mm = 沿航向 2.38 + 垂直航向 13.25（98%）**；在窗口内做 ±0.5s 最优整体时移，
**压掉 0%** ⇒ **不是时间对齐问题**。

---

## 10. ★★ `--metric-scale-mode` 的消融：**它把 RMSE 与 MAX 往相反方向拉**

`g7_ablation.py`，21 组 × {`joint` 现役, `stereo`, `imu`, `no_g7`（整个跳过 `[7/8]`）}，
`[8/9]` 固定现役参数，口径 = 官方评测器同一条门限。

⚠ **本节是 09-20 重跑的**：第一版只记 RMSE/rot（留档 `g7_ablation_rmse_only_v1.log`），
而**当下唯一卡门的是 `ate_translation_max`** ⇒ 补上 MAX 一列才看得出方向。**两版结论相反。**

| cell | 臂 | `joint`（现役） | `stereo` | `imu` | `no_g7` |
|---|---|---|---|---|---|
| v10b/g1 | tight | **2.62** / 1.76 / 13.5 | 2.67 / 1.77 / 13.9 | 2.81 / 1.76 / **13.1** | 3.58 / 1.74 / **10.6** |
| v10b/g2 | tight | 7.75 / 3.06 / **16.8** | **6.01** / 3.07 / **23.0** | 11.44 / 3.06 / 23.8 | 14.78 / 3.25 / 45.6 |
| v11b3/g1 | tight | — | — | — | 15.24 / 2.18 / 30.0 |
| v11b3/g2 | tight | 4.67 / 1.88 / — | **4.16** / 1.89 / — | 5.47 / 1.84 / — | 7.92 / 2.09 / — |

（每格 `RMSE mm / rot ° / MAX mm`；`—` = 该臂在此 cell 上失败，见 §3.1 / §3.4）

### ★★ 结论：**按 RMSE 挑尺度模式会正好挑错**

看 v10b/g2/tight 那一行：`stereo` 把 **RMSE 7.75 → 6.01（−22%）**，
**同时把 MAX 16.8 → 23.0（+37%）**。而门是查 MAX 的。
⇒ **RMSE 的收益是用最坏点买来的**。第一版消融只看 RMSE，因此指向 `stereo`；
补上 MAX 之后方向翻转。

另两点：
* **`joint`（现役）从不在 MAX 上最差**。
* `no_g7` 在 v10b/g1 上 MAX 最好（**10.6**，几乎过门）却在 RMSE 上最差
  ⇒ 「跳过 `[7/8]`」**有机会救 max**，但代价是 RMSE 全面变差（与 §5 的
  「`[7/8]` 整体有益、局部放大峰」一致）。**这是一条值得继续查的线索，不是本轮结论。**

**⇒ 判决：不据此改生产参数。** 与用户明令一致（「不要为单一轨迹改
`--metric-scale-mode`；任何更改必须是在**所有数据组**上验证过的通用策略，且由用户决定」）。
注意 `stereo` 还会**跳过 `imu_stereo_metric_scale_disagreement` 硬门**
（`fuse_mast3r_stereo_imu.py:2770-2775`）—— 那是 Codex 2026-09-11 定过、
只接进 `compare)` 而从没接进产品路径的策略。**所以若哪天要动它，
不能只看精度，得同时决定那道尺度硬门要不要一起放开。**

⚠ **数据卫生**：本节的 `[7/8]` 产物**留盘**在 `g7_cache/<cell>__<臂>__<模式>/`，
所以改评估口径**不需要重跑 `[7/8]`**（第一版把产物写在 `tempfile` 里随删，
是这次多花一轮的原因）。`g7_cache/` **不入库**（可再生的派生数据）。

⚠ **已知的 nan 不是消融结果**：`no_g7` 之外的臂在 `holdout_b2/g1` 与 `v11b3/g1/tight`
上是 `nan`，原因分别是 §3.1（台架写死 `docker2_slam/`，`g7_ablation.py` **尚未**接入
`vins_dir.py`）与 §3.4（`merge_stereo_reports`）。**别把 nan 读成「该模式更差」。**