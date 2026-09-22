---
name: d405-hardware-facts
description: D405 硬件关键事实:RGB↔左IR 基线≈0.01mm(伪双目退化)、Depth_Units=0.0001m(DepthMapFactor=10000 正确)、左IR 内参
metadata: 
  node_type: memory
  type: reference
  originSessionId: bd123ebc-72bc-46c2-a61d-5d9f3d3f6ff9
  modified: 2026-08-09T10:00:09.614Z
---

D405(8/4 组合)硬件事实,2026-08 实验确认:

- **RGB↔左IR 基线 ≈ 0.01mm**(本应 2cm 级):两传感器光学中心几乎重合 → 伪双目 VINS(把 RGB+IR 当 stereo)完全退化,深度无约束、轨迹爆炸。**结论:任何把 RGB 和左IR 当双目的算法都不可行。** 可行的 VINS 组合 = mono-RGB(单目)+IMU,IR-depth 只做点云/可视化,不进 VINS。
- **Depth 单位 = 0.0001m(0.1mm)**:从 bag 的 `Depth_Units` option topic 确认。ORB-SLAM3 RGB-D 的 `RGBD.DepthMapFactor: 10000` 是正确的换算(原始 Z16 × 0.0001m)。之前怀疑深度尺度是标定 bug 的方向被排除。
- **左IR 内参**(720p, PinHole):fx=fy=647.52, cx=638.534, cy=369.768, 畸变 0。配置在 `ego_vio_humble/config/orbslam3_d405_rgbd_inertial_720p.yaml`(ORB 用它做 RGB-D 视觉相机 + 外置 IMU)。

相关: [[orb-rgbd-inertial-status]] [[vins-fork-state]]
