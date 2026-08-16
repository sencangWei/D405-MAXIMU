# 配置状态

## CURRENT_ACTIVE

- 实时设备：`devices_vins_fusion_live.yaml`
- IMU运行时内参：`imu_runtime_accel_calibrated_raw_gyro_20260816.yaml`
- VINS双IR：`/home/robot/ros2_ws/src/vins_fusion_ros2/config/d405_stereo_imu/d405_stereo_imu_config.yaml`
- 时间合同：VINS `estimate_td=0`、`td=-0.0117`、回放shift=0。

## REGRESSION_EVIDENCE

- `slam_declared_loop_regression.json`及其它产品数据清单。
- `imu_level_20260816.yaml`是失败/待验的世界Z候选状态记录，不是生产运行标定。
- ORB配置只用于历史副链复验；当前主链是VINS双IR。

## LEGACY_DO_NOT_RUN

- `camimu_720p_leftir_kalibr.yaml`：08-04旧标定，含废弃`-7.36ms`，只作历史档案。
- 任何 `estimate_td=1 + 7.36ms shift` 组合都禁止。
- `d405_rgb_ir_imu_config.yaml`是RGB+左IR伪双目历史实验，不能替代双IR当前配置。
