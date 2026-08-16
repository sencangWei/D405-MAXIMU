# CURRENT_ACTIVE

当前唯一推荐链路：

- 采集：`./capture_d405_720p_rgb_stereo_ir_rsusb.sh --duration 60`
- 实时：`./run_vins_realtime.sh stable`
- 相机：D405 双IR 1280×720@30（彩色只记录，不与IR组成伪双目）
- IMU：KT-EX9-2 400Hz
- VINS：`/home/robot/ros2_ws/src/vins_fusion_ros2/config/d405_stereo_imu/d405_stereo_imu_config.yaml`
- 时间：`estimate_td=0, td=-0.0117`，VINS回放shift=0
- IMU运行时标定：`config/imu_runtime_accel_calibrated_raw_gyro_20260816.yaml`

所有当前/证据/废弃分类见 `JAZZY_HANDOFF_20260816/CURRENT_RUNTIME_PROFILE.md`。旧RGB+IR伪双目、7.36ms、FAIL手工gyro和固定世界Z候选均不得加载。

GitHub 单仓库恢复时，配套标定工具和修改后的 VINS 源码快照位于 `components/`。
