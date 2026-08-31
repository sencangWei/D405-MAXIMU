# 2026-08-31 多段轨迹鲁棒诊断与算法变更报告

## 目标

针对截图中 UMI 与从臂 TCP 局部不重合的问题，使用三段不同运动数据联合估计：

- 共享设备级时间偏移；
- 机身到从臂 `gripper_end` 的手眼旋转；
- 同一刚体的 TCP 杠杆臂；
- 不使用终点重合、闭环答案或整段 Kabsch 结果作为标定输入。

同时，要求大于约 10 mm 的自动回环修正经过更强的多帧双 IR + PnP 几何验证；模糊/低特征帧只降权，不删除真实运动。

## 截图段根因证据

本轮对 `/home/robot/umi_ego_vio_data_device2_c48df736/robot_eval/diagnostic/tcp_validate_20260830_191603_docker2_product_v1` 的已配对轨迹做了 5 秒分段诊断，结果不支持“单一固定平移”假设：

- 中段运动时 shape RMSE 降到约 12–18 mm；
- 起始和停止段回到约 26–30 mm；
- 分段偏差方向随时间改变，而不是保持同一个向量；
- 旧回放后端 `total_max30_ms` 最大 6418.23 ms，50/88 个后端采样超过 100 ms，20/88 个采样队列深度达到 10 以上；
- 旧日志没有 `AUTO_LOOP_LARGE_REJECT`，因此不能用它证明新大回环门禁已生效。

因此，截图差异不是把绿色轨迹整体平移几毫米就能解释。主要嫌疑按证据排序为：旧回放后端排队/长尾延迟与停止后跟随误差，其次是时间源仍为旧的 host-wall 文件时间；不能据此把全部误差归咎于 VIO 图像算法。

详细分段表与后端事件：
`/home/robot/umi_ego_vio_data_device2_c48df736/robot_eval/diagnostic/tcp_validate_20260830_191603_docker2_product_v1/segment_diagnosis_20260831/segment_diagnosis.md`

## 联合鲁棒估计结果

输入为三段多方向运动（平移、旋转、升降均有激励）。输出：

- 共享查询偏移：`22.49598 ms`；粗搜索最佳点为 `25 ms`，20–25 ms 是平坦区间；
- 联合残差 RMS：`3.4149 mm`，P95：`6.6835 mm`，10 mm 内比例：`97.829%`；
- 平移残差 RMS/P95：`3.3701 / 6.4907 mm`；旋转残差 RMS/P95：`0.3157 / 0.5594°`；
- 候选杠杆臂长度：约 `86.3 mm`；
- 结果状态：`PASS_CANDIDATE`，`auto_apply=false`。

候选文件：
`/home/robot/umi_ego_vio_data_device2_c48df736/robot_eval/diagnostic/joint_calibration_20260831/joint_three.json`

这说明相对运动方程可以得到稳定的刚体候选，但不代表绝对 TCP 已经达到 5 mm；绝对指标还包含从臂实际跟随、旧时间戳和世界坐标锚定误差。

## 时间偏移敏感性

在同一段数据、同一候选矩阵上比较 16、20、22.496、25 ms：

|偏移|绝对 TCP RMS|shape RMS|
|---:|---:|---:|
|16.0 ms|20.861 mm|22.966 mm|
|20.0 ms|20.856 mm|22.964 mm|
|22.496 ms|20.853 mm|22.964 mm|
|25.0 ms|20.852 mm|22.964 mm|

最大变化小于 `0.01 mm`，所以截图中的主要形状差异不是 16–25 ms 之间的单一偏移造成的。旧记录器没有电机内部生成时间戳，只有 CAN 内核接收事件可用；新记录会优先使用该事件时间，不能从旧 JSONL 事后恢复更早的电机生成时刻。

## 已实施的算法保护

### 视觉质量

- 左/右 IR 模糊、特征弱、双目比例低和高速运动会降低视觉因子权重；
- 帧仍保留在 VIO 轨迹和后端队列中；
- 不通过删除真实帧来获得好看的重合图。

### 自动回环

修正量 `>= 0.010 m` 时必须满足：

- 连续 `8` 个时序相邻确认（普通回环为 `4`）；
- PnP 内点至少 `25`、内点比例至少 `0.50`；
- 右 IR 几何内点至少 `30`、比例至少 `0.45`；
- PnP RMSE 不超过 `2.5 px`，P95 不超过 `5.0 px`。

不满足时输出 `AUTO_LOOP_LARGE_REJECT`，保留 VIO 位姿但不写入危险回环边。

## 验证证据

- Python 编译检查：`calibrate_robot_umi_joint.py`、`diagnose_robot_umi_segments.py`、`compare_robot_umi_precision.py` 通过；
- 针对比较/时间偏移/回环指标测试：`28 passed`；
- `vins_fusion_ros2` Release 构建：`1 package finished`，退出码 0；
- 新回环节点启动烟测打印：`large_loop_gate: threshold=0.0100 m confirmations=8 ...`；
- `git diff --check` 通过；
- 联合估计合成刚体测试恢复误差约 `0.01 mm`。

## Docker2 交付边界

当前主机源码和 `install/` 使用了新门禁；Docker2 已签发镜像仍通过固定 `.product_live_build` 和哈希清单启动旧回环二进制。为避免污染已验收产品镜像，本轮没有静默替换它。要让 Docker2 正式入口使用新门禁，需要构建独立候选镜像、更新其哈希清单并做一次完整录制回放验收。
