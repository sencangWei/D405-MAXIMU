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

- `--clock-offset-ms` 是固定设备级偏移，默认 `0`；不会针对单次数据自动搜索最优偏移。可用 `scripts/estimate_robot_umi_time_offset.py` 对多组同步录制估计候选值，再用 `scripts/aggregate_robot_umi_time_offsets.py` 聚合并显式加载：

```bash
python3 scripts/compare_robot_umi_precision.py \
  --slam-dir <SLAM回放目录> --robot <robot_joints.jsonl> --out <空目录> \
  --clock-offset-file <robot_umi_clock_offset_calibration_v1.json>
```

该文件只描述“在 UMI 时刻查询机械臂 `robot(umi_t + offset)`”的评估映射，绝不能替代 VINS 配置中的相机—IMU `td`。
- 机械臂 TCP 使用时间线性插值；姿态使用四元数 SLERP。
- 默认最近时间差门限为 10 ms（可用 `--max-pair-error-ms` 调整），但在线性插值模式下该门限只作诊断统计；插值括区默认不超过 60 ms，括区无效的样本才会被剔除。
- 报告同时给出超过最近样本门限的数量，避免把独立 30 Hz 采样相位差误报成丢帧或时钟错误。

## 输出

- `trajectory_metrics.json`：机器可读的 A/B 指标、配对统计和独立的主臂/从臂跟随误差。
- `precision_report.md`：中文报告。
- `matched_relative.csv`：时间门控后的配对与一次 SE(3) 对齐结果。
- `robot_tcp_vs_umi_3d.png`：静态三维对比图（不再混入二维投影）。
- `robot_vs_umi_two_3d.html`：无外部依赖、浏览器鼠标可旋转的三维对比图。
- `evo/evo_ape.txt`、`evo/evo_rpe_1s.txt` 和对应 zip：EVO 原始结果（RPE 使用 30 帧近似 1 秒；严格时间窗指标以报告 A 部分为准）。

如果要把评估点从 URDF 的 `gripper_end` 原点改成 UMI 左夹爪实际 TCP，先按产品模板
`/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/calibration_assets/umi_left_jaw_tcp_offset.example.json`
测量偏移，再用工具生成 JSON：

```bash
python3 /home/robot/umi_docker2_product_1.0.0-20260829/tools/write_umi_left_jaw_tcp_offset.py \
  ~/umi_ego_vio_data_device2_c48df736/calibration/umi_left_jaw_tcp_offset.json \
  <x_mm> <y_mm> <z_mm>
```

然后在上面的评估命令中追加：

```bash
  --gripper-tcp-offset ~/umi_ego_vio_data_device2_c48df736/calibration/umi_left_jaw_tcp_offset.json
```

这里是物理测量，不是第二次手眼求解；评估器拒绝 `measured: false` 模板。

报告中的三类含义：

1. A：0.1/0.5/1.0 秒位移长度、对齐向量、方向和角度增量误差，以及共同有效区间的累计距离差。
2. B：一次无尺度 SE(3) Kabsch 对齐后的形状误差；不强制首尾闭合。
3. C：仅在显式提供 `--handeye` 时计算 UMI→TCP 映射。未提供偏移时，映射链为 `T_world_gripper_end = T_world_body @ body_T_cam0 @ inv(T_gripper_camera)`；提供实测偏移后追加 `@ T_gripper_tcp` 得到左夹爪 TCP。世界坐标只用第一个有效配对姿态作一次固定锚定，不做整段 Kabsch、不使用终点答案。该项会保留手眼矩阵自身残差，当前状态是诊断项，不等同于绝对基座坐标验收。

`follower_tracking` 中的目标 TCP→实际 TCP误差和主臂→目标关节误差独立报告，不计入 SLAM 误差。
