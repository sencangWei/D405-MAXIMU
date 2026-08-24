# CURRENT_ACTIVE

当前唯一推荐链路：

- 采集：`./capture_d405_720p_rgb_stereo_ir_rsusb.sh --duration 60`
- 实时：`./run_vins_realtime.sh stable`
- 新STM32产品实时候选（正在HIL验收）：`./run_vins_realtime.sh product-live`
- 历史 `<1 cm` 冻结链复现：`./run_vins_realtime.sh frozen`
- 同采集实时准确＋可复放冻结后端：`./run_vins_realtime.sh frozen-record --duration 60`
- 相机：D405 双IR 1280×720@30（彩色只记录，不与IR组成伪双目）
- IMU：KT-EX9-2 400Hz
- VINS：`/home/robot/ros2_ws/src/vins_fusion_ros2/config/d405_stereo_imu/d405_stereo_imu_config.yaml`
- 时间：`estimate_td=0, td=-0.0117`，VINS回放shift=0
- IMU运行时标定：默认关闭，发布未修改 raw IMU；旧
  `config/imu_runtime_accel_calibrated_raw_gyro_20260816.yaml` 已标为 `FAIL` 且禁止默认加载。
  2026-08-23 的 30 姿态候选也已被同回放盲 A/B 否决：Z 改善不能跨数据稳定，且水平
  短边缩小约 13.2%。该工具只作研发诊断，不进入产品运行时；原始 DB3/imu.bin 始终不改写。

所有当前/证据/废弃分类见 `JAZZY_HANDOFF_20260816/CURRENT_RUNTIME_PROFILE.md`。旧RGB+IR伪双目、7.36ms、FAIL手工gyro和固定世界Z候选均不得加载。

`stable` 是当前工作区实时链；它不等于历史 `a3a38b8` direct-BRIEF 冻结回环链。
需要复现历史四组 `<1 cm` 三维闭环报告时，必须显式使用 `frozen`，并检查启动日志中
冻结回环 SHA256。

`product-live` 不复用旧 `stable` 的 CH340 串口和 `td=-0.0117`。它固定使用当前
CP2102N、63字节 `stm32_combined_v1`、新装配外参与 `td=-0.009312`，并加载已通过
水平 `<1 cm`/真实升降安全 A/B 的自适应回环。Rerun 显示 `/odometry_rect`，同时
保存 `/odometry`、`/odometry_rect` CSV 并发布 `/slam/health`。在本轮真实手持验收
完成前它仍是候选，不替换 `stable` 或 `frozen`。

`product-live` 还启用了产品失效保护：正常轨迹单步不超过 `0.05 m` 时原样发布；
一旦原始 VINS 出现非有限位姿、时间戳倒退或单步超过 `0.05 m`，在坏位姿发布前
锁存 `SLAM_FAILED`，冻结 Rerun 最后可信轨迹并停止发送回环关键帧。该状态不能在
同一进程内自动解除；产品包装器检测到该锁存后会打印证据、关闭旧 Rerun 和传感器
主链并以非零状态退出，避免界面静默“卡住”。必须先把相机重新对准有静态纹理的
环境，再重新启动入口；重新初始化后的数据属于新轨迹段，禁止静默拼接坐标。
这项保护用于避免人体/近景动态物体占据主要视野时输出转圈假轨迹，不代表经典 VINS
已经能在静态背景完全不可见时继续精确定位。

产品 VINS 日志同时记录四级跟踪证据：`left/temporal/new/stereo/mature30hz`
以及后端 `tracked_from_previous/new_features/long_tracks`。出现
`[TRACKING-DEGRADED]` 时可区分当前角点不足、时序光流断裂、双目匹配不足和成熟轨迹
断层，禁止再只凭一个 `tracks` 数字盲目放宽门槛。关闭 Rerun 的自动验收仍固定使用
同一份产品 VINS 二进制及 SHA-256，不允许回落到 ROS 环境中的其他节点。

世界 Z 当前保持“已知限制”，不启用固定压平或 Depth 平面候选。开源路线和本机 Depth
水平/升降盲 A/B 的拒绝证据见 `reports/WORLD_Z_OPEN_SOURCE_REVIEW_20260823_ZH.md`；
若继续投入，优先试严格静止门控的 ZUPT，运动中的绝对高度则需要持续可见的同一平面
或专用测距观测。

GitHub 单仓库恢复时，配套标定工具和修改后的 VINS 源码快照位于 `components/`。

训练采集已把 STM32 联合包内的磁编码器作为独立 400 Hz 状态流接入，但不送入
VINS／SLAM。正式会话新增 `external_imu/gripper_encoder.csv` 和逐相机帧的
`gripper_camera_alignment.csv`；编码器与 IMU 共用 MCU→主机单调时钟映射，相机
关联复用 `td=-0.009312 s`。字段、加载物体时的距离语义和验收门见
`docs/TRAINING_GRIPPER_SYNC_ZH.md`。
