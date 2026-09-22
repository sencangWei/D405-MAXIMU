---
name: mast3r-g1-vs-g2-sweep-20260919
description: "G1(09-14产物) vs G2(现役融合尾段)全组对照: 现役≈旧代打平(RMSE 8改善/10变差, max 9/9, rot 7/11), 达标 0→1(仅 v11b3/g2/tight max 9.79); ★四处更正: REJECT 是 9条/5组 不是6项; ★★[9/9]质量门【真阻断】——不读JSON但看exit code, rc=3+set -e 中止整条零产物(我先前错了两次); ★★holdout_b2两条不是跑不了=台架写死 docker2_slam/ 而09-14用的是 docker2_slam_rate0p5/(PASS); ★★该门09-15 15:12被Codex有意加严(已当面告知用户,非静默回归); ★09-20 独立重跑逐位复现全部18条"
metadata:
  node_type: memory
  type: project
  originSessionId: 616ba18d-61b7-4aba-830c-5738635103f2
  modified: 2026-09-21T15:36:15.944Z
---

## 这条记忆回答什么

"**现役融合尾段参数** 是否把 09-14 那条好结果找回来" —— 答案是 **没有，打平**。
`g2_sweep.py`（2026-09-19）就是干这件事的，23 项条目、18 条可对照。
**2026-09-20 独立重跑（`rerun_tail_current_20260920/`）逐位复现了全部 18 条**
⇒ 尾段重跑是确定性的，两套独立实现同一答案（见下"复现校验"）。

## 唯一达标的轨迹

`20260914_validation_v11_holdout_batch3/group2` **tight**，现役参数：

```
rmse 4.1087  p95 6.5232  max 9.7936  w10 100.0%  rot 1.8865   => PASS, failures []
```

同候选 G1 是 max **10.32** ⇒ FAIL。**参数换代是这一条 PASS 与 FAIL 的分界**，但只有这一条。

## 全组结果：现役 ≈ 旧代，整体略偏差

18 条可对照 cell，逐项比大小：

| 指标 | 改善 | 变差 | 中位变化 |
|---|---|---|---|
| max（卡验收的那项） | 9 | 9 | **+0.23mm** |
| RMSE | 8 | 10 | +0.28 |
| P95 | 8 | 10 | +0.21 |
| rot | 7 | 11 | +0.04 |

**达标 旧 0/18 → 现役 1/18。** 明显收益：`batch5/g2/tight` max 16.57→**11.31**、
`collective_batch4/g1/tight` 30.39→**24.16**；明显变差：`batch5/g3` 22.07→22.30、
`v10_batch/g2/sparse` 17.50→18.61。**换代的真正效果是"更依赖 VINS 分支"，不是精度。**

## ★ 两处更正（都是我先说错、后查实的）

**① REJECT 数量**：是 **9 条 cell / 5 组**，不是先前说的"6 项"。
命中组 = `batch5/g2,g3,g4`（各2条）+ `collective_batch4/g1`（2条）+ `collective_batch4/g2`（1条）。

**② [9/9] 质量门【不阻断】—— ★★ 2026-09-20 推翻，这条我错了两次**

先前的说法：`mast3r_slam_precision_workflow.sh:301-311` 跑完
`assess_mast3r_fusion_input_quality.py` 后**直接进入平滑，没有任何分支读它的结果**
⇒ REJECT 只是 `input_quality_report.json` 里的字段，**产物照样出**。

**错在哪**：工作流确实不*读*那个 JSON，但**它读退出码**。现行
`mastr_slam_precision_workflow.sh:335-338` 调用时**没有 `|| true`**，而脚本顶部是
`set -euo pipefail` ⇒ `assess_…` 以 **rc=3 (REJECT)** 退出 **立刻中止整条 `fusion)`**，
**连 `trajectory_fused.csv` 都不会产生**。实测：`batch5/group3` 的 sparse 与 tight 两臂。

⇒ **这道门是真阻断的**，与 VINS 验收门并列。区分三件事：
`不读 JSON`（对） ≠ `不阻断`（错） —— **阻断机制是 exit code，不是读文件**。

