---
name: mast3r-fusion-param-generations
description: "★★ 09-20 第二次判决(推翻第一次): 「09-14 fused=图优化直通」**只对 sparse 臂成立**; 09-14 的 tight 臂其实是 **lw 0.35+adaptive+smooth15+sw0.475** 在**真融合**(用原输入重跑复现盘上产物 0.236mm/1.171mm, 而 lw=0 差 2.069mm/3.552mm; 报告 inj_max 6.89 ↔ 重跑 6.85 吻合); 第一次的 0.187mm「直通」是**重建误差假象**(且拿09-14 fused 比了 09-20 的 graph)。**lw 是 09-15 15:45 重写 adaptive 启动器 + 单元测试 `assert \"--docker2-local-weight 0.35\" not in script` 钉死的, 不是数据判决**。已复原 [8/9] 为 lw0+sw0+--auto-docker2-scale-weight(实测 5.46 < 09-14 tight 的 5.72, 改动仍站得住但叙事要改)"
metadata:
  node_type: memory
  type: project
  originSessionId: 616ba18d-61b7-4aba-830c-5738635103f2
  modified: 2026-09-20T10:08:00.449Z
---

## 现象

修好 `--calib` 门控后重跑流水线,前端产物**逐字节一致**,但
`trajectory_fused.csv` / `trajectory_fused_unsmoothed.csv` 的 sha **对不上** 09-14 基准。
差值结构:**纯平移、四元数逐位相同(`max|Δq| = 0.0`)**,
偏差均值 `[+0.215, −0.102, +0.153] mm` —— 正好等于 `fusion_report.json` 里
`fusion.alignment_translation_m` 的差。**不是回归,是融合段参数被改过。**

## 两代改动(都在 `mast3r_slam_precision_workflow.sh` 里)

| 版本 | `fuse_mast3r_stereo_imu.py` | `fuse_docker2_mast3r_complementary.py` |
|---|---|---|
| 09-14 基准 | `--joint-correction-cap-mode global` | 无 adaptive 开关 |
| ↓ 09-14 ~ 09-16 18:24 | `per-node` | — |
| ↓ 09-18 14:35 ~ 15:40 | `per-node` | `--docker2-local-weight 0.25 --docker2-scale-weight 0.475 --adaptive-local-weight` |
| 现役(09-19 00:56) | `per-node` | 同上 |

指纹字段:基准 `fusion_report.json` 的 `adaptive_local_weight=False` / `effective_local_weight_max=0.0`;
现役 `True` / `0.25`。基准 `graph_fusion_report.json` 的 `stereo_translation_fusion.correction_cap_mode="global"`;现役 `"per-node"`。

## ★★★ 09-20 第二次判决（推翻 §1）：09-14 的 tight 臂**不是直通**

用户问「[8/9] 以前不是这样的啊？之前没遇到过啊」⇒ 查盘上全部 1016 份
`fusion_report.json` 的历时轨迹，**用户是对的**。

### 1. 09-14 跑的是**两个候选**，参数根本不同

| 臂 | `local_weight` | adaptive | `smoothing_s` | 启动器 |
|---|---|---|---|---|
| `sparse` | 0 | 否 | 8 | `mast3r_slam_precision_workflow.sh` |
| `tight` | **0.35** | **是** | **15** | `mast3r_slam_adaptive_precision_workflow.sh` |

（逐 cell 实测：`20260914_validation_v10_batch` 与 `_holdout_batch2` 的 tight 全是
0.35/ada/smooth15；`v11_holdout_batch3` 与 `20260915_*` 起两条臂才都变 0。）

**每次选谁有记录**：`fusion/selection_report.json`（schema
`umi_mast3r_fusion_candidate_selection_v1`）。v10b/g1 选 tight、g2 选 sparse；
holdout_b2/g2 先选 sparse，`fusion_v11_reselected/` 又把
`minimum_tight_stereo_improvement_ratio` 从 0.1 放宽到 **0.0** 改选 tight。

### 2. 直接实验：tight 臂**真的在注入修正**

