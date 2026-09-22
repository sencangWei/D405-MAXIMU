---
name: vins-replay-args
description: "_test_vins_dynamic.py 默认参数已于 2026-08-10 修复(shift 7.36→0, align 0.0001→0);仍需 session 完整路径 + 双 source"
metadata:
  node_type: memory
  type: project
  originSessionId: bd123ebc-72bc-46c2-a61d-5d9f3d3f6ff9
  modified: 2026-08-09T16:37:37.155Z
---

/home/robot/ego_vio_humble/scripts/_test_vins_dynamic.py 的 argv 回退默认值 **已于 2026-08-10 修复**(commit 13240c2):`--imu-shift-ms` 默认 "7.36"→"0"、`--imu-align-s` 默认 "0.0001"→"0"。现在零参数跑即正确,显式传 `1.5 1.0 0 0` 仍等效可用。

**为什么默认是 0:** shift=7.36 是 08-04 陈旧 Kalibr,配置已固定 `estimate_td:0` + `td=-0.0117`(08-08 标定) → 回放若再预平移就是双重补偿 → 发散(见 [[dual-ir-divergence-rootcause]])。

**仍必须遵守:**
- **session 必须是完整路径** `/home/robot/ego_vio_humble/recordings/<session>`(回放按 cwd 相对解析裸名会报"会话里没有 db3" → VINS 收不到数据 → 轨迹 0)。
- 运行前 source 双 setup: `/opt/ros/humble/setup.bash` + `/home/robot/ros2_ws/install/setup.bash`(只 source 前者 → "Package 'vins_fusion_ros2' not found")。
- 测试前 `pkill -f 'vins_fusion_ros2_[n]ode'` 清场(见 [[vins-process-hygiene]])。
- 可用 `VINS_OUT=...` 环境变量指定输出 csv。

相关: [[vins-alignment-bug]] [[orb-replay-time-offset]]
