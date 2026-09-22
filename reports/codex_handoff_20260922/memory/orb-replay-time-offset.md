---
name: orb-replay-time-offset
description: ORB 回放 IMU 时间偏移必须是 11.7ms(08-08 Kalibr td=-0.0117 换算); 陈旧 7.36 在 53cm 慢回路必致尺度爆炸(318m)
metadata: 
  node_type: memory
  type: project
  modified: 2026-08-09T16:37:05.826Z
  originSessionId: bd123ebc-72bc-46c2-a61d-5d9f3d3f6ff9
---

ORB-SLAM3 没有 td 概念,IMU-图像时间对齐全靠 replay 的 `--imu-shift-ms`。正确值 = **+11.7ms**(08-08 Kalibr `td=-0.0117` 换算,shift 加到 IMU 戳上)。

**shift 扫描实测**(d405_720p_all_20260810_001100,53×43cm 方形回路):
| shift | ORB 路径 | 闭环 | 结论 |
|---|---|---|---|
| 7.36(旧默认,08-04 陈旧) | 318 m | 爆炸 | 必现尺度发散 |
| 10.0 | 7.6 m | 45 cm | 发散 |
| **11.7** | 1.6–4.7 m | **最佳 3.5 cm** | 最优点 |
| 13.0 | 3.0 m | 22.5 cm | 发散 |

**已提交**: ego_vio_humble `13240c2`,`_test_orb_dynamic.py` 默认 `ORB_SHIFT_MS=11.7`。VINS 侧配对:shift=0 + config `td=-0.0117`(见 [[dual-ir-divergence-rootcause]])。

**Why:** 陈旧 7.36 来自 08-04 标定,08-08 Kalibr 修正为 -11.7ms;ORB 与 VINS 时间补偿必须一致,否则小回路(慢速、视差小)上 IMU 传播误差盖过视觉约束 → 尺度/偏置解坏。

**How to apply:** 跑 ORB 前不要覆盖 `ORB_SHIFT_MS`(默认即 11.7)。若改采集硬件/IMU,重新 Kalibr 并把换算值写进 `_test_orb_dynamic.py` 默认。

相关: [[orb-rgbd-inertial-status]] [[d405-hardware-facts]] [[vins-replay-args]]
