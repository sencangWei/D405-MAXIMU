---
name: orb-rgbd-inertial-status
description: ORB RGB-D-Inertial 在 8/4 组合已彻底跑通:世界帧重锚定后 z_mean 跨次极差 0.0152m;Allan 实测噪声/BA 迭代加倍两个精度杠杆均负结果(已 revert,基线即最优)
metadata:
  node_type: memory
  type: project
  originSessionId: bd123ebc-72bc-46c2-a61d-5d9f3d3f6ff9
  modified: 2026-08-09T16:37:14.993Z
---

ORB-SLAM3 RGB-D-Inertial(定制 fork,vanilla 无此模式)在 8/4 组合(d405_720p_all_20260804_215229:RGB+左IR+depth+IMU)上的状态,2026-08-09 **已彻底跑通**。

**已提交为 tag(回滚点):** `orb-rgbd-inertial-stable-20260809`(commit d431b3a,ORB fork `/home/robot/ego_pipeline/work/toolchains/ORB_SLAM3`,本地身份 robot,分离头指针但 tag 有效)。`git checkout orb-rgbd-inertial-stable-20260809` 恢复。含 9 文件修复集 + 世界帧重锚定。

**修复集:**
1. `Tracking.cc` PredictStateIMU/TrackLocalMap 空 mpImuPreintegrated 守卫。
2. `LocalMapping.cc` BA2 阈值 mTinit 15→5(近静态 mTinit 累积慢)。
3. 节点 `rgbd_inertial_node.cpp`(ros2_ws/src/ego_orbslam3_ros2,非 git,备份 /tmp/orb_node_backup/rgbd_inertial_node.cpp.20260809_gated):IMU 未跟上(≥1s)丢弃帧,绝不喂空 IMU。
4. **世界帧重锚定(2026-08-09 新增,本轮验证)**:LocalMapping.cc::InitializeIMU 最后一次初始化(VIBA2, priorG=priorA=0)后,首关键帧 body 平移到世界原点(仅平移、保重力对齐、s=1)。根因:近静态 ApplyScaledRotation 绕原点重力对齐,地图离原点数米 → 整图被推任意 z(修复前 0/+3.2/-17.8/+22m 每次随机)。

**验证(3 次复跑 215229):** 无崩溃,单段无跳变;z均值 0.13/0.14/0.12(极差 0.02m);|t|max 1.7-1.9m(接近 VINS mono-RGB 参考 ≤1.6m);路径 10.4-11.7m @ 0.17-0.21 m/s。

**重要负结果(别重蹈):** 把 T_b_c1 换成 Kalibr 2026-08-04 物理外参(0.74° 近恒等)→ ORB 完全崩(VIBA1 后 Fail to track local map → 地图重置循环,0 点)。**根因:回放脚本 replay_db3_to_ros2.py:241-253 硬编码把 IMU 旋转 R_rep≈91.42°(重力 y→z),配置的 T_bc bake 了等量旋转(~90°,双IR 88.0°/mono 88.5°,注意不是"57°"——57° 是我早前轴角小角近似算错,见 [[vins-230503-rootcause]] 子代理调查)。bake 与回放旋转自洽(残差 0.267°),必须成对保留,缺一即崩/发散。**

**测试命令:** `cd /home/robot/ego_vio_humble && bash -c 'source /opt/ros/humble/setup.bash && source /home/robot/ros2_ws/install/setup.bash && python3 scripts/_test_orb_dynamic.py recordings/d405_720p_all_20260804_215229 rgbd'`(session 必须带 `recordings/`)。改 ORB 源后 `cd build && make -j$(nproc)`(节点动态链 libORB_SLAM3.so,只重建 .so 即可)。

**2026-08-09 精度压测(两个杠杆都是负结果,均已 revert,基线即最优):**
1. **IMU 噪声换 Allan 实测值(失败)**: 配置 `IMU.NoiseGyro 1.03e-3→5.472e-5`、`NoiseAcc 8.28e-3→4.240e-4`(60.6s 静止段 Allan 实测密度)。语义确认无误(ORB 按密度处理: Tracking.cc:624 `Ng*sqrt(freq)`, ImuTypes.cc `Cov=diag(ng²)`, 每步 `B·Nga·Bᵀ`≈density²·dt,累积正确)。但更小噪声 → IMU 约束强 361 倍 → 覆盖 RGB-D 深度约束 → 世界帧被 IMU 传播误差拉离地板(z[end] 垂直漂到 3.48m)。revert 制造商值后 3 次复跑 z_mean 0.1335/0.1247/0.1183 → **极差 0.0152m(优于原 0.02m)**。**教训: RGB-D 场景深度是尺度真值,视觉主导正确,不要用实测更小噪声。**
2. **BA 迭代数加倍(失败)**: LocalInertialBA `opt_it 10→20`(bLarge 4→8, Optimizer.cc:2388)+ 初始化 FullInertialBA `100→200`(LocalMapping.cc:1309)。z_mean 跨次极差 0.0152→**0.1216m(8x 恶化)**,run1/3 路径爆炸 24.4m/19.0m。更多 LM 迭代非更好——fork 作者从 2→10 是调好稳定点,20 次让优化器漂出好局部解。revert(git checkout src/Optimizer.cc src/LocalMapping.cc + make)后确认复跑 path 11.0m、|t|max 1.713、z_mean 0.1228,基线恢复。
**结论: tag orb-rgbd-inertial-stable-20260809 + 制造商噪声已是本管线实际最优。重锚定精度 z_mean 极差 0.0152m。** 别再试图压噪声或迭代数。

**2026-08-10 53×43cm 方形闭环测试(新 session d405_720p_all_20260810_001100,四路全录):**
- **时间偏移根因修好**: `_test_orb_dynamic.py` 默认 shift 7.36→11.7(见 [[orb-replay-time-offset]]),不再爆炸。
- 但小回路仍是极限: 5 次复跑每次 14–15 次 `Fail to track local map`(短暂跟踪失败),闭环(对重锚定原点)最佳 3.5cm/中位 ~18cm,形状失真(PCA 63×34 vs 物理 53×43),z span 8.6cm。VINS 双IR 同段: 闭环 1.7–3.2cm、PCA 49×43/52×45 贴合物理、z span 1.4–2.8cm、稳定可复现。
- **根因**: RGB-D 模式 scale 顶点固定(Optimizer.cc:3123 `VS->setFixed(!bMono)`),尺度不漂;变异性来自小回路上偏置/重力方向可观测性弱 + 角点处理积压(节点队列>3丢帧,`dropped_pairs_` 涨到 74)。与 VINS 的 calm-start 限制同源,是动态场景根本性限制。
- **建议**: 公平对比两管线最佳精度需更大方形(边长 1.5–2m)。ORB 大运动下(215229,11m)z 极差 0.0152m。
- 注意: 旧 session 215229 无右IR 是旧版采集脚本(其 frames.csv 无 infrared_right 列),非硬件限制;当前 all_streams 脚本四路全录,一次录制可同时喂 VINS stereo 和 ORB rgbd。

**结论:** ORB RGB-D-Inertial 现在与 VINS 双IR 都是可用方案。小回路(<1m)精度 VINS 双IR 明显占优;大运动 ORB RGB-D 提供点云+深度。时间偏移务必用 11.7ms。

相关: [[vins-fork-state]] [[d405-hardware-facts]] [[orb-replay-time-offset]] [[dual-ir-divergence-rootcause]]
