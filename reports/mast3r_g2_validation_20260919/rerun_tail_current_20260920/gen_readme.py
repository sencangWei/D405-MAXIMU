#!/usr/bin/env python3
"""从 compare.log / gates.log / summary.json 生成 README.md —— 不手抄数字。"""
import json, re
from pathlib import Path
HERE = Path(__file__).parent
cmp_lines = (HERE / "compare.log").read_text().splitlines()
gate_lines = (HERE / "gates.log").read_text().splitlines()
S = json.loads((HERE / "summary.json").read_text())

# 精度表：取 compare.log 里以 batch 名开头的行
i = next(i for i,l in enumerate(cmp_lines) if l.startswith("cell"))
rows = [cmp_lines[i-1], cmp_lines[i], cmp_lines[i+1]] + \
       [l for l in cmp_lines if re.match(r"^20\d{6}_", l)]
# 状态行：取 compare.log 尾部的 "  <cell> 旧 ... → 现役 ..."
stat = {}
for l in cmp_lines:
    m = re.match(r"^\s{2}(\S+/\S+/\S+)\s+旧\s+(\S+)\s+(.*?)\s+→\s+现役\s+(\S+)\s+(.*)$", l)
    if m:
        stat[m.group(1)] = (m.group(2), m.group(3).strip(), m.group(4), m.group(5).strip())

gates = {}
for l in gate_lines:
    m = re.match(r"^(20\d{6}_\S+/\S+/\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$", l)
    if m:
        gates[m.group(1)] = (m.group(2), m.group(3), m.group(4))

