---
name: vins-fork-state
description: vins_fusion_ros2 稳定基座已提交为 tag d405-mono-rgb-stable-20260809(6 文件改动+8/4 配置);纯 origin 对所有会话全败
metadata:
  node_type: memory
  type: project
  originSessionId: bd123ebc-72bc-46c2-a61d-5d9f3d3f6ff9
  modified: 2026-08-09T10:11:08.126Z
---

vins_fusion_ros2 稳定方案状态,2026-08-09 已提交。

**已提交为 tag(回滚点):** `d405-mono-rgb-stable-20260809`(commit e7824ac,分支 main,本地身份 robot)。`git checkout d405-mono-rgb-stable-20260809` 恢复完整稳定工作状态(6 文件改动 + 7 个 8/4 配置文件)。注意本仓库无 git 身份,提交用的是本地 `robot <robot@localhost>`。

**6 文件基座**(相对 origin/0cdd022,此前记忆说 4 文件,已扩为 6):
1. `src/vins_estimator.cpp`: IMU QoS depth 100→2000, 图像 depth 5→100(防处理延迟丢 IMU/帧)。
2. `vins/include/vins/factor/integration_base.h`: 移除 `data.timestamp > 1.0` 时的跳过,改为继续积分。
3. `vins/src/featureTracker/feature_tracker.cpp`: KLT 窗口 21→31、金字塔 3→5、goodFeaturesToTrack 0.01→0.005。
4. `vins/src/estimator/estimator.cpp`: updateLatestStates 不重置 latestImuData 时间戳;总 bg 初始化守卫(>0.01 rad/s 拒绝+回滚增量);保留 15fps 帧丢弃(移除后 205703 ATE 从 1.79cm 恶化到 1075m)。
5. `vins/include/vins/initial/initial_alignment.h`: solveGyroscopeBias 返回 Vector3d delta_bg。
6. `vins/src/initial/initial_aligment.cpp`: solveGyroscopeBias 改 Huber IRLS(3 迭代,δ≈1.4°)鲁棒,返回 delta_bg 供守卫回滚。

**配置:** 已验证的 mono-RGB 稳定配置 = `config/d405_rgb_ir_imu/d405_rgb_mono_config.yaml`(num_of_cam=1,cam0=RGB)。`d405_rgb_ir_imu_config.yaml`(num_of_cam=2,伪双目)是退化实验配置,`d405_stereo_imu/` 是早期 stereo 配置——均已提交但非推荐。

**注意:** tag 内含 `[DIAG-ROT]`/`[DIAG-BG]`/`pos-ba-bg` 调试日志,不影响行为。要清掉再改。

**历史:** 纯 origin/main 跑 8 会话基线全败(发散或 0 点),纯 fork 在 D405 上不可用。改动用 `git stash` 管理,pop 后要 diff 校验。

相关: [[vins-230503-rootcause]] [[vins-process-hygiene]] [[vins-replay-args]] [[orb-rgbd-inertial-status]]