用 09-14 的**原始输入**（`fusion/tight/mast3r/trajectory_graph.csv` +
`docker2_slam/vio_corrected_stream.csv`）重跑，与盘上
`fusion/tight/trajectory_fused_unsmoothed.csv` 比：

| cell | `lw.35 ada smooth15 sw.475` | `lw=0` | 报告 inj_max ↔ 重跑 |
|---|---|---|---|
| v10b/g1 | **0.236 mm** ★复现 | 2.069 mm ✗ | 6.89 ↔ 6.85 |
| v10b/g2 | **1.171 mm** ★ | 3.552 mm ✗ | 9.00（被裁） |

⇒ **lw=0 明确不是 09-14 tight 的配置。** 另两个 tight cell
（holdout_b2/g1、g2）重跑失败于 `docker2_position_policy`：
`ValueError: Docker2 run is not safe ... corrected_trajectory_jump`
——是**现役 VINS 验收门**拦的，与融合无关。

### 3. 它是**被启动器重写 + 单元测试钉死**的，不是数据判决

* `mast3r_slam_adaptive_precision_workflow.sh`（**09-15 15:45**）被重写：不再自己
  带融合参数，两条臂都交给 `mast3r_slam_precision_workflow.sh`（内含
  `--docker2-local-weight 0`）⇒ 两臂都变 0。唯一区别只剩 `MAST3R_SLAM_CONFIG`
  （由 `resolve_candidate_config "$output"` 按输出目录名选 `tight`/`offline.yaml`）。
* `tests/test_mast3r_adaptive_workflow.py:20`：
  `assert "--docker2-local-weight 0.35" not in script`
  ⇒ **明令禁止它回来**。

**没有任何一份记录写了「因为 A/B 更差所以关掉」** —— 这是纯粹的工程重写副作用。

### 4. ⚠ 更正 §1 的「直通」结论

§1 说「09-14 的 fused = 图优化轨迹」，**只对 sparse 臂与 09-15 之后的所有运行成立**。
`graph_vs_fusion.py` 的两处缺陷：(a) 只看了 `tight` 一条臂；(b) 拿 09-14 的
`fused` 去比 **09-20** 的 `fusion_current/tight/mast3r/trajectory_graph.csv`
（比错了对象，其中 2 个 cell 的 graph 还因 cap mode 变过）。
那个「0.187mm 直通」是**重建误差假象**（`camera_to_body_with_body_orientation_prior`
的重建与脚本内 `base` 有 ~2mm 偏差）。见 `graph_vs_fusion_v2.py`（改为比
**同一臂自己的** graph）。

### 5. 已应用的复原仍然站得住，但**理由变了**

不是「回到 09-14 的直通」，而是「**lw 从 0.35 降到 0 实测更优**」：
09-14 tight 实测 **5.72mm**，剩 lw=0 配置 **5.46mm** ⇒ 改动有效，但叙事要改。
**lw=0.35 到底好不好的干净 A/B 还没做**（只有 2/4 个 tight cell 能复现）。

### 6. ★★ 多组对照判决（17–19 组，固定同一 graph 只动融合参数）

| 实验 | 脚本 | 结论 |
|---|---|---|
| lw A/B | `lw35_vs_lw0_ab.py` 17组×4配置 | A(`lw.35 s15 sw.475`) vs D(`lw0 s15 sw.475`) = **5.78 vs 4.99**, 门 **2.44 vs 2.03**, 逐cell **3胜14负** ⇒ **lw=0.35 是负贡献**; C vs B(sw=0) ATE 打平(5.25/5.26) 但门 2.45 vs 2.03 ⇒ lw 连门都伤 |
| sw 扫描 | `sw_sweep_lw0.py` 17组×6值 | `lw=0` 下 **0.15–0.475 是平台**, 中位 ATE 5.26→5.03/4.99, **门完全不动(2.03, 8/18)**, 12胜5负 ⇒ **sw>0 是真杠杆**; §1 判它「不足以当结论」是**样本太少下的误判** |
| 姿态杠杆 | `attitude_lever_ab_v2.py` 19组 | 去掉 `--use-docker2-orientation-for-lever-arm`: **门 rot 19/19 改善**, 中位 **−0.085°**, ATE 不动(+0.016mm); 旧配置(`lw.25 sw.475`)下也 18/18 ⇒ **跨配置稳定**; 有 cell 从超标翻达标(batch5/g2 sparse 2.04→1.96) |

