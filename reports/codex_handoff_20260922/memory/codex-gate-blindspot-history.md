---
name: codex-gate-blindspot-history
description: "★ 尺度门真正的历史: codex 09-11 20:23 就明确告诉过用户『尺度由双红外负责, IMU 只修姿态, 尺度冲突只作诊断』, 并把它实现成 --metric-scale-mode stereo (=d405_stereo_direct_metric, 09-08 引入) —— 但它只把 stereo 接进了 compare 子命令, 产品路径 fusion 子命令一直用 joint (门生效, 冲突=硬 FAIL⇒return 3⇒set -e 杀整条⇒零产物); 09-14 codex 又在 adaptive 脚本里加了 set +e 兜底而该脚本 09-15 即弃用; 且 codex 持久记忆对 9 月 SLAM 完全空白"
metadata: 
  node_type: memory
  type: project
  originSessionId: 616ba18d-61b7-4aba-830c-5738635103f2
  modified: 2026-09-21T23:31:22.680Z
---

## ★ 核心: 用户被明确告知过策略, 但产品路径实现的是相反的策略

主线程 `019fbcfe-…`(cwd `/home/robot/桌面/ego_vio_calib_kit`, **08-01 19:03 → 09-16 18:24**,
rollout 1.9 GB / 165717 行)。其中 **09-11 20:23:03** 的 assistant 消息(原文):

> 当前融合输入的独立尺度出现明显冲突: 双红外 0.2240, 纯 IMU 平移尺度 0.2884(相差约 25%)。
> 由于 IMU 加速度双积分对短时手持轨迹和偏置非常敏感, **我已让"尺度"由通过 734 个内点验证的
> 双红外负责, IMU 只修正姿态; 尺度冲突保留在报告里作为诊断, 不会取两者平均把好轨迹拉坏。**

⇒ **这就是 `d405_stereo_direct_metric` 策略**, 和 09-19 那次 24.31% 是同一类事件。
**用户是被告知过的** —— "我说过" 成立。

同一策略的其它明说场合:
- **09-12 09:07:44**: "旧序列尺度不一致达 **23.4%**、密模式也未改善边残差, 因此自动选稀疏模式"
- **09-13 20:49**: 表格列出 `034633` 双目 0.22520 / IMU 0.28536, 相差 **23.57%** 超门限 15%

## ★★ 但 `stereo` 只接在 compare 上, 产品路径是 `joint`

`mast3r_slam_precision_workflow.sh` 两个子命令, 用的模式相反:

| 子命令 | 行 | `--metric-scale-mode` | 尺度门行为 |
|---|---|---|---|
| **`fusion)`** (产品路径) | 178 → **277** | **`joint`** | **生效**: 冲突⇒`failures.append(...)`⇒`return 3`⇒`set -euo pipefail` **杀掉整条, 零产物** |
| **`compare)`** (实验/评分) | 316 → **360** | **`stereo`** | **不生效**: 尺度直接取双目 |

两个模式(`equal_weight_log_mean` / `d405_stereo_direct_metric`) **是 09-08 08:48 同时引入的**,
`d405_stereo_direct_metric` 首次使用 09-09 05:19。

⇒ **Codex 把"尺度冲突只作诊断"这个承诺实现对了, 但只实现进了实验路径;
产品路径从头到尾跑的是会硬 FAIL 的 `joint`。这个落差没有任何人说明过。**

## 09-14 的第二次掩盖

09-14 13:13 Codex 撞门后, 13:14 在 `mast3r_slam_adaptive_precision_workflow.sh` 里改:

    - MAST3R_SLAM_CONFIG=...motion_kf_tight.yaml "$WORKFLOW" fusion ...
    + set +e
    + ... "$WORKFLOW" fusion ...
    + tight_candidate_status=$?
    + set -e

产出 `20260914_validation_v11_holdout_batch3/group1/fusion/selection_report.json`:
`result: PASS` / `selection_policy: use the internally valid sparse candidate when tight fails
quality gates` / `selected_candidate: sparse` / `fallback_reason: tight_candidate_failed_internal_quality`。

**但该脚本 09-15 15:45 后再无人引用(已弃用); 现役 `mast3r_slam_precision_workflow.sh`
从未拿到这个兜底。**

## ★ 09-21: 「冲突只作诊断」这个性质**首次**接进产品路径（但接的是**另一道门**）

