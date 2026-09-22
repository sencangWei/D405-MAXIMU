---
name: vins-process-hygiene
description: "VINS 测试前必须 pkill 清场,残留节点会同时订阅同一 topic 污染 odometry"
metadata: 
  node_type: memory
  type: project
  originSessionId: bd123ebc-72bc-46c2-a61d-5d9f3d3f6ff9
  modified: 2026-08-09T07:49:28.087Z
---

2026-08-09 踩坑: 手动诊断时 `kill $PID` 只杀了 `ros2 run` 包装进程,**真正的 VINS 节点子进程存活成为僵尸**,仍订阅 /cam0/image_raw + /imu0、仍发布 /odometry。之后每次测试,僵尸与正常 VINS 同时处理同一数据、odom_sink 收到混合垃圾 → 205703 变 207315m、111538 变 1.6e19m,被误判为"构建回归"。

**Why**: VINS 节点是 `ros2 run` 的 child,杀 wrapper 不杀 child。驱动 _test_vins_dynamic.py 用 `os.killpg(os.getpgid(vins.pid), SIGKILL)` 是对的(杀整个进程组),问题只出在手动 `&` 启动+直接 kill wrapper。

**How to apply**: 
- 批量脚本每迭代前 `pkill -9 -f vins_fusion_ros2_node; pkill -9 -f replay_db3`。
- 手动测试必须 `kill` 整个进程组或 `pkill -9 -f vins_fusion_ros2_node`。
- 出现"数值爆炸"(1e18m 级)结果时,先 `ps aux | grep vins_fusion` 查残留,不要直接怀疑算法回归。

相关: [[vins-230503-rootcause]] [[vins-replay-args]]
