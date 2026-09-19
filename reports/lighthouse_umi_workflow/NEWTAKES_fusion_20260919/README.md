# 四条 720p take 的融合重跑 (2026-09-19)

## 为什么要重跑

20260918 那批前端是用带 **`--calib` 循环门控 bug** 的启动器跑的: 启动器拿
`config["use_calib"]` 当传 `--calib` 的前提, 而该值正由 `--calib` 反向赋值 ⇒ 门控恒假
⇒ 前端静默丢掉 D405 内参。症状是 frame1 位移 6.9–14.4mm(正常 0.06mm),
`[2/8]` 双目尺度死在 dispersion 0.797/0.873(阈值 0.5)。

bug 已修, 所以四条都得**整条重跑**; 20260918 的前端产物一律不可复用。

驱动: `run_all.sh`(顺序执行, 一次只跑一个 GPU 任务; 单条失败不中断后面的)。
必须显式 `export MAST3R_SLAM_CONFIG=.../mast3r_slam_d405_offline_motion_kf_tight.yaml` ——
脚本默认的 `config/mast3r_slam_d405_offline.yaml` 是带 22 个 `stereo_*` 键的"第三链"。

## 状态

| take | 结果 | 说明 |
|---|---|---|
| 20260917_233028 | **rc=3 FAIL** | 卡在 `[7/8]`, **无任何融合产物**。详见下节 |
| 20260917_235329 | 运行中 | |
| 20260918_002714 | 未开始 | |
| 20260918_003403 | 未开始 | |

## take 20260917_233028 的失败: 不是误报, 门拦对了

`[7/8]` (`fuse_mast3r_stereo_imu.py`) 报:

```
"result": "FAIL",  "failures": ["imu_stereo_metric_scale_disagreement"]
```

`scale_consistency()` 的门是**相对差 ≤ 15%**:

| 量 | 值 |
|---|---|
| 双目尺度 | 0.4884 m / MASt3R 单位 |
| IMU 尺度 | 0.6236 |
| 相对差 | **24.3%** |

**因为 `fuse_mast3r_stereo_imu.py` 在 FAIL 时 `return 3`, 而工作流是 `set -euo pipefail`,
整条在第 7 步中止** ⇒ 后面 `[8/9]`/`[9/9]` 没跑 ⇒ 没有 `fusion/trajectory_fused.csv`。
(注意: `mast3r/trajectory_graph.csv` 也没写出来, 只有 `graph_fusion_report.json`。)

### 独立判定: 哪个尺度是对的

两条候选轨迹是**同一条 MASt3R 轨迹**分别乘两个候选尺度, 所以各自做 Umeyama 相似变换
对齐到 Lighthouse 真值, **恢复出的尺度哪个 ≈1.0, 哪个就是对的**:

| 轨迹 | Umeyama 尺度 | 结论 |
|---|---|---|
| `trajectory_stereo_bidirectional.csv`(×0.4884) | **1.0122** | 只差 1.2% ⇒ **双目尺度对** |
| `trajectory_imu_metric.csv`(×0.6236) | **0.7928** | 差 20.7% ⇒ **IMU 尺度错** |

反推真值尺度 = 0.4884 × 1.0122 = **0.4944**, IMU 报的 0.6236 **偏大 26%**。

真值有 v11 代跳变污染(约 50 次 × ~7mm ≈ 0.35m), 但那只影响路程长度, 对相似变换的
**尺度**几乎无影响 —— 顺带印证: 真值路程 4.154m, 扣掉跳变虚增的 ~0.35m 后 ≈3.80m,
与双目轨迹的 3.673m 只差 3.3%。

> ⚠ 两条轨迹对齐后的 ATE **完全相同**(均值 6.94mm / 中位 4.32mm)—— 这是**必然的**,
> 相似变换会把全局尺度吸收掉, 所以 ATE 对全局尺度不敏感。**别拿 ATE 来判尺度。**

### 它是常态还是异常

扫全部 686 份 `schema = umi_mast3r_stereo_imu_fusion_v2` 报告:
PASS 663 / FAIL 23, 其中失败原因分布:

| 次数 | 原因 |
|---|---|
| 13 | `visual_gyro_rotation_inconsistent` |
| **4** | **`imu_stereo_metric_scale_disagreement`** |
| 3 | `trajectory_frame_orientation_correction_too_large` |
| 2 | `orientation_correction_too_large` |
| 2 | `imu_position_refinement_did_not_improve` |
| 1 | `stereo_translation_refinement_did_not_improve` |

尺度不一致只出现过 4 次, 本条的 24.3% 是**其中最差的** ⇒ 是这条 take 的真异常。

### 值得记的一条

`[6/8]` (`align_mast3r_scale_with_imu.py`)**自己报 PASS**: `residual_rmse_mps = 0.0119`、
`rank == unknowns == 550`、`condition_number = 24.09`、`gravity_norm = 9.792`。
**数值条件很好, 尺度却错 26%。** 又是"**自洽性门对尺度失明**"这个模式 ——
与 VINS 验收门那条是同一个坑(见 `mast3r_g2_validation_20260919/README.md` §17)。

## 待办 / 待用户定夺

1. 等其余三条跑完, 看是否同样撞 `imu_stereo_metric_scale_disagreement`。
   若四条都撞, 说明这是这批 take 的系统性问题, 而不是单条偶发。
2. **是否改用 `--metric-scale-mode stereo` 重跑本条** —— 传这个参数时该门**不会触发**
   (见 `fuse_mast3r_stereo_imu.py:2771-2774`), 即尺度一律采信双目。
   - 支持的理由: 已有独立证据(上表)双目尺度只差 1.2%, 而双目尺度物理上锚在
     D405 已标定的基线 + `fx*baseline/disparity` 上, 比 IMU 尺度稳。
   - 反对的理由: 这会**让一条未通过门控的轨迹变成通过**, 而且它是**针对单条轨迹**的处置,
     触犯"绝不能针对单条轨迹调参"。若要改, 必须作为**通用策略变更**
     (尺度不一致时一律信双目, 而非中止), 并在**所有数据组**上重新验证。
   - **不擅自改。等用户定。**

## 数据卫生

- 一次只跑一个 SLAM/GPU 任务。
- 所有报告保持 `slam_supervision: false` / `external_ground_truth_used: false`。
- Lighthouse 仅用于评估。
