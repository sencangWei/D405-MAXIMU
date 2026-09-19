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

### 根因: IMU 尺度估计器本身有 ±10% 散布, 这条撞上了极值

先排除时间偏置: 工作流把 `--td-s -0.009109323` **硬编码**给所有 take, 于是扫了一遍 td:

| td (s) | -0.020 | -0.012 | -0.00911 | -0.006 | 0.000 | +0.006 |
|---|---|---|---|---|---|---|
| scale | 0.6194 | 0.6226 | 0.6236 | 0.6246 | 0.6264 | 0.6278 |

26ms 范围内尺度只动 **1.4%** ⇒ **时间偏置不是原因, 排除**。

再看历史上两个尺度的一致程度(按 (IMU尺度, 双目尺度) 去重后 131 个组合):

| 分位 | P5 | P25 | **P50** | P75 | P90 | P95 | P99 |
|---|---|---|---|---|---|---|---|
| 相对差 | 1.4% | 4.0% | **6.9%** | 12.2% | 14.6% | 17.4% | 24.1% |

**本条的 24.3% 在 P99 之上, 是历史最差之一。**

比值 `IMU / 双目`: **69% 的情况 >1**, 中位 **1.061**, 范围 **[0.857, 1.288]**。
本条的 1.277 顶在最大值附近。

⇒ **IMU 尺度估计器是一个有约 ±10% 散布、外加 ~6% 正偏的噪声量**, 不是坏了。
本条只是抽到了极端值。而双目尺度有四份独立配置互相印证 + 真值验证(差 1.2%),
**所以当两者打架时, 该信的是双目。** 门在这一点上是对的。

> 附带一条代价观察: 门限 15% 恰好压在分布 P90–P95 之间, 意味着**约 9% 的运行会被这个门
> 拦下**; 而因为 `return 3` + `set -e`, 被拦的运行是**零产物**。这个门的杀伤力不小。

### 值得记的一条

`[6/8]` (`align_mast3r_scale_with_imu.py`)**自己报 PASS**: `residual_rmse_mps = 0.0119`、
`rank == unknowns == 550`、`condition_number = 24.09`、`gravity_norm = 9.792`。
**数值条件很好, 尺度却错 26%。** 又是"**自洽性门对尺度失明**"这个模式 ——
与 VINS 验收门那条是同一个坑(见 `mast3r_g2_validation_20260919/README.md` §17)。

## 评测已备好(跑出产物即可直接评)

四条 take 的 Lighthouse 真值(`lighthouse_body_ground_truth.csv`)现在**全部就位**:

| take | GT 位置 | 状态 |
|---|---|---|
| 20260917_233028 | `20260917_233028_720p_loop1_720p_arm/` | 20260918 那批已生成, 复用 |
| 20260917_235329 | `20260917_235329_720p_loop1_720p_arm_defaultcfg/` | 同上, 复用 |
| 20260918_002714 | `THIRDCHAIN_newtakes_20260918/20260918_002714/` | **本次新生成** |
| 20260918_003403 | `THIRDCHAIN_newtakes_20260918/20260918_003403/` | **本次新生成** |

两条新生成的都是 `result=PASS`、`timestamp_overlap_ratio=1.0`、
`tracker_query_offset_ms=13.371`,与 take1 那条**完全一致**。

生成命令(标定沿用 take1 provenance 记录的那一份, sha256 `382517ec…`):

```
python3 scripts/apply_lighthouse_aprilgrid_calibration.py \
  --query-times <该 take 的 vio_corrected_stream.csv> \
  --tracker reports/lighthouse_umi_sessions/<会话>/tracker.csv \
  --d405-frames <录音目录>/d405_frames.csv \
  --calibration reports/lighthouse_extrinsic_time_recheck_20260911_round4/independent_calibration/lighthouse_d405_aprilgrid_calibration.json \
  --body-camera-config .../formal_runtime_calibration_installed/vins_config.yaml \
  --target body --output <...>/lighthouse_body_ground_truth.csv --report <...>/lighthouse_ground_truth_provenance.json
```

> 核对过: 融合工作流用的 `formal_runtime_calibration/vins_config.yaml`(`87338c34…`)与 GT 用的
> `formal_runtime_calibration_installed/vins_config.yaml`(`3f47e90f…`)**sha256 不同, 但差异只有
> 20 行 `loop_*` 门控键**(前者是后者的超集), **外参部分逐字节相同** ⇒ 估计与真值没有坐标系不一致。

评测(`scripts/evaluate_slam_ground_truth.py`)直接:

```
--estimate <take>/trajectory_fused.csv --ground-truth <上面的 GT>
--output <...>/precision.json --report-md <...>/precision.md
```

### ⚠ 四条 take 的真值全部被分支门 REJECT

已实测(`lighthouse_tracker_branch_gate.py`,阈值 2%):

| take | 受影响相机帧 |
|---|---|
| 20260917_233028 | **65.4%** |
| 20260917_235329 | **84.5%** |
| 20260918_002714 | **82.8%** |
| 20260918_003403 | **80.6%** |

四条**全部 REJECT**。根因是这四条都用 v11 代标定(跳变率 1.092 次/米,
见 `mast3r_g2_validation_20260919/README.md` §17.8):tracker 中途跳到另一条位姿分支、
不再回位,真值被切成互不相接的多段,**单一 SE(3) 对齐被迫在段与段之间折中**,
ATE 被抬到 ~10mm 量级 —— 与融合算法本身的精度无关。

**所以这四条出来的 ATE 数字只能这样用**:

- ✅ **相对比较**:融合 vs VINS vs MASt3R 单链,同一条被污染的真值下横向比 —— 有效。
- ❌ **绝对精度**:不能上报。真值伪影量级(10mm)与待测精度同量级,绝对数没有意义。
- 想要绝对精度, 得先重标基站(这正是用户预告的 v12), 重录。

**在重标定之前, 这批 take 的价值就是"相对排序", 不是"达标与否"。**

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
