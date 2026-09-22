---
name: dual-ir-divergence-rootcause
description: "双IR SLAM 发散根因=时间偏移双重补偿(shift=7.36 陈旧标定 + estimate_td:1);回放硬编码 IMU 旋转 R_rep≈91.42°,配置 ~90° bake 自洽必需(不存在\"57°\")"
metadata: 
  node_type: memory
  type: project
  originSessionId: bd123ebc-72bc-46c2-a61d-5d9f3d3f6ff9
  modified: 2026-08-09T13:35:57.475Z
---

双IR(左IR+右IR 真双目)SLAM 发散根因调查结论,2026-08-09 由子代理查明、主会话复核。

**根因(两层):**
1. **历史主因(已修复):** 回放脚本 `ego_vio_humble/scripts/replay_db3_to_ros2.py:241-253` 的 `publish_imu` **硬编码**把 IMU 旋转 R_rep≈91.42°(重力 y→z,注释"IMU 坐标系变换: 重力 y -> z 向下")。VINS/ORB 配置的 `body_T_cam0`/`T_b_c1` 因此 bake 了等量 ~90° 旋转才自洽(双IR 88.0°/mono 88.5°,子代理用 trace 法得 91.995°/91.54°,均≈90°)。**早期"57°"是误记/轴角小角近似算错,不存在。** 配置 bake 与回放旋转自洽(残差 0.267°),必须成对保留,缺一即崩/发散(见 [[orb-rgbd-inertial-status]] 物理外参负结果)。
2. **现存触发因素(未修复):** 时间偏移双重补偿——`--imu-shift-ms 7.36`(2026-08-04 陈旧 Kalibr 值)+ 配置 `estimate_td:1` 在线细化,对同一偏移补偿两次。**受控实验(仅改 shift,230503):** shift=0 → 有界 1.1m;shift=+7.36ms → 发散 846m。

**已验证正确项:** cam0=leftIR(正确);left/right 内参相同是 rectified 立体正常现象(非 bug);IMU 数据健康(静止陀螺≈0、加速度模≈9.8)。**已排除:** 外参方向错误、帧号/时间戳丢失、纯视觉。

**08-08 Kalibr**(/tmp/calib_run/calib_imucam-camchain-imucam.yaml):T_cam_imu 旋转 1.41°,timeshift=**-11.73ms**(≠ 旧的 7.36)。

**修复建议(已实施验证,2026-08-09):** 配置 `estimate_td:0` + 固定 `td=-0.0117`(08-08 Kalibr),验证会话必须 `--imu-shift-ms 0` 避免双补偿。已在 111538(静止/微动会话)验证 2 次:235/235、212/212 全好帧,路径 3.3m,INIT-GUARD 0 次拒绝。**但注意 230503(手部动态)即使方案A 也难**(144 点仅 27 好帧,12 次 INIT-GUARD 拒绝)→ 选验证会话要用静止/微动会话(111538、06_192749 有 60s 静止段),不要用手部动态会话。

**新发现(独立 bug):** `/odometry` 发布存在 TOCTOU 数据竞争(getVisualInertialOdom 先 check() 再 get(),之间另一线程 set() 写入新数据),间歇混入异常帧(一次 est_td:1 跑出现 42% 坏帧位置 1e35m;方案A 跑 0 坏帧)。`SafeClass<OdomData> safe_vio_odom` 有 mutex 但 check+get 非原子。**影响**:轨迹统计会被坏帧污染(路径/速度爆炸),需过滤 >1m 帧或用 get(force=true) 原子读。未修,后续可优化。

相关: [[vins-replay-args]] [[vins-fork-state]] [[vins-230503-rootcause]]
