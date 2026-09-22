---
name: lighthouse-gt-timing-uncertainty
description: "Lighthouse tracker–相机时间偏移在标定数据里几乎不可观测(0–20ms 残差仅变 0.6mm vs 6mm 底), 三份 PASS_CANDIDATE 标定相差 7.4ms ⇒ 真值精度与 10mm 门同量级, A/B 分辨率受限"
metadata: 
  node_type: memory
  type: project
  originSessionId: 616ba18d-61b7-4aba-830c-5738635103f2
  modified: 2026-09-21T14:45:24.836Z
---

真值链路里有一个**被低估的误差源**：`tracker_query_offset_ms`（用它在相机时间戳上
插值 tracker 位姿的那个偏移）。

`lighthouse_extrinsic_time_recheck_20260911_round4/independent_calibration` 的
`offset_search.coarse_best_candidates` 显示残差对偏移**极不敏感**：

| offset_ms | 残差 p95 (mm) |
|---|---|
| 0 | 6.773 |
| 5 | 6.398 |
| 10 | 6.283 |
| 15 | 6.165 |
| 20 | 6.606 |

**0–20 ms 全程只变 0.6 mm，而残差底噪本身约 6 mm** ⇒ 该偏移在这份标定数据里
基本**不可观测**。后果：三份都 `result=PASS_CANDIDATE` 的标定给出
**13.371 / 5.998 / 9.0 ms**（散布 7.4 ms）。所以那不是"跨会话漂移"，是同一个
平坦谷里的任意点。

**Why**：手持速度下 10 ms ≈ 数毫米平移 + 约 1° 旋转，与"全帧 ≤10 mm"的门**同量级**。
故 848-vs-720p 这类想分辨 ≤10 mm 差异的 A/B，其分辨率被真值自身的定时不确定度限制。
`estimate_robot_umi_time_offset.py` **不解决**这个问题 —— 它估的是 UMI 轨迹 vs
`robot_joints.jsonl`，不是 tracker-vs-camera。

**How to apply**：
- 不要从三份标定里"挑一个更可信的" —— 这个参数在它们之间没有区分度，
  挑就是任意选择。要用更强的方法先钉住 tracker–相机时延。
- **判一条 take 的真值能不能用**：`tracker_integrity.json` 的 `status` 要 PASS，
  且要看 `motion_continuity.angular_pose_jump_count` / `max_angular_step_deg`。
  历史分布：多数会话 jumps=0（maxstep ≤4.8°），偶发灾难（20260915_103540 jumps=41,
  175°）。**只有姿态跳、平移不跳**（`translation_pose_jump_count=0`）更像 tracker
  姿态重解的假阳性，不是真的碰动。
- **跳变必须映射到相机正式窗口才有结论**：tracker.csv 的 `host_monotonic_ns`
  与 d405_frames.csv 的 `infrared_left_mono` 同在 host monotonic 域，直接比。
  `min-timestamp-overlap-ratio 0.98` 那道门**只查覆盖率、不查位姿有效性**，
  所以真值坏了这道门**不报** —— 必须自己查。
  实例：20260917_224105_848x480_loop1 在窗口内 1.683 s 有 10 次 >10° 跳变
  （最大 29.76°/7.7ms），受影响 152/5392 帧 = 2.82% ⇒ 该 take 不可用于 A/B。

相关：[[lighthouse-bump-detection-method]]、[[capture-stop-hazard]]

## ★★ 第三种坏真值：tracker 会话窗**短于**相机窗 ⇒ GT 铺不满 ⇒ 与精度无关地 FAIL（2026-09-21）

上面第 45 行那条要**补一句限定**：overlap 门查的是**覆盖率**，所以
①「窗内位姿跳变」它**不报**（要自己查）；② 但「**覆盖本身不足**」它**会报** ——
而覆盖不足的来源就在 GT 的生成方式里。

**机理（代码级）**：GT 不是独立生成的。`lighthouse_umi_precision_workflow.sh:111-127`
的 `apply_lighthouse_ground_truth()` 把**估计轨迹**作为 `--query-times` 传给
`apply_lighthouse_aprilgrid_calibration.py` ⇒ **GT 按估计的时间戳采样**。
tracker 会话的单调窗若**比相机窗早结束**，末端那段 query 时刻取不到 tracker 位姿，
脚本**只写取到的部分、不报错** ⇒ `samples_written < query_samples` ⇒
overlap 掉到 0.98 以下 ⇒ 官方评测器报 `timestamp_overlap_ratio_below_limit`。

**独立判据（不必跑评测器，provenance 里就有）**：
`lighthouse_ground_truth_provenance.json` 的
`timestamp_overlap_ratio` / `query_samples` / `samples_written`。
根因验算：`inputs.tracker` 的 `host_monotonic_ns` 末端 − `inputs.d405_frames` 的
`sensor_event_mono` 末端。**符号 100% 预测 overlap 门**（21 组普查零例外）：
正 ⇒ 1.0000；负 ⇒ 0.9592/0.8711。

| 组 | 末端差 | 晚于 tracker 末端的相机帧数 | 缺的 GT 样本数 | overlap |
|---|---:|---:|---:|---:|
| `v10_holdout_batch2/group2` | −2.417 s | 72 | 71 | 0.9592 |
| `collective_batch4/group2` | −7.448 s | 224 | 223 | 0.8711 |

逐帧吻合、tracker 流**内部零空洞**（中位 7.6/7.8ms）⇒ 缺的 GT **100% 由末端截断解释**。

**Why**：这两格**无论精度多好都过不了门** —— 覆盖完整相机窗的轨迹，overlap 上限就是
`tracker窗∩相机窗 / 相机窗` = 0.959 / 0.871。把 ATE 归因给算法会得到假结论。

**How to apply**：
- **验收集第一道筛 = `timestamp_overlap_ratio ≥ 0.98`**（读 provenance，秒级）。
  放在任何族间/代际 ATE 比较**之前**。
- 工具：`reports/mast3r_g2_validation_20260919/rerun_tail_v2_20260920/gt_window_census.py`
  （独立从两个 csv 算时窗，不读现成值），产物 `gt_window_census.json`。
- 坏真值现在是**两个物种、共 4 组**（12 个门禁组里）：
  **分支跳变 REJECT** = `holdout_b2/g1`(63.6%)、`batch5/g1`(65.6%)（见
  [[lighthouse-tracker-branch-switch]]）；**时窗截断** = `holdout_b2/g2`、`collective_b4/g2`。
  ⇒ **`holdout_batch2` 两条 take 真值都有缺陷，这批拿不出可用 ATE。**
- 修法：剔除，或用**覆盖完整相机窗**的 tracker 会话重建真值。

相关：[[mast3r-0914-no-good-result-20260921]]（§34 复算后其判决不变）、
[[mast3r-g1-vs-g2-sweep-20260919]]、[[report-fusion-not-vins-accuracy]]