**顺带**：09-18 那次改动是**好坏捆绑** —— `sw 0.475` 对、`lw 0.25` 错。
`--auto-docker2-scale-weight` 在本语料**结构性恒选 0**（条件 2 要求两链尺度分歧 ≥2%，
实测只差 0.44%）⇒ **写死 0.25 不是「绕过安全闸」，是恢复设计意图**。

### 7. ★ 已落地（提交 `e0fca6bb`，已推 sencang，远端 blob 逐位校验通过）

`scripts/mast3r_slam_precision_workflow.sh` [8/9]：
```diff
  --docker2-local-weight 0
- --docker2-scale-weight 0
- --auto-docker2-scale-weight
+ --docker2-scale-weight 0.25
  ...
- --use-docker2-orientation-for-lever-arm
```
去掉姿态开关还有个**语义收益**：输出姿态不再恒等于 VINS 姿态，
「用深度学习轨迹形状修轨迹」这才真正生效。

⚠ **仍未解决**：`coll4/group2/sparse` 用任何融合配置都崩
（`matmul` 维度不匹配 1508 vs 1732），是独立 bug，没查。

相关: [[mast3r-chain-topology]]、[[mast3r-frontend-config-silent-disable-20260920]]

---

## ★★ 09-20 判决：两代的**实际**影响（这次是量的，不是读脚本推的）

证据 `reports/mast3r_g2_validation_20260919/fusion_param_ab_20260920/`（README + 10 脚本）。

### 1. 09-14 的 `trajectory_fused.csv` = **图优化轨迹本身**（融合是直通）

代码级推导：`scale_ratio = docker2_scale_ratio ** docker2_scale_weight`，
09-14 的 report 记着 `local_weight=0.0`、`docker2_scale_weight=0.0`
⇒ `scale_ratio = x⁰ = 1.0` ⇒ `scaled_base = base` ⇒ `fused = base`（`high_frequency` 被乘 0）。
而 `base = camera_to_body_with_body_orientation_prior(mast3r_positions, …)`。

实测（8 cell）：位置差 **RMS 中位 0.187mm**（残差来自重采样到 `common_times`），
**门在 8/8 个 cell 上吻合到 0.02°**（1.89/1.89、3.16/3.18、1.96/1.96…）⇒ 成立。

**推论**：`local_weight=0` 时 `smoothing_s` 与 `scale_horizon_s` **双双失效**，
管线退化成「**MASt3R 图 × 标量尺度 → 输出**」。

### 2. `--docker2-local-weight` 0→0.25 是**净负贡献**，且单调有害

逐 cell 调真实脚本（subprocess），只改这一个参数：

| lw | 0.00 | 0.05 | 0.10 | 0.15 | **0.25** | 0.40 |
|---|---|---|---|---|---|---|
| 中位 ATE (mm) | **4.98** | 5.04 | 5.16 | 5.32 | 5.79 | 6.75 |
| 中位 门 (°) | **2.06** | 2.06 | 2.07 | 2.13 | 2.39 | 2.78 |
| 门过 | **4/8** | 4/8 | 4/8 | 4/8 | 3/8 | 3/8 |

机理：**VINS 单链中位 24.98mm vs MASt3R 图 5.46mm** ⇒ 掺 25% 只会拖坏。
`--adaptive-local-weight` 实测**惰性**（有效权重只在 0.2327–0.2505 间动，开关两轮扫描逐格差<0.15mm）。
`--docker2-scale-weight` 效果弱得多且**门完全不敏感**（5.46/4.90/4.98/5.85/6.76，逐 cell 胜负 4:4 混合）
⇒ **不足以当结论**，别据此改生产参数。

### 3. `--auto-docker2-scale-weight` 是一道被写死绕过的安全闸

