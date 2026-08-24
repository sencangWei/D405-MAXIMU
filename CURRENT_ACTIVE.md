# CURRENT_ACTIVE — 正式客户产品链

本分支只允许一条运行链：Ubuntu 22.04 + ROS 2 Humble、D405 双 IR、STM32
63 字节 IMU/编码器协议、当前 VINS、当前自适应回环和 Rerun。

## 唯一实时入口

```bash
cd /home/robot/ego_vio_humble
./build_product_live.sh
./run_vins_realtime.sh
```

无参数默认就是 `product-live`。显式传入 `stable`、`frozen`、Jazzy 或任何诊断候选均会
直接失败，且不会搜索 `/home/robot/ros2_ws`、旧工作区或 `.planning` 中的二进制。

## 唯一后处理入口

```bash
cd /home/robot/ego_vio_humble
./run_slam_postprocess.sh /绝对路径/录制会话 /绝对路径/输出目录
```

后处理使用独立签名的全30 Hz VINS、产品自适应回环、同一产品标定和隔离构建哈希；
实时入口仍使用15 Hz后端。两条链不会互相复用VINS二进制，也不会回落到系统ROS
工作区中的旧节点。三个正式入口都会先清除继承的 ROS/colcon overlay，再只加载
Humble 与产品工作区。

## 正式配置身份

- 设备配置：`config/devices_product_live_stm32.yaml`
- VINS 配置：`config/product_live_stm32/vins_config.yaml`
- 左／右 IR：D405 1280×720 出厂去畸变参数，`fx=fy=647.519775`、
  `cx=638.534302`、`cy=369.76825`。本轮自由拟合相机内参已被真实轨迹 A/B 否决。
- 相机—IMU：当前固定装配 2026-08-22 两轮 Kalibr 共识外参；
  `estimate_td=0`、`td=-0.009312 s`。
- IMU：STM32 `stm32_combined_v1`、400 Hz；VINS 默认接收未改写原始量，运行时
  `imu.calibration` 为空。30 姿态加速度候选没有跨数据稳定通过，不进入产品配置。
- 回环：`components/vins_fusion_ros2_product_loop` 自适应回环，输出
  `/odometry_rect`；原始 VIO 同时保留 `/odometry`。
- 世界坐标系：每次启动由 VINS 初始化重力和初始朝向建立，不做人为固定角度压平。
- 静止保护：ZUPT 只在视觉和 IMU 共同支持静止时激活；运动证据立即释放。
- 夹爪：`config/gripper/umi_manual_gripper_20260824.yaml`，只记录训练状态，不进入
  VINS/SLAM 优化。

## 训练采集与时间同步

正式 STM32 采集会保存原始图像、IMU、约 400 Hz 夹爪状态和逐相机帧的最近邻夹爪
关联。IMU 首字节与编码器读取由同一个 MCU 定时器测量，实机间隔约 65–67 µs；
PC 端以连续 400 Hz 计数器和主机接收时钟驯服 MCU 时钟漂移，并保留这段实测差值。
相机关联继续使用 `td=-0.009312 s`。完整字段和验收门见
`docs/TRAINING_GRIPPER_SYNC_ZH.md`。

## 历史代码

历史 `<1 cm` 冻结复现链、Jazzy 候选、旧 `stable`、被否决 Z 候选只保存在 GitHub
不可变分支／标签中，不是本机产品运行时。版本映射见
`RELEASES_OLD_NEW_20260823.md`。
