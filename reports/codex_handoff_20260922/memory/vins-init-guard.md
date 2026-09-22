---
name: vins-init-guard
description: VINS stereo-IMU 初始化守卫实现细节:鲁棒 solveGyroscopeBias(Huber IRLS) + 总 bg 检查 + 回滚 + 0.01 阈值
metadata:
  node_type: memory
  type: project
  originSessionId: bd123ebc-72bc-46c2-a61d-5d9f3d3f6ff9
  modified: 2026-08-09T08:15:10.692Z
---

2026-08-09 实施的 VINS stereo-IMU 初始化守卫(让 6/8 会话稳定,cm 级轨迹):

1. **鲁棒 solveGyroscopeBias**(initial_aligment.cpp):原版最小二乘把 1-2 个坏帧对(移动手/模糊)的 PnP 旋转噪声吸进 bg(230503 曾 8°/s)。改为 Huber 迭代重加权 IRLS,delta=0.025 rad(~1.4°),3 次迭代。返回 Vector3d delta_bg(签名从 void 改)。
2. **守卫检查总 bg 而非增量**(estimator.cpp processStereoWithImuInitialization):solveGyroscopeBias 内部 `states[].gyro_bias += delta_bg`。**踩过的坑**:守卫必须检查 `estimator_state[0].gyro_bias.norm()`(总量)而非 delta_bg(增量)——被拒的脏增量不回滚会残留进下一窗口,而下一窗口增量是相对脏 bg 的修正(小),守卫误放行(230503:0.074 未回滚 + 0.026 增量 = 0.099 总 bg → 发散)。触发时回滚 `states[i].gyro_bias -= delta_bg` 再 slideWindow+return。
3. **阈值 0.01 rad/s**(~0.57°/s):本 IMU 干净会话 bg 0.0009-0.0015,污染窗口 0.04-0.16,25 倍间隙。0.05 会让 2.5°/s 脏窗口溜进 NON_LINEAR 再发散(230503 skip=1.5:0.043 通过→3665m)。0.01 保证只有真干净才放行,污染场景诚实报失败而非垃圾轨迹。

**How to apply**: 改这两个文件后必须 rebuild 并 pkill 清理再复验。判定标志:[DIAG-BG] <0.01=干净,[INIT-GUARD] count=N=重试 N 次。正常会话 6 个都 pass(count 0),230503 类动态场景会 count>0 最终 0 点(诚实失败)。

相关: [[vins-230503-rootcause]] [[vins-fork-state]]