用户批准后，`[9/9]` 质量门（`assess_mast3r_fusion_input_quality.py`，09-15 15:12 加严）
由「`rc=3` 中止整条」降级为诊断 —— 只容忍 `rc=3`，其余非零码仍中止，**门本身一字未改**。
已推 sencang `ad6b63a3`；见 [[mast3r-g1-vs-g2-sweep-20260919]] §35。

⚠ **口径必须精确，别混为一谈**：
- 降级的是 `[9/9]`（**双目/视觉惯性一致性**质量门），**不是** 上面那道
  `imu_stereo_metric_scale_disagreement` 尺度门（`[7/8]`，`--metric-scale-mode joint`）。
- 两者**性质相同**（硬 FAIL ⇒ `return 3` ⇒ `set -e` ⇒ 零产物），机制**各自独立**。
- ⇒ 09-11 Codex 承诺的「尺度冲突只作诊断」在产品路径 `fusion)` 里此前**未兑现**；
  「这道门该不该阻断整条」的**先例已经立下**，且 `[7/8]` 是同族第二道硬门
  （`fuse_mast3r_stereo_imu.py:2997`；其约 10 条 `failures` 任一命中即 `rc=3`，
  本语料 **65/65 PASS 从未触发**）⇒ 尺度门照此办理，有现成依据。

## ★★ 09-22: 尺度门**本身**也降级为可选诊断 —— Codex 09-11 的承诺终于兑现在产品路径

用户批准后落地：`fuse_mast3r_stereo_imu.py` 新增 `--scale-disagreement-policy {fail,diagnose}`，
**默认 `fail`（契约逐字节不变）**；`mast3r_slam_precision_workflow.sh` 的 `fusion)` 传 `diagnose`。
`diagnose` **只摘掉 `imu_stereo_metric_scale_disagreement` 这一个名字**，其余失败照旧阻断，
且**不改尺度估计**（仍取 `joint` 对数均值 —— 这是它与 `--metric-scale-mode stereo` 的关键差别：
后者换尺度，不是替代品）。报告里 `failures` 原样保留，新增 `blocking_failures`/`tolerated_failures`
**仅当 policy != fail 才写**。已推 sencang `6ae2c187`；单测 4 条 + 合成触发三用例 + 2 格逐字节对账。

⚠ **排查口径**：看到 `[7/8]` 报告 `result: FAIL` 且 `failures` 里有尺度那条时，先看
`scale_disagreement_policy` 键 —— 若为 `diagnose` 则 `blocking_failures` 为空、**轨迹已写出**，
那是**诊断**不是阻断。`--max-scale-disagreement-ratio` 只喂 `consistent`、**不影响选中的尺度**，
所以能合成触发翻门而轨迹一字节不变（验证全靠这个性质）。

**② 回放速率的负面结论**：`--rate` 现在可用 `VINS_RATE` 覆盖（默认仍 0.5，行为不变），
rc=3 时打印失败物种判别（回放压力型 / 发散型）。
★ **不要再说「0.25× 升级阶梯被实验否决」** —— 那是**单跑**对照，而回放本身不确定
（§36.4：同配置两遍 raw_max 10.37 vs 19.35mm）⇒ 「0.25× 更差」**未确立**。
成立的说法只有：**无证据显示 0.25× 更好**。阶梯照样不做，但理由是「缺证据」不是「有反证」。
`1.0×→0.5×` 那段（raw 40.74→14.73mm，同 capture 唯一 A/B）是**大效应**，仍可信。
现已在 `vins_failure_diagnosis` 打印里补了这条证据强度警告（`7a729ab2`）。

## codex 的持久记忆对 9 月 SLAM 完全空白

- `~/.codex/memories/rollout_summaries/` **最新停在 2026-07-18**, 9 月零摘要。
- `~/.codex/memories/MEMORY.md` / `raw_memories.md` 搜尺度门相关词 ⇒ **0 命中**。
- `~/.codex/AGENTS.md`(每次会话加载) **完全不提 SLAM/融合/MASt3R**。
- 9 月只有 14 个线程, 且 **09-16 18:24 之后 codex 再无新线程** ⇒ 09-17 起的 NEWTAKES 不是 codex 跑的。

⇒ 每开新会话 codex 都得从零重新发现这道门; 而主线程 1.9 GB 的上下文靠 `compacted` 摘要续命。

## 排查口径教训

`thread_history_1.sqlite` 是**投影表**, 会漏。权威记录是
`~/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-*.jsonl` —— 必须流式扫原始 rollout。

相关: [[fusion-scale-gate-umeyama-check]]、[[mast3r-frontend-regression-20260918]]
