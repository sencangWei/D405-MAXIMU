---
name: docker-vins-imu-chain
description: "Docker/VINS 那条链怎么用 IMU: db3 里根本没有 IMU 话题(只有 D405 的 69 个相机话题), IMU 单独在 external_imu/imu.bin 由回放器注入 /imu0; 回放只做 raw*9.80665 + 旋转, 【不施加六面加计标定】—— 而那份标定自称 runtime_applied: true 且离线 MASt3R 尺度脚本在读它; 实测两条链差 0.32% 重力, 换算法使 s 变 2-3%"
metadata: 
  node_type: memory
  type: project
  originSessionId: 616ba18d-61b7-4aba-830c-5738635103f2
  modified: 2026-09-19T06:14:43.546Z
---

## 结构性事实(2026-09-19 实测)

`recordings/<take>/d405_720p_rgb_stereo_ir.db3` 的 topic 表里**只有 D405 自己的
69 个话题**(Image / camera_info / option / info),**没有任何 IMU 话题**。
IMU 单独在 `external_imu/imu.bin`。

⇒ **VINS 与 MASt3R 尺度脚本读的是同一份 `imu.bin` 字节**,差异只可能在换算。

## 两条链的换算不同(核心)

| | MASt3R 尺度脚本 | Docker / VINS 回放 |
|---|---|---|
| 位置 | `scripts/align_mast3r_scale_with_imu.py:80-93` | `scripts/replay_db3_to_ros2.py:178-188` |
| 加计 | **六面标定** `(raw @ M.T + offset)` | **不施加任何标定**,直接 `raw` |
| 重力常数 | `× 9.805` | `× G0 = 9.80665` |
| 陀螺 | 不使用 | `× DEG2RAD`(原始比例, 无刻度标定) |
| 轴向旋转 | `@ R.T` | `R @`(同一个 `load_vins_imu_rotation()` 默认阵) |

`config/imu_runtime_accel_calibrated_raw_gyro_20260816.yaml` 的验收块写着
**`runtime_applied: true`**、文件名与 purpose 都自称"运行时产品候选" ——
**但运行时(回放器)根本没读它**, 只有离线 MASt3R 尺度分析在读。
⇒ **文档与实现不符。**

两条链的加速度差 = **0.031–0.032 m/s² = 重力 0.32%**。

## 换算法对 `s` 的影响:2–3%,不是根因

同一份 `imu.bin` + 同一条视觉轨迹, 只换换算方式:

| take | s(带标定) | s(docker) | Δs | 比值(带) | 比值(docker) |
|---|---|---|---|---|---|
| GOOD 09-14_202142 | 0.35478 | 0.34662 | −2.30% | 0.980 | 0.958 |
| BAD 09-17_233028 | 0.62357 | 0.60715 | −2.63% | 1.277 | 1.243 |
| BAD 09-17_235329 | 0.65093 | 0.63625 | −2.26% | 1.254 | 1.225 |
| BAD 09-18_002714 | 0.79893 | 0.77112 | −3.48% | 1.237 | 1.194 |
| BAD 09-18_003403 | 0.63925 | 0.61818 | −3.30% | 1.145 | 1.107 |

- 方向对但**只占 24% 缺口的 ~10%** ⇒ **不是** [[fusion-scale-gate-umeyama-check]] 那条 24% 的根因。
- **施加标定反而让分歧更大** ⇒ 该六面标定的**绝对刻度本身可疑**(需独立标定判, 不下结论)。

## 时间处理:只有 shift=0/align=0 时两链自洽

回放器在 `td` 之外还有 `--imu-shift-ms`(默认 0)与 `--imu-align-s`(可为 `auto`)。
MASt3R 尺度脚本**只**用硬编码 `td=-0.009109323`(**与 `vins_config.yaml` 的 `td` 同值**,
`estimate_td: 0`)。⇒ **任何非 shift=0/align=0 的回放调用都会让两链静默错开。**

## 陀螺:VINS 只能估零偏, 估不了刻度(线索, 未定方向)

回放发的是 `gx*DEG2RAD` —— 原始比例无刻度标定(yaml policy 明写)。
VINS 在线估计的是陀螺**零偏**, **不是刻度**。

实测:视觉帧间转角 / 陀螺积分转角 = **1.0953 / 1.0959 / 1.0935**(好/坏/坏一致)
⇒ **陀螺与视觉旋转之间有约 +9.5% 系统比例差**。
⚠ **方向未定**:分不清是"陀螺低 9.5%"还是"视觉高 9.5%", 需独立基准(如 Kalibr
相机-IMU 标定的陀螺刻度项)。无论哪侧, **产品链路把它原样喂给 VINS, 而 VINS 修不了比例**。

## 重力常数三处口径

`vins_config.yaml` `g_norm` = **9.805**;回放器/桥接 `G0` = **9.80665**;MASt3R 脚本 = **9.805**。
回放按 9.80665 换算却告诉 VINS 期望 9.805 ⇒ **0.017% 不一致**, 量级可忽略但同类问题。

相关:[[fusion-scale-gate-umeyama-check]]、[[vins-runtime-intrinsics-source]]、
[[vins-replay-args]]、[[orb-replay-time-offset]]
