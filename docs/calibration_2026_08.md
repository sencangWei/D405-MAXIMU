# D405 + KT-EX9-2 标定结果 (2026-08)

供任何标准 VINS-Fusion / ORB-SLAM3 使用, 不依赖本仓库的代码修改。

## 硬件
- 相机: Intel RealSense D405, SN 260322273737
- IMU: KT-EX9-2 (外置, 串口 UART 921600, 400Hz)
- 立体基线: 18.079mm (出厂)

## 1. 相机内参 (D405 出厂 rectified, 无畸变, Y8 IR 流)

左 IR (cam0) 和右 IR (cam1) 相同:
```
fx = 647.5198   fy = 647.5198
cx = 638.5343   cy = 369.7683
畸变 = [0,0,0,0]  (硬件 rectified, 无畸变)
分辨率 = 1280x720
```
来源: D405 出厂标定 (rs2_intrinsics coeffs=0, 官方确认 rectified)。

## 2. 相机-IMU 外参 (VINS body_T_cam0/cam1)

**来源: 用户 2026-08-08 相机-IMU 联合标定 (Kalibr)。** 配置里的 body_T_cam0/cam1 即此值。
`body_T_cam0 = R_gravity @ inv(T_cam0_imu)`, 其中 R_gravity 是 IMU 重力 Y→Z 变换。
静态验证 0.2cm 最优。

```
body_T_cam0:
[ 0.99968852,  0.00656964, -0.02407688, -0.01528376,
  -0.02383466, -0.03474416, -0.99911198, -0.02793015,
  -0.00740034,  0.99937464, -0.03457675, -0.01236338,
   0.0, 0.0, 0.0, 1.0 ]

body_T_cam1:
[ 0.99966951,  0.00672528, -0.02481216,  0.00281157,
  -0.02457859, -0.03283312, -0.99915858, -0.02812380,
  -0.00753427,  0.99943822, -0.03265697, -0.01245857,
   0.0, 0.0, 0.0, 1.0 ]
```

注: 2026-08-08 新标定 (Kalibr + 手眼) 结果见 `/tmp/calib_run/` 和 `scripts/handeye_calibration.py`, 但旧外参静态最优。

## 3. 相机-IMU 时间偏移

- Kalibr 双目标定 (2026-08-08): timeshift = -11.7ms (t_imu = t_cam - 11.7ms)
- VINS回放端不再平移IMU时间戳：`--imu-shift-ms 0`，配置固定`td=-0.0117`。
- ORB回放端使用`--imu-shift-ms 11.7`。陈旧`7.36ms`已废弃。
- **当前VINS必须使用`estimate_td: 0`**，避免固定偏移与在线估计重复补偿。

## 4. IMU 噪声 (Allan 实测 2026-08-08)

```
acc_n (噪声密度) = 8.28e-3   m/s²/√Hz
gyr_n (噪声密度) = 1.03e-3   rad/s/√Hz
acc_w (随机游走) = 3.92e-6   m/s³/√Hz
gyr_w (随机游走) = 2.89e-7   rad/s²/√Hz
g_norm = 9.805
```

## 5. 数据采集格式 (VINS 后处理输入)

```
recordings/d405_720p_rgb_stereo_ir_时间戳/
  d405_720p_rgb_stereo_ir.db3   # 图像 rosbag2 (左右IR Y8 + RGB YUYV)
  d405_frames.csv               # 相机时间戳 (设备全局时间)
  external_imu/imu.bin          # IMU (400Hz, <dI7f 格式)
  external_imu/imu_ts.csv
```

## 6. 回放命令

```
python3 scripts/replay_db3_to_ros2.py --session <目录> --mode stereo --rate 1.0 --skip-s 0
```

## 关键经验 (调试记录)

1. **D405 Y8 IR 是硬件 rectified (无畸变)**, 用出厂内参, 不要用 Kalibr 的原始畸变模型
2. **Kalibr 内参/外参是耦合的**: 用 Kalibr 外参必须配 Kalibr 内参, 配出厂内参会发散
3. **手眼标定** (固定出厂内参解外参) 静态 2.3cm 可用
4. **静态可跑 (0.2cm), 动态发散** → 本机 VINS 动态处理不稳定, 建议换电脑/算法验证
5. **录制开头静止 5-8 秒** 让 VINS 正确初始化重力
