# 现役生产尾段参数 vs 09-14 第一代 —— 全 cell 重评 20260920

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
                                                       旧         新                  旧         新                  旧         新                  旧         新                  旧         新         
cell                                                rmse      rmse        Δ       p95       p95        Δ       max       max        Δ      w10%      w10%        Δ      rot°      rot°        Δ
-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
20260914_validation_v10_batch/group1/sparse         3.37      3.23    -0.14      5.34      4.87    -0.48     12.31     12.96    +0.65     99.89     99.89    +0.00      2.03      1.95    -0.08
20260914_validation_v10_batch/group1/tight          2.65      2.76    +0.11      4.47      4.86    +0.38     13.16     13.74    +0.58     99.89     99.89    +0.00      1.89      1.87    -0.01
20260914_validation_v10_batch/group2/sparse         6.00      7.07    +1.07      9.81     11.67    +1.86     17.50     18.61    +1.11     95.41     84.45   -10.96      2.64      2.68    +0.04
20260914_validation_v10_batch/group2/tight          7.72      8.36    +0.64     12.97     13.31    +0.34     17.75     19.08    +1.34     78.89     69.36    -9.52      3.18      3.20    +0.02
20260914_validation_v10_holdout_batch2/group1/sparse      8.45       ---      ---     14.91       ---      ---     30.02       ---      ---     85.54       ---      ---      1.18       ---      ---
20260914_validation_v10_holdout_batch2/group1/tight      7.39       ---      ---     11.91       ---      ---     30.33       ---      ---     91.62       ---      ---      1.13       ---      ---
20260914_validation_v10_holdout_batch2/group2/sparse      6.14       ---      ---     11.50       ---      ---     17.94       ---      ---     91.07       ---      ---      1.55       ---      ---
20260914_validation_v10_holdout_batch2/group2/tight      5.31       ---      ---     10.39       ---      ---     17.31       ---      ---     94.42       ---      ---      1.46       ---      ---
20260914_validation_v11_holdout_batch3/group1/sparse      4.88      4.76    -0.12      7.09      6.77    -0.32     13.60     13.06    -0.54     99.71     99.71    +0.00      1.97      1.98    +0.01
20260914_validation_v11_holdout_batch3/group2/sparse      5.10      4.70    -0.40      8.33      7.25    -1.08      9.86      8.33    -1.54    100.00    100.00    +0.00      2.12      2.08    -0.04
20260914_validation_v11_holdout_batch3/group2/tight      4.57      4.11    -0.46      7.39      6.52    -0.87     10.32      9.79    -0.53     99.94    100.00    +0.06      1.96      1.89    -0.08
20260915_batch5_four_videos/group1/sparse           5.28      4.98    -0.29      9.55      9.47    -0.08     18.35     19.32    +0.97     95.58     95.87    +0.29      1.82      1.81    -0.01
20260915_batch5_four_videos/group1/tight            6.18      4.91    -1.27     12.42     10.24    -2.18     17.61     18.54    +0.93     86.52     94.78    +8.26      1.84      1.76    -0.08
20260915_batch5_four_videos/group2/sparse           4.21      3.66    -0.55      8.54      7.03    -1.50     13.01     10.66    -2.35     98.28     99.66    +1.38      2.04      2.22    +0.18
20260915_batch5_four_videos/group2/tight            5.22      3.94    -1.28     11.69      8.23    -3.46     16.57     11.31    -5.26     90.30     99.20    +8.89      2.14      2.35    +0.21
20260915_batch5_four_videos/group3/sparse           5.93      7.23    +1.30     11.38     14.10    +2.72     22.07     22.30    +0.23     92.48     82.16   -10.33      2.22      2.50    +0.28
20260915_batch5_four_videos/group3/tight            7.35      9.11    +1.76     13.41     18.13    +4.72     20.11     20.37    +0.26     78.31     74.93    -3.38      2.52      2.83    +0.31
20260915_batch5_four_videos/group4/sparse           4.99      6.57    +1.58      8.21      9.79    +1.59     15.33     14.29    -1.04     97.65     95.93    -1.72      1.90      2.58    +0.67
20260915_batch5_four_videos/group4/tight            5.27      6.63    +1.36      8.41      9.63    +1.22     15.10     13.19    -1.92     98.85     96.96    -1.89      1.98      2.41    +0.43
20260915_collective_batch4/group1/sparse            5.76      7.81    +2.05     11.24     11.40    +0.17     18.67     14.85    -3.81     91.16     80.21   -10.96      1.99      2.62    +0.63
20260915_collective_batch4/group1/tight             7.50      8.29    +0.79     12.80     13.02    +0.21     30.39     24.16    -6.23     89.79     80.32    -9.47      2.19      3.09    +0.90
20260915_collective_batch4/group2/sparse            5.80      6.08    +0.28     10.42     11.76    +1.33     15.48     16.08    +0.60     94.56     92.11    -2.45      2.36      2.08    -0.28
```

**汇总（每条 cell 逐项比大小）**

| 指标 | 改善 | 变差 | 持平 | 中位变化 |
|---|---|---|---|---|
| RMSE | 8 | 10 | 0 | +0.277 |
| P95 | 8 | 10 | 0 | +0.212 |
| max | 9 | 9 | 0 | +0.228 |
| w10 | 5 | 9 | 4 | +0.000 |
| rot | 7 | 11 | 0 | +0.042 |

**达标数：旧 0/18 → 现役 1/18**

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