**★★ 而且它在 09-15 15:12 被加严过**（详见 [[mast3r-rerun-tail-v2-20260920]]）：
同一 cell 同一输入，09-15 14:29 的报告是 `PASS / reason: null`，今天 `REJECT /
primary_shape_not_independently_supported`；`position_branch_weight` 与
`input_disagreement_p95_m` 只差 7e-5。新判据要求
`disagreement ≥ 0.05 ∧ branch_weight == 0 ∧ stereo_rmse ≥ 0.875×0.004`。
**是 Codex 有意加的、当天当面告知过用户、并引用了用户「数据本身有问题就舍弃」的指示**
（用 `~/.codex/sessions/**/rollout-*.jsonl` 定位到秒：本地 09-15 15:12:32 首个 patch，
且测试里写死了该 cell 的实测四数 0.00393/80.5/0.043/0.0174）。
⇒ **不要当成"没人认领的静默回归"删掉**；有后果的只是"中止整条"这个实现方式。

**真正阻断的只有一道：VINS 验收门**。`[7/8]` 内 `validate_relative_motion_report`
对 `run_acceptance.json` 报 `ValueError: input report did not pass` → `set -e` 整条中止。
本轮命中 4 条（都是 `v10_holdout_batch2`）：

- `group1/sparse`、`group1/tight`：验收 FAIL `corrected_trajectory_jump` + `pose coverage 0.9590 < 0.9800`
- `group2/sparse`、`group2/tight`：**`docker2_slam/vio_corrected_stream.csv` 根本不存在**

**★★ 但"这 4 条跑不了"也是台架选错目录**（2026-09-20 更正）：这两条 cell 盘上有**两份** VINS 产物
—— `docker2_slam/`（g1 FAIL `corrected_trajectory_jump`；**g2 是空目录**）与
`docker2_slam_rate0p5/`（**两条都 PASS**）。09-14 的 `graph_fusion_report.json` 里
`inputs.relative_motion_trajectory` 记的正是 **rate0p5**，且 `docker2_slam/` 的验收写于
09-14 18:01 而 `trajectory_graph.csv` 产出于 18:37 ⇒ 36 分钟后仍跑通。
两份 `vins_auto_loop_config.yaml` 除 `output_path` 外**逐字节相同** ⇒ 差别只在回放速率。
⇒ 台架把路径写死成 `docker2_slam/` 才报错，**不是产品阻断**。
选目录规则见 `rerun_tail_v2_20260920/vins_dir.py`（优先验收 PASS 的，否则取 `docker2_slam_*/` 里第一份 PASS）。

## 机制：为什么现役会触发质量门

`--docker2-local-weight` 0→0.25 让融合**真的开始用 VINS 分支**：

```
position_branch_used:       旧 False → 现役 True
position_branch_weight_max: 旧 0.0   → 现役 0.2505
reason:                     旧 None  → 现役 independent_onboard_trajectory_branch_unobservable
input_disagreement_p95_m:   旧 0.05310 → 现役 0.05304   (底层量几乎逐位相同)
```

**旧代 weight=0 ⇒ "没用到该分支" ⇒ 该检查形同虚设**。与 [[slam-tail-error-elimination-20260919]]
的根因是同一件事的两面：现役更依赖 VINS，而 VINS 的度量尺度可差 37%。

## 复现校验（本轮抽样，全部逐位一致）

| cell | 本轮 | `g2_sweep.json` 的 g2 |
|---|---|---|
| v10_batch/g1/tight | 2.759/4.855/13.737/99.885/1.874 | 2.7586/4.8550/13.7371/99.8853/1.8741 |
| v11b3/g2/tight | 4.1087/6.5232/9.7936/100.0/1.8865 | 同 |
| batch5/g2/tight | 3.94/8.23/11.31/99.20/2.35 | 同 |
| collective_batch4/g1/tight | 8.287/13.016/24.163/80.321/3.090 | 同 |

## 数据卫生

09-19 那次产物在 `/tmp/claude-1000/stereoab/g2final`（scratch）。
**2026-09-20 已重跑并落盘**到 `<group>/fusion_current/<subset>/`（真实报告树，不覆盖旧产物），
脚本 `reports/mast3r_g2_validation_20260919/rerun_tail_current_20260920/`（`run_all.sh` /
`compare_g1_current.py` / `gates.py` / `gen_readme.py`，README 由脚本生成不手抄）。

