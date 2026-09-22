---
name: imu-sixface-calibration-null
description: "六面加计标定(2026-08-03)实测绝对刻度 X/Y/Z = 1.000059/1.000086/1.000587 ⇒ 是零修正, 传感器本来就准; 该候选本身验收 FAIL(runtime_applied: false)且有 LEGACY_DO_NOT_RUN, 其加计半边却被摘进运行时 yaml 并改写成 PASS; 它撑不起 IMU 尺度那 24%"
metadata: 
  node_type: memory
  type: project
  originSessionId: 616ba18d-61b7-4aba-830c-5738635103f2
  modified: 2026-09-19T06:26:46.609Z
---

## 它是什么、哪来的

**2026-08-03** 做于 `imu_manual_calibration/intrinsic_20260803_000908/`(不是用户记错 ——
这次标定**没有产出任何可用的东西**, 所以"不记得标定过"是合理的)。10 段 400Hz:
`static_bias`(115.5s) + 六个静止面 ±x/±y/±z + 三组手工旋转 roll/pitch/yaw。

**"绝对刻度"的物理含义**: 加计六个面各应读 **±1.000 g**, 同轴两面配对平均 = 该轴刻度。

| 面向 | 读数模长 (g) |
|---|---|
| +X / −X | 0.997341 / 1.002778 |
| +Y / −Y | 0.999782 / 1.000391 |
| +Z / −Z | 1.002966 / 0.998208 |
| **配对平均 = 绝对刻度** | **X 1.000059 · Y 1.000086 · Z 1.000587** |

⇒ **三轴全在 1.0000, 最大偏差 0.06%**。矩阵近似单位阵**不是因为标定修准了, 而是
因为这个加计本来就准**。非平凡部分只有**零偏 ≈0.002 g ≈ 0.02 m/s²**。
⇒ **这是一条零修正, 物理上不可能撑起 [[fusion-scale-gate-umeyama-check]] 那 24%。**

## 验收其实是 FAIL, 但半边被"洗白"进了运行时

`calibration_candidate.yaml` 自记:

    status: FAIL        runtime_applied: false
    failures: static_neg_z 14.69s<20s; rotate_yaw 间隔 0.100224s>0.01s;
              rotate_yaw counter 不连续 251 次; 手工90度拟合 RMSE 4.22°>3°

同目录 `LEGACY_DO_NOT_RUN.md`: 手工 90° 陀螺矩阵 A/B 使闭环 3.60cm→17.84cm,
**"禁止加载到实时采集或VINS"**, 主工程运行时加载器主动拒绝该文件。

**但** `config/imu_runtime_accel_calibrated_raw_gyro_20260816.yaml` 的 `source` 字段
**正指向这份 FAIL 候选**, 从中**摘走加计那半、丢掉陀螺那半**, 并把验收改写成
**`status: PASS` / `runtime_applied: true`**。
⇒ 真实的文书不一致: 被判死的文件, 其加计分支以"PASS + runtime_applied"的名义活着,
**而产品链(VINS 回放)根本不读它** —— 只有离线 MASt3R 尺度脚本在读
(见 [[docker-vins-imu-chain]])。

## 相关事实

- 最弱那段是 `static_neg_z`: 只采 14.695s(<20s 建议值), 且它的 `accel_std_g` 三轴
  最大(0.00078/0.00095/0.00136, 其余面 ~0.0007) ⇒ **−Z 面是六面里质量最差的一个**。
- **IMU 侧标定文件与 td 在全历史 114 份 `imu_scale_report.json` 里逐位不变**
  (`imu_calibration` 全指向该文件, `td_s` 全是 −0.009109323); 该文件 mtime
  08-16 16:35 早于管线最早运行(09-08) ⇒ **不存在 IMU 配置漂移**。
- **时间戳也已排除**: 相机 `infrared_left_mono` 与 `imu.bin` 的 `ts` 是**同一宿主时钟**,
  偏置 <1ms, 整条 take 极差 2.5ms(= 一个 400Hz 采样周期, 纯最近邻量化),
  `device_ms→mono` 斜率 1.00000000, 好/坏两侧逐位相同。
  ⇒ `camera_epoch_to_monotonic` 的插值成立, **无时间基错配**。

相关: [[fusion-scale-gate-umeyama-check]]、[[docker-vins-imu-chain]]、[[d405-hardware-facts]]