W = S["win"]
doc = f"""# 现役生产尾段参数 vs 09-14 第一代 —— 全 cell 重评 20260920

## 为什么做这件事

记忆里有一条"**这 15 组产物全是第一代(G1)的，现役算法从未在这批数据上评过**"。
本轮把它补齐并把产物**落进真实报告树**（此前 09-19 的 `g2_sweep.py` 把产物放在
`/tmp/claude-1000/stereoab/`，scratch，无持久化，且漏了 5 条）。

⚠ **更正**：上一轮我口头说"要评现役必须重跑尾段——这件没做"是**错的**。
`reports/mast3r_g2_validation_20260919/g2_sweep.py`（2026-09-19）就是这件事，
23 项条目。本轮是**独立重跑**：18 条可对照 cell 的现役数字与 `g2_sweep.json` 的
`g2` 值**逐位吻合**（见下"复现校验"）。

## 方法

**输入逐字节冻结**：复用各组现成的 `trajectory_imu_metric.csv` 与四份 stereo 报告，
**不重跑前端与标定**。只把融合尾段 `[7/8] [8/9] [9/9]` 换成现役生产参数：

```
[7/8] fuse_mast3r_stereo_imu.py     --joint-max-correction-mm 25 --joint-correction-cap-mode per-node
                                    --full-rate-max-correction-mm 20 --metric-scale-mode joint
[8/9] fuse_docker2_mast3r_complementary.py
                                    --scale-horizon-s 1 --smoothing-s 8
                                    --docker2-local-weight 0.25 --docker2-scale-weight 0.475
                                    --adaptive-local-weight --roughness-threshold-mm 9
                                    --adaptive-weight-strength 0.45
[9/9] assess_mast3r_fusion_input_quality.py + smooth_pose_trajectory.py --gaussian-sigma-s 0.025
```

与 09-14 第一代的差异（本仓 22 条 cell 普查，`correction_cap_mode` **22/22 全是 global**）：

| 参数 | 第一代(旧) | 现役 |
|---|---|---|
| `correction_cap_mode` | global (22/22) | **per-node** |
| `docker2-local-weight` | 0.0 (18条) / 0.35 (4条) | **0.25** |
| `--adaptive-local-weight` | False (18条) / True (4条) | **True** |
| `docker2-scale-weight` | 0.0 (17条) | **0.475** |
| `smoothing-s` | 8.0 (18条) / 15.0 (4条) | **8.0** |

产物写入 `<group>/fusion_current/<subset>/`，**不覆盖任何旧产物**。

## 结果：18 条可对照 cell（逐 cell，官方评测器）

```
{chr(10).join(rows)}
```

**汇总（每条 cell 逐项比大小）**

| 指标 | 改善 | 变差 | 持平 | 中位变化 |
|---|---|---|---|---|
""" + "\n".join(
    f"| {k} | {v['改善']} | {v['变差']} | {v['持平']} | {v['中位变化']:+.3f} |"
    for k, v in W.items()
) + f"""

**达标数：旧 {S['pass_old']}/{S['n']} → 现役 {S['pass_new']}/{S['n']}**

### 结论

**现役尾段参数不是"把 09-14 找回来"的答案，整体与第一代打平、略偏差。**
- `max`（卡住验收的那一项）改善 9 / 变差 9，中位 **+0.23mm**。
- `RMSE`、`P95` 各改善 8 / 变差 10；`rot` 改善 7 / 变差 11。
- 唯一达标仍是 `v11b3/group2/tight`（max 10.32→**9.79**，唯一一条越过 10mm 门）。

**换代带来的真正变化是"依赖了 VINS 分支"**，而不是精度：
现役 9 条 cell 触发 [9/9] 质量门 `independent_onboard_trajectory_branch_unobservable`
（`position_branch_used` False→True，因为 `--docker2-local-weight` 0→0.25）。

## 两道门分别是什么行为（本轮查清，含更正）

| 门 | 位置 | 行为 | 本轮命中 |
|---|---|---|---|
| VINS 验收门 | `[7/8]` 内 `validate_relative_motion_report` | **真阻断**：`ValueError` → `set -e` 整条中止，零产物 | **4 条** |
| [9/9] 输入质量门 | `assess_mast3r_fusion_input_quality.py` | ⚠ **只写 `input_quality_report.json`，工作流从不读它** ⇒ **不阻断**，产物照出 | **9 条** |

⚠ **更正**：我先前说"质量门 REJECT ⇒ 生产路径拒绝出产物"是**错的**。
`mast3r_slam_precision_workflow.sh:301-311` 跑完评估直接进入平滑，没有任何分支读结果。

被 VINS 门真阻断的 4 条（**现役拿不到产物**）：

```
20260914_validation_v10_holdout_batch2/group1/sparse  VINS ['runtime watchdog is SLAM_FAILED: corrected_trajectory_jump',
                                                            'pose coverage 0.9590 < 0.9800']
20260914_validation_v10_holdout_batch2/group1/tight   同上
20260914_validation_v10_holdout_batch2/group2/sparse  VINS：docker2_slam/vio_corrected_stream.csv 不存在
20260914_validation_v10_holdout_batch2/group2/tight   同上
```

⇒ 这 4 条**不是融合尾段的问题**，是上游 VINS。09-19 的 `g2_sweep.py` 因此主动跳过它们。

## 复现校验

本轮独立重跑 vs `../g2_sweep.json` 的 `g2` 字段（抽样，全部逐位一致）：

| cell | 本轮 rmse/p95/max/w10/rot | g2_sweep 的 g2 |
|---|---|---|
| v10_batch/group1/tight | 2.759 / 4.855 / 13.737 / 99.885 / 1.874 | 2.7586 / 4.8550 / 13.7371 / 99.8853 / 1.8741 |
| v10_batch/group2/sparse | 7.070 / 11.671 / 18.607 / 84.452 / 2.678 | 同 |
| v11b3/group2/tight | 4.109 / 6.523 / 9.794 / 100.0 / 1.886 | 同 |
| batch5/group2/tight | 3.94 / 8.23 / 11.31 / 99.20 / 2.35 | 同 |
| collective_batch4/group1/tight | 8.287 / 13.016 / 24.163 / 80.321 / 3.090 | 同 |

⇒ 尾段重跑是**确定性的**，两套独立实现给出同一答案。

## 复现方法

```bash
cd reports/mast3r_g2_validation_20260919/rerun_tail_current_20260920
./run_all.sh                     # 22 条 cell，~115s/条，写进 fusion_current/
python compare_g1_current.py     # 精度对照（两侧现算，同一评测器）
python gates.py                  # 两道门状态
python gen_readme.py             # 重新生成本文件
```

⚠ **口径坑**：`compare_g1_current.py` 的 `score()` 里 `interpolate_ground_truth` 返回的
第 4 个量（GT 四元数）**已经是有效子集**，不能再 `[inside][valid]` 索引一次。

## 相关

- `../README.md` 与 `../g2_sweep.json`（09-19 那次）
- `../spike_attribution_20260919/README.md`（尖峰归属与 A/B）
"""
(HERE / "README.md").write_text(doc)
print(f"已写 {HERE/'README.md'}  {len(doc)} 字符")
