---
name: vins-config-optimal
description: "VINS 双IR 配置已证为经验最优点 (26+ 变体全中性/更差): td=-0.0117/iter8/parallax10/max_cnt400/min_dist20, 可达闭环~1.4cm"
metadata: 
  node_type: memory
  type: project
  modified: 2026-08-10T03:01:22.702Z
  originSessionId: bd123ebc-72bc-46c2-a61d-5d9f3d3f6ff9
---

**2026-08-10 精度最大化实验结论**: VINS 双IR 在 000943 (53×43cm 慢速回路) 上, committed 配置 `e239352`/tag `vins-dual-ir-stable-20260810` **已是经验最优点, 不要再调参**。全部 26+ 次变体实验无一改进:

- **td**: 0 必炸 (122.8cm), -0.0100~-0.0125 全在噪声带, Kalibr -0.0117 最优; estimate_td:1 **不生效** (此端口 DIAG-TD 恒停初始值, ≡固定)。
- **parallax 3/5、iter 12/16、solver_time 0.08、F_threshold 0.5、g_norm 9.80665、IMU 随机游走松/紧**: 全中性。
- **max_cnt 250 爆炸 / 600 更差; min_dist 15 不稳** (3 跑中 1 跑 51.5cm 坏跑); WINDOW_SIZE 10→14 (代码, 重编译验证) 中性, 已回滚。
- **可达精度**: 闭环 0.7–2.2cm (均值~1.4cm), zspan<2.6cm, 重力面倾斜<1°, z 平面 RMS<3mm, 矩形尺度 100–105%。
- 残留误差 = 小回路视差/IMU 激励弱导致的运行间噪声, 配置不可调。与 ORB RGB-D 教训完全一致: 偏离调好的稳定参数不带来精度。

**Why:** 调参已收敛, 继续试是浪费时间。**How to apply:** 需要更高精度请换更大回路 (边长 1.5–2m 重录) 而非改配置。完整数据见 slam_trajectories/vins_precision_sweep_20260810.md。相关: [[dual-ir-divergence-rootcause]] [[vins-replay-args]] [[orb-rgbd-inertial-status]]。
