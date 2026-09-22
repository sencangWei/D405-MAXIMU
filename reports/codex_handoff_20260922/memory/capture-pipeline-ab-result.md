---
name: capture-pipeline-ab-result
description: "采集管线 A/B(2026-08-10 84x52 矩形对照): 旧 db3→FFV1 闭环 2-3cm 稳定, 新 inline mkv 14-74cm+尺度膨胀, 差异锁定在采集方法"
metadata: 
  node_type: memory
  type: project
  originSessionId: bd123ebc-72bc-46c2-a61d-5d9f3d3f6ff9
  modified: 2026-08-10T12:33:10.743Z
---

2026-08-10 用户对照实验(旧 capture_d405_720p_rgb_stereo_ir.py vs 新 capture_d405_mp4_inline.py,同一 84x52 矩形动作,各录一段):

| 会话 | 闭环 R1/R2/R3 | 路径 | 特征曲线 |
|---|---|---|---|
| OLD (db3→FFV1, 20260810_200101) | 3.2/2.4/2.1cm | 2.5-2.6m 稳定 | min=2, median=194, 从不塌0 |
| NEW (inline mkv, 20260810_200218) | 73.8/14.4/33.7cm | 3.1-5.1m 尺度膨胀1.2-2x | 反复塌到0(每~5s) |

**隔离实验**: OLD 走 replay_db3 原始路径同样闭环 2.1cm → 差异锁定在**采集方法本身**,与 FFV1 编码/回放路径无关。

**已排除的诱因**: IMU 数据(两会话 400Hz 零跳变)、图像时间戳(均零大间隙)、mkv/sidecar 帧对齐(均 1:1)、丢帧(均~3帧)、skip 不对称(NEW skip=0.3 仍~19cm, anchor 到最早流导致 NEW 丢前~35帧)、全局亮度/纹理/帧差、估计器处理节奏(~50-60ms/帧两会话相同)、mkv 帧大小(171-173KB)。

**机制**: NEW 会话特征塌缩(feat→0)导致尺度膨胀+闭环差;OLD 从不塌缩。微因未完全定位(倾向内容相关),但经验结论确凿: **关键录制必须用旧 db3 管线**。

**Why**: 新 inline 管线作为采集便利(直接 mkv)会产生 VINS 特征塌缩,交付录制用它有失败风险。
**How to apply**: 交付/关键录制一律用 `capture_d405_720p_rgb_stereo_ir.py` (db3)→`bag_to_ffv1.py`;inline 管线仅作快速预览,不用于正式数据。相关 [[recording-format-ffv1-lossless]] [[dual-ir-divergence-rootcause]]。