`select_docker2_scale_weight` 四条件全过才给候选权重，否则**返回 0.0**；
其中一条是 `cross_slam_disagreement ≥ 0.02`（第二个尺度投票必须**有信息量**）。
本语料 docker2 与 MASt3R 只差 **0.44%** ⇒ 判「无信息量」⇒ 设计性地选 0。09-14 正是如此。
**现役写死 0.475 = 绕过该闸。**

### 4. ★ `correction_cap_mode` global→per-node：**只在裁切时生效**

两份 `graph_fusion_report.json` **逐字段完全相同**（连
`position_correction_requested_max_m = 0.017721039795965936` 都逐位一致），**只有 cap_mode 不同**。
逐 cell 比 `trajectory_graph.csv` 的 sha256：**产物不同 ⟺ `clipped > 0` ⟺ `req_max > 25mm`**

| cell | 产物 | clipped (global→per-node) | req_max |
|---|---|---|---|
| v10_batch/g1 | 逐字节相同 | 0/0 | 9.80mm |
| **v10_batch/g2** | **不同** | **1799/53** | **37.36mm** |
| v11b3/g2 | 逐字节相同 | 0/0 | 17.72mm |
| **batch5/g1** | **不同** | **1799/26** | **31.73mm** |
| batch5/g2,g3,g4、coll4/g1 | 逐字节相同 | 0/0 | 14–22mm |

`global` = 把**整个修正场等比缩小**直到最大值等于上限（1799 帧全中）；
`per-node` = 只裁越界节点（53/26 帧）。**6/8 个 cell 是空转。**

**代际判决**：09-14 图（global）+ lw0 = **5.72mm** vs 09-20 图（per-node）+ lw0 = **5.46mm**
⇒ **per-node 那代是改进，不是回归。** 最优 = **新图 + 旧融合**的混合体。

### 5. 已应用的复原（提交 `e0e2bd8c`，已推 `sencang`）

`scripts/mast3r_slam_precision_workflow.sh` 第 [8/9] 步改回
`--docker2-local-weight 0 --docker2-scale-weight 0 --auto-docker2-scale-weight`（去掉 adaptive），
与 `...bak_20260918_1435_pre_l025` 逐字相同。效果：中位 ATE **5.78→5.46**、
门 **2.38°→2.06°**、门过 **3/8→4/8**。

**⇒ 融合侧已无杠杆**：`lw=0` 后残余 4.9–5.5mm **全部来自 [7/8] 图优化 + 前端**。
下一步只能攻 `fuse_mast3r_stereo_imu.py` 或前端。目标仍是 fused ATE ≲4mm
（见 [[rotation-is-the-binding-gate]]）。

### 6. 回归排查清单

- **只有 `trajectory_fused*.csv` / `fusion_report.json` / `input_quality_report.json` /
  `smoothing_report.json` 对不上时，先查这几个开关，别当回归查。**
- 前端产物（`trajectory_imu_metric.csv`、`trajectory_stereo_*.csv`、`mast3r_logs/dataset*.txt`）
  **与融合尾段改动无关**，可以放心当回归基准；但 **`trajectory_graph.csv` 会因
  `correction_cap_mode` 变**（只在 `req_max > 25mm` 的 2/8 个 cell）——比 sha 前先看
  `stereo_translation_fusion.correction_clipped_frames`。
- 版本落地在 `scripts/` 的两份备份：`...bak_20260918_1435_pre_l025`（09-16 18:24 内容）与
  `backups/rollback_20260919_calib_fix/mast3r_slam_precision_workflow.sh.bak`（09-18 15:40 内容）。
- `run_manifest.json` / `dataset_manifest.json` 里成片的 DIFF 多为 **schema 新增字段**
  （`config_sha256`、`kernel_release`、`source_camera_info.fx`、`crop_bottom_px` 等），
  判断方法是看基准侧是否为 `None`；新增字段不是数据变化。

相关: [[mast3r-frontend-regression-20260918]]、[[mast3r-frontend-0915-edit-window]]、
[[mast3r-chain-topology]]、[[rotation-is-the-binding-gate]]
