# 正式产品代码与标定边界

## 运行代码

- 传感器读取、录制、Rerun 桥接：`ego_vio/`、`scripts/run_realtime.py`、
  `scripts/capture_d405_720p_rgb_stereo_ir.py`
- VINS：`components/vins_fusion_ros2/`
- 回环：`components/vins_fusion_ros2_product_loop/`
- 固件：`firmware/stm32f070_imu_encoder/`
- 实时入口：`run_vins_realtime.sh`
- 离线入口：`run_slam_postprocess.sh`

## 生效标定

- 设备与串口：`config/devices_product_live_stm32.yaml`
- 相机内参、相机—IMU外参与时间偏移：`config/product_live_stm32/`
- 夹爪状态：`config/gripper/umi_manual_gripper_20260824.yaml`

相机内参是 D405 出厂双 IR 参数；相机—IMU空间外参和 `td=-0.009312 s` 是当前固定
装配联合标定结果。两者不是同一类参数。夹爪标定也不属于 VINS 参数，只作用于训练
数据元信息。

## 标定工具和证据

`/home/robot/ego_vio_calib_kit` 是独立标定仓库。它保存客户分阶段标定脚本、手册、
原始标定证据和夹爪 App 接口；不会被实时入口 source，也不会覆盖上述产品配置。

## 禁止项

- 禁止 source `/home/robot/ros2_ws` 或复制旧 build/install。
- 禁止把历史 `td=-0.0117 s` 用于当前 STM32 固定装配。
- 禁止加载已被否决的加速度运行时修正或固定世界 Z 压平。
- 禁止让 App 再次打开 STM32 串口；夹爪数据必须从现有联合包解析链分发。
