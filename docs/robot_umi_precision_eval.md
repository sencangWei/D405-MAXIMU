# UMI 与机械臂 TCP 评估

`scripts/compare_robot_umi_precision.py` 默认只做不使用手眼矩阵的形状诊断；传入 `--handeye` 后，会额外把 UMI 传感器轨迹映射到夹爪/TCP 坐标，再单独输出 TCP 映射诊断。它只读取已经完成的 SLAM 输出和机械臂日志，不把机械臂数据反馈给 SLAM，也不接受终点重合答案。

## 运行

```bash
cd /home/robot/ego_vio_humble
python3 scripts/compare_robot_umi_precision.py \
  --slam-dir <SLAM回放目录> \
  --robot <robot_joints.jsonl> \
  --out <空的输出目录>
```

启用手眼到 TCP 的映射诊断：

```bash
python3 scripts/compare_robot_umi_precision.py \
  --slam-dir <SLAM回放目录> \
  --robot <robot_joints.jsonl> \
  --out <空的输出目录> \
  --handeye /home/robot/umi_ego_vio_data_device2_c48df736/robot_eval/handeye/handeye_online_01/umi_tcp_handeye.json \
  --body-to-camera /home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/formal_runtime_calibration/vins_config.yaml
```

默认输入必须是 `<SLAM回放目录>/vio_corrected_stream.csv`，即完整 30 fps corrected 轨迹；找不到时会直接报错，不会静默退回低频 `loop_output/vio_loop.csv`。

机械臂参考使用从臂 `actual_deg` 的 B601-RS URDF FK。`leader_deg` 和 `target_deg` 只用于单独的跟随诊断，不作为 UMI 的真值。

## 时间配对契约

- `--clock-offset-ms` 是固定设备级偏移，默认 `0`；不会针对单次数据自动搜索最优偏移。
- 机械臂 TCP 使用时间线性插值；姿态使用四元数 SLERP。
- 默认最近时间差门限为 10 ms（可用 `--max-pair-error-ms` 调整），插值括区默认不超过 60 ms。
- 超过门限的样本会被剔除并在报告中计数；不使用最近点硬配替代插值。

## 输出

- `trajectory_metrics.json`：机器可读的 A/B 指标、配对统计和独立的主臂/从臂跟随误差。
- `precision_report.md`：中文报告。
- `matched_relative.csv`：时间门控后的配对与一次 SE(3) 对齐结果。
- `robot_tcp_vs_umi_3d.png`：三维、XY、XZ、YZ 对比图。
- `evo/evo_ape.txt`、`evo/evo_rpe_1s.txt` 和对应 zip：EVO 原始结果（RPE 使用 30 帧近似 1 秒；严格时间窗指标以报告 A 部分为准）。

报告中的三类含义：

1. A：0.1/0.5/1.0 秒位移长度、对齐向量、方向和角度增量误差，以及共同有效区间的累计距离差。
2. B：一次无尺度 SE(3) Kabsch 对齐后的形状误差；不强制首尾闭合。
3. C：仅在显式提供 `--handeye` 时计算 UMI→TCP 映射。映射链为 `T_world_tcp = T_world_body @ body_T_cam0 @ inv(T_gripper_camera)`，世界坐标只用第一个有效配对姿态作一次固定锚定，不做整段 Kabsch、不使用终点答案。该项会保留手眼矩阵自身残差，当前状态是诊断项，不等同于绝对基座坐标验收。

`follower_tracking` 中的目标 TCP→实际 TCP误差和主臂→目标关节误差独立报告，不计入 SLAM 误差。
