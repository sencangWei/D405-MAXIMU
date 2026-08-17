# 历史 `<1 cm` 冻结链

## 已确认的链路

历史四组闭环图不是 `c0cf726` 生成的。它们使用：

1. VINS 双 IR 30 fps + KT-EX9-2 IMU 400 Hz；
2. VINS 固定时间偏移 `estimate_td=0`、`td=-0.0117`，回放额外 shift 为 `0 ms`；
3. VINS 仓库基线 `a3a38b82da5ed7b7fd3de11e6dc1f42287472cf8`；
4. `loop_fusion` 的 direct-BRIEF 跟踪点对应；
5. 冻结回环二进制 `lfn_product_origin_ready_v7`，SHA256：
   `8148cc99945e56c38151254da7aae38269892efb5d6786c6b003e97e8d550001`；
6. 三维闭环指标是轨迹首尾欧氏距离，不是二维投影距离。

`c0cf726` 只增加 place-retrieval 排名日志，不能作为历史封版链的替代品。

## 当前电脑的隔离部署

- 干净原版工作树：`/home/robot/ros2_ws_vins_frozen_a3a38b8`
- 当前历史冻结回环二进制：`frozen_chain_a3a38b8/bin/lfn_product_origin_ready_v7`
- 同版本重建的 VINS/回放前缀：`frozen_builds/20260817_191957/`

当前实时入口增加了显式 `frozen` 模式；默认 `stable` 仍保留当前工作区版本，
不会被静默替换：

```bash
cd /home/robot/ego_vio_humble
./run_vins_realtime.sh frozen
```

如果需要**同一次采集**同时得到准确实时轨迹和可复放的原始 DB3＋IMU，使用唯一硬件
采集者的 `frozen-record` 模式；不要另开两个会抢占相机/串口的命令：

```bash
cd /home/robot/ego_vio_humble
./run_vins_realtime.sh frozen-record --duration 60
```

它将同一份双 IR 帧和 IMU 样本同时送给冻结 VINS 与原始记录器；Rerun 只订阅
`/odometry_rect`、`/cam0/image_raw`、`/imu0`，不再额外打开设备。

启动日志必须打印冻结二进制路径和上面的 SHA256。Rerun 继续显示
`/odometry_rect`，原始轨迹仍记录为 `/odometry`。

## 复现边界

当前电脑已完成源码隔离构建和依赖检查；由于历史录制数据已移入系统回收站，尚未在
本轮重新跑四组数据。要重新验收，必须恢复对应录制目录，并保持相同相机配置、
`td`、IMU 轴变换和回放参数。不得把当前 `c0cf726` 的 `loop_fusion_node` 混入该链。