⚠ **口径坑**：比较器里 `E.interpolate_ground_truth()` 返回的**第 4 个量（GT 四元数）已经是
有效子集**，不能再 `[inside][valid]` 索引一次（会 IndexError）。

## ★★★ §33（2026-09-21）「跑不通」的三道闸 + 门的精确量化

`rerun_tail_v2_20260920/README.md` §33；已推 sencang。

**先更正上面「机制」那节的适用范围**：那节说的是 **09-20 01:34 的 `fusion_current` 族**
（lw=0.25+adaptive+sw=0.475，18 格拒 9）。**现役产线是 lw=0**，两码事，别混。

### 两条臂按 `lw` 互斥，合起来覆盖 `lw` 的全部取值

```python
arm_A = disagreement >= 50mm and effective_local_weight_max > 0
arm_B = disagreement >= 50mm and effective_local_weight_max == 0
        and stereo_edge_rmse >= 0.875 * 4.0mm      # = 3.5mm
```

`effective_local_weight_max` 读的就是 **`--docker2-local-weight`（lw）**，
**不是** `--docker2-scale-weight`（我一度搞错过，`0.2505` 是 lw 不是 sw）。
⇒ **只要分歧 ≥50mm，`lw` 取 0 还是非 0 都会被拒**，只是 `reason` 不同。

| 参数集 | 格数 | 被门拒 | 臂 |
|---|---|---|---|
| 09-14（lw 0/0.35，sw 0/0.85）| 22 | **0** | 旧代码无这两臂（**22/22 PASS**）|
| **现役产线（lw=0，sw=0.25）** | **22** | **4** | 全 arm B |
| 09-20 01:34（lw=0.25+adaptive，sw=0.475）| 18 | 9 | 全 arm A |

**现役被拒 4 格**（真值均干净）：`batch5/g3` sparse+tight、`holdout_batch2/g2` sparse+tight。

### 三条必须记住的反直觉

1. **判 arm B 的是 stereo edge RMSE，不是分歧。** `collective_batch4/g1` 分歧 **164.94mm**
   （= severe 上限 50mm 的 **3.3 倍**，全表最大）却 **PASS** —— 因为 `stereo 3.2094 < 3.5`。
   只盯 `input_disagreement` 会判错。刀刃在 `0.875×max`：`holdout_b2/g2/tight` 的
   `stereo 3.5115` 只超 **0.3%** 就 REJECT。
2. **`lw=0` 是救星不是元凶**：lw=0.25 那 9 格里有 **6 格**（b5/g2 52.4、b5/g4 91.6、
   c4/g1 164.9，各两臂）在 lw=0 下**转 PASS**（stereo 都 <3.5）。
   **同格 A/B**：`collective_batch4/g1` → `fusion_v2`(lw0) **PASS** / `fusion_current`(lw.25) **REJECT**。
   ⇒ [[mast3r-fusion-param-generations]] 的「lw=0 对」**从门通过率角度独立地又对了一次**。
3. **逐位证明是代码翻转、不是数据翻转**：`holdout_b2/g2/sparse` 同一 take，
   09-14 旧门与现役新门的 `stereo_edge_rmse_after_m`（**0.0036623580**）与
   `full_rate_imu_requested_correction_m`（**0.007664695683837181**）**逐位相同**，
   `disagreement` 只差 2.6e-4 —— 判决 PASS→REJECT 完全由代码翻转。

**硬指纹**：旧报告的 `policy` 串没有这两臂的文字，且缺
`maximum_severe_input_disagreement_p95_m` / `position_branch_used` /
`position_branch_weight_max` / `primary_shape_independently_supported` 四键
⇒ **按 `policy` 串即可判定一份报告出自哪代代码**。
⚠ 别用目录位置判代际：`batch5/*/fusion/` 那 12 份是**被后期重跑覆盖过的新代码**报告
（其中 `group3` 顶层是 REJECT），其余 22 份才是旧代码。

### 代价是「零产物」，不是「精度损失」

