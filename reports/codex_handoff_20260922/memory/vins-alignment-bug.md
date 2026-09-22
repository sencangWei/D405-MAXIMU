---
name: vins-alignment-bug
description: "分析脚本 Umeyama 实现有转置 bug,曾误判 VINS 深度反转;正确实现下 VINS 轨迹完全正常"
metadata: 
  node_type: memory
  type: project
  originSessionId: bd123ebc-72bc-46c2-a61d-5d9f3d3f6ff9
  modified: 2026-08-09T06:51:33.670Z
---

2026-08-09 确认:此前的"VINS 深度方向反转/镜像/发散"结论全部是 **GT 对比分析脚本里 Umeyama 实现错误** 造成的测量伪影。

**Bug**: 对行向量点集(Nx3),我用的 `H = Qcc.T @ Pcc; R = U@Vt` 最大化的是 `trace(R^T H)`,而误差交叉项实际是 `trace(R·H)` —— 转置反了。合成数据验证:错误版本 ATE=0.50(应为0),正确版本 ATE=0.0000。

**正确约定**:
- `H = Pcc.T @ Qcc; R = U@Vt` (或等价 `SVD(Qcc.T @ Pcc)=U,S,Vt; R = Vt.T @ U.T`)
- scale `s = sum(S)/sum(Qcc^2)`, t = mp - s*R@mq, aligned = s*Q@R.T + t

**How to apply**: 以后任何 VINS↔GT 对齐一律用 /tmp/umeyama_correct.py(已验证正确),绝不用 H=Q^T P + R=U@Vt。检测方法:若 trace(R^T H) 与合成已知真值不符,或合法旋转(R2@Rz)能达到比 Umeyama 更低的 ATE,就是对齐 bug。

**验证结果(正确对齐下)**:
- 205555 动态 AprilGrid: ATE=1.48cm, scale=1.0191, det=+1, 前段1.09/后段1.20cm
- 205703 动态 AprilGrid: ATE=1.79cm, scale=0.9963, det=+1, 前段1.30/后段1.31cm
- 时间戳干净(shift=0)、基线18.1mm正确、VINS 算法本身轨迹正确

相关: [[vins-replay-args]]
