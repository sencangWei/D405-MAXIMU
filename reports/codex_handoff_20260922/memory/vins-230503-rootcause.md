---
name: vins-230503-rootcause
description: 230503 手部会话发散根因:手占满画面→初始化 PnP 旋转噪声→gyro bias 污染(2.5-9.4°/s)→特征全崩→纯 IMU 死推算;动态场景根本性限制(calm-start 4 窗口全败)
metadata: 
  node_type: memory
  type: project
  originSessionId: bd123ebc-72bc-46c2-a61d-5d9f3d3f6ff9
  modified: 2026-08-09T08:20:18.948Z
---

2026-08-09 确认手部会话 230503(d405_720p_rgb_stereo_ir_20260808_230503)发散的完整根因链:

1. **数据本身干净**(IMU 400Hz 零丢、零毛刺;gyro std 仅 0.27 rad/s,峰值 1.5 rad/s —— 之前"680°/s 剧烈运动"是单位误判,raw 是 deg/s)。
2. **初始化窗口是回放 skip 后第一个 ~100ms**(prevTimestamp=-1 → getIMUInterval 取缓冲开头),该窗口 avg acc 倾角 36°。
3. **关键:每帧对 视觉PnP旋转 vs IMU预积分旋转 差 diff_norm=0.007~0.045 ≈ 0.5~2°**([DIAG-ROT] 日志),方向随机。solveGyroscopeBias 最小二乘把噪声吸进 bg → **bg=(-0.02,-0.095,-0.10)=8°/s**(111538 只有 0.0005)。
4. 大 bg → 预积分旋转错 → 优化无共识 → outliersRejection(>3px)删光特征 → **feat 355→0** → 纯 IMU 死推算 → 6000m 发散。
5. PnP 噪声来源:IR 模糊(Laplacian 5.5 vs 111538 的 14.6)、特征极少(goodFeaturesToTrack 56 vs 126)、**移动的手填充画面(非刚体场景破坏静态假设)**。
6. KLT 跟踪本身 98% 成功 —— 不是跟踪崩的,是优化离群删除崩的。

**帧丢弃(inputImageCount%2==0)**:fork 首个 commit 就有,估计 15fps。移除后 205703 从 ATE=1.79cm 恶化到 1075m(66ms 帧间旋转大、PnP 噪声占比小→bg 干净;33ms 时噪声占比大)。**必须保留**。

**predictPtsInNextFrame()**:fork 里定义了但从未被调用(死代码),上游在 processImage NON_LINEAR 分支调用,是快速运动鲁棒性关键。但 multiple_thread:1 下上游也不在 processImage 调。

**calm-start 决定性实验(2026-08-09)**:230503 用 skip_s=1.5/5/10/14 找平静起始窗口,**4 窗口全败**——首个 DIAG-BG 分别为 2.5°/9.4°/6.8°/8.8°(正常会话 0.05-0.09°),轨迹 3665/920/938/194m 全发散。40s 会话里没有任何 init 窗口能干净初始化(手全程占满画面)。守卫重试 98 次也只能找到"最不脏"的窗口。

**补强实验(0.01 严格阈值,同一构建)**:230503 重试 93 次后找到 bg<0.01 的真干净窗口并初始化,但 59 点后仍发散 89.9m——**污染不只在初始化,手占满画面的动态场景在滑窗优化阶段同样拖垮跟踪**。

**最终结论:230503 是动态场景在初始化和跟踪两个阶段的根本性限制,非时间戳/标定问题。** 针对性解法是动态物体剔除(dynamic-VIO:IMU 与视觉旋转不一致时用 RANSAC 离群点=静态背景,见下方链接),或部署时 init 期让手静止/移出画面。

**How to apply**: 判定会话是否适用:看 DIAG-BG 是否 <0.01 rad/s。bg 巨大=初始化 PnP 被污染(模糊/移动物体/特征少)。230503 类场景(手占满画面全程运动)当前守卫会诚实报初始化失败(0 点),需要 dynamic-VIO 才能跟踪。相关开源参考:github.com/jinguanzhu/VINS-Mono(dynamic-VIO), CEUR Vol-3248 paper21.

相关: [[vins-alignment-bug]] [[vins-replay-args]] [[vins-init-guard]]