门在平滑与评测**之前**中止 ⇒ 这 4 格连 `trajectory_fused.csv` 都没有。
同格 `fusion_current`（另一套参数）是 max 22.3/20.4mm ⇒ **反正过不了 10mm 门**。
⇒ 门**没毁掉任何好结果**，毁掉的是**产物与可观测性**。

### 另两道闸（都不是精度）

- `[7/8]` `validate_relative_motion_report` 逃生舱**只认** `["raw_trajectory_jump"]`，
  而实际是 `corrected_trajectory_jump` + coverage 0.959<0.98 ⇒ **不触发**。
- 台架选目录：`vins_dir.py` 已修（优先验收 PASS 的 `docker2_slam_*`），
  但其 mtime **18:36:44** 晚于 `fusion_v2` 运行 **18:32:59** ⇒ **那 4 格从未重跑**。
  用 `OUT_SUBDIR=fusion_v3 rerun_one_v2.sh` 补跑：`holdout_b2/g1` 两臂**跑通**
  （max 28.664/29.802 ⇒ FAIL），`g2` 两臂**仍 [9/9] rc=3**。
  ⚠ **`holdout_b2` 现在没有任何一格同时「过门」且「真值干净」**（g1 真值 REJECT 63.6%、
  g2 门中止）⇒ 这批拿不出可用 ATE。

### ★ 最强旁证：门自带的测试就是行为规格（数字逐格取自真实 cell）

`tests/test_assess_mast3r_fusion_input_quality.py`（**此前未被 git 跟踪**，09-21 一并备份）
把四条臂写死，输入数字**抄自真实格**：

| 测试 | 输入 | 期望 | 真实格（实测）|
|---|---|---|---|
| `..._even_when_stereo_is_consistent` | stereo 0.0028, 分歧 **112mm** | REJECT arm A | `c4/g1`（3.21 一致 / 164.9）|
| `test_keeps_primary_branch_when_disagreeing_position_branch_is_not_used` | stereo 0.0028, 分歧 **52.6mm**, lw=0 | **PASS** | **`b5/g2`（52.44）** |
| `..._all_fallback_checks_are_weak` | stereo **0.00393**, 分歧 **80.5mm**, lw=0 | REJECT arm B | **`b5/g3`（3.9337/80.61）** |

与实测 22 格**逐格吻合**（b5/g2 PASS、b5/g3 REJECT）⇒ **不是 bug、不是静默回归，
是有测试背书的策略选择**；上面那条「stereo 一致也可能被拒」作者的测试名**字面就写着**
`even_when_stereo_is_consistent` —— 特殊性是已知且刻意的。

### ★ §34（2026-09-21）门旁路 A/B：门**没毁掉任何合格结果**（§33.4 那句从旁证升为直接证据）

`rerun_tail_v2_20260920/README.md` §34；已推 sencang `c7438fd4`（tree+blob 双通道逐位验证）。

被门拦下的 4 格，其 `trajectory_fused_unsmoothed.csv` **已在盘上**（门在平滑**之前**中止）
⇒ 直接补 `smooth_pose_trajectory.py` + 官方评测器，落在 `gate_bypass_diagnostic/`：

| cell | rmse | p95 | **max** | w10 | rot | 结果 |
|---|---:|---:|---:|---:|---:|---|
| `holdout_b2/g2/sparse` | 6.326 | 11.769 | **18.440** | 90.168% | 1.611 | FAIL |
| `holdout_b2/g2/tight` | 5.454 | 11.052 | **16.364** | 93.585% | 1.464 | FAIL |
| `b5/g3/sparse` | 5.770 | 11.198 | **20.826** | 92.140% | **2.193** | FAIL |
| `b5/g3/tight` | 7.662 | 14.036 | **19.271** | 77.567% | **2.477** | FAIL |

**4/4 FAIL 且全栽在 `max`**（对照同格 `fusion_current` 22.301/20.370）。
⇒ **判决：门没毁掉任何合格结果** ⇒「保留硬门、只把『`rc=3` 中止整条』改为
『降级为诊断』」是**纯赚**（不换精度，只换回产物与可观测性）。
⚠ `b5/g3` 两臂 **rot 也超 2.0°** ⇒ 该格是平移+旋转双超（与
[[rotation-is-the-binding-gate]] 的旧结论不同，记一笔）。

**★ 同时更正上面 §33「现役被拒 4 格（真值均干净）」**：其中 `holdout_b2/g2` 的
sparse+tight **真值其实也不可用**（tracker 窗短 2.42s ⇒ overlap 0.9592 < 0.98）⇒
那两格是**双重缺陷**。见 [[lighthouse-gt-timing-uncertainty]]。
不影响 §33「门是代码翻转不是数据翻转」的逐位证据（那是同一 take 的两代门报告对比）。

### ★★ §35（2026-09-21）③ 已实施：`[9/9]` 由「`rc=3` 中止整条」降级为诊断

用户批准后落地（`rerun_tail_v2_20260920/README.md` §35；已推 sencang `ad6b63a3`）。

**改动只在一个调用点**（`scripts/mast3r_slam_precision_workflow.sh` `[9/9]` 段，+16 行），
不是改门本身（`assess_mast3r_fusion_input_quality.py` 的 `rc=3` 契约保留、其测试直接调
`assess()` 不断言退出码，已核实）：
- `set +e` → 跑门 → `status=$?` → `set -e`（**沿用脚本既有 rc 感知写法**，非新发明）；
- **只容忍 `rc=3`**（= REJECT）⇒ 打 `⚠ 仅诊断，不阻断` 后继续；
  其他非零码仍 `exit $status` ⇒ **不把崩溃伪装成「门说不合格」**。

**三条验证**：
1. **三用例矩阵**（产线 14 行逐字抽成 harness）：rc=0 → 退出 0 无警告；rc=3 → **退出 0** + ⚠、
   报告仍 REJECT；真故障 rc=1 → 退出 1 + ❌。**用例 A 同时是无回归证据**（rc=0 时只多走两个恒假分支）。
2. **端到端真产线**（`holdout_b2/g2/sparse`，此前零产物）：全阶段完成、`### fusion) rc=0 ###`、
   `trajectory_fused.csv` 产出；耗时 ~22min，抽帧 **1796 帧与 09-14 参考跑逐位相同**；
   `find -newermt` 确认**既有产物零改动**（写在新目录）。
3. **★ 决定性对账**：新产物 sha256 `4de134b2…` **= §34.1 手工旁路产物逐字节相同**；
   门报告逐位相同；评测复现 6.326/11.769/18.440/90.168%/1.611 ⇒ FAIL（含 overlap 项）
   ⇒ **降级只改变「是否中止」，不改变产物一个字节。**

**★ 新查实（未擅动）：`[7/8]` 是同族第二道硬门**
`scripts/fuse_mast3r_stereo_imu.py:2997` `return 0 if report["result"]=="PASS" else 3`；
`result` 由约 10 条 `failures.append(...)`（`:2728…:2775`）任一命中决定，
`write_trajectory` 仅在 `not failures or args.write_failed_output`（默认关）时执行
⇒ **任一失败 = rc=3 + `set -e` + 零产物**，性质与 `[9/9]` 完全相同。
本语料 **65/65 `graph_fusion_report.json` result=PASS、零 failures ⇒ 从未触发**。
其逃生舱 `:112-119` 要求 `watchdog_failures == ["raw_trajectory_jump"]` **精确列表相等**，
而实际发生的是 `corrected_trajectory_jump` ⇒ 不触发 ⇒ `raise ValueError`。

### 待用户拍板（仍未动）

① `[7/8]` 逃生舱是否接纳 `corrected_trajectory_jump`；② 0.5× 回放是否做成全链自动重试；
③（新）`[7/8]` 第二道硬门是否也要同性质的兜底。
（`[9/9]` 那三条臂的**门本身保留**，只降级了中止行为 —— 已落地，不再待拍板。）

相关：[[mast3r-fusion-param-generations]]、[[slam-tail-error-elimination-20260919]]、
[[rotation-is-the-binding-gate]]、[[gate-failure-taxonomy-20260919]]、
[[mast3r-rerun-tail-v2-20260920]]、[[codex-gate-blindspot-history]]
