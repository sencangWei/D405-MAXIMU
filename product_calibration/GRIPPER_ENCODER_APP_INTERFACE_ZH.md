# UMI 次编码器采集模块与 App 接口 v1

本文档是 App 端唯一需要依赖的夹爪编码器合同。App 不解析 STM32 63 字节包、不读取
标定 YAML，也不自己计算角度或距离。

## 1. 架构与串口所有权

```text
STM32 63字节联合包（400 Hz）
          │
          ▼
现有唯一串口所有者 / StreamDecoder
          │ ImuPacket
          ▼
GripperEncoderProcessor
          │ GripperSample / JSON v1
          ├── App UI：读取最新样本 20–60 Hz
          └── 数据记录：保留全部约 400 Hz 样本
```

串口是独占资源。产品同时运行 VINS 或录制时，必须由原有串口所有者把已经解析的
`ImuPacket` 交给 `GripperEncoderProcessor`；App 不得启动
`GripperEncoderCollector` 再次打开同一设备。

`GripperEncoderCollector` 只用于 App 单独运行、标定或台架诊断。它自己打开串口，
内部使用有界队列把慢 App 回调与 400 Hz 读取线程隔离，不会无限增长内存。

## 2. 稳定接口文件

- Python 模块：`product_calibration/gripper_encoder.py`
- 样本 JSON Schema：`product_calibration/gripper_encoder_sample_v1.schema.json`
- 健康 JSON Schema：`product_calibration/gripper_encoder_health_v1.schema.json`
- 标定配置：`product_calibration/umi_manual_gripper_20260824.yaml`
- 标定 ID：`UMI_MANUAL_GRIPPER_20260824_V1`

`schema=umi_gripper_sample_v1` 的字段名、单位和语义冻结。任何删除、重命名、单位变化或
状态码变化都必须发布 `v2`；App 必须拒绝未知主版本，不能猜字段含义。

## 3. 样本字段

| 字段 | 类型/单位 | App 含义 |
|---|---|---|
| `schema` | string | 固定 `umi_gripper_sample_v1` |
| `calibration_id` | string | 当前距离标定身份，必须随数据保存 |
| `protocol` | string | 固定 `stm32_combined_v1` |
| `sequence` | uint32 | STM32 联合包连续序号，用于检测传输缺口 |
| `imu_counter` | uint32 | 同一包内 IMU 计数，便于与 VINS/录制对齐 |
| `device_time_us` | uint64/µs | 已跨 32 位回绕展开的 MCU 编码器读取时刻；仅本次进程内连续 |
| `sensor_pair_delta_us` | int/µs | 编码器读取时刻减 IMU 首字节时刻，当前链正常约 65 µs |
| `host_monotonic_ns` | uint64/ns | PC 收到并处理样本的单调时钟，不是 Unix 时间 |
| `raw_flags` | uint16 | STM32 原始诊断标志，App 正常不自行解析 |
| `raw_count` | uint14 | AS5047P 原始绝对角计数，范围 0–16383 |
| `angle_deg` | float/° | 原始绝对角，范围 `[0,360)`，权威状态量 |
| `direction` | enum | `closing`、`opening` 或启动/静止时的 `unknown` |
| `closure_ratio` | float/null | 0=完全张开，1=完全闭合；App 主显示量 |
| `estimated_no_load_gap_mm` | float/null/mm | 空载软垫内侧间距估计 |
| `no_load_uncertainty_mm` | float/null/mm | 当前为 1.5 mm，必须和间距一起显示 |
| `dual_closing_distance_mm` | float/null/mm | 两个夹爪合计闭合行程：66.90 mm 减空载间距 |
| `single_jaw_travel_mm` | float/null/mm | 单边夹爪行程，等于双边的一半 |
| `loaded_object_size_valid` | bool | v1 固定 `false` |
| `valid` | bool | 本帧编码器标志、错误位和奇偶校验综合有效性 |
| `status` | enum | `OK`、`DIRECTION_UNKNOWN`、`ENCODER_INVALID` |

当 `valid=false` 时，距离和闭合比例字段必须为 `null`。App 不得沿用上一帧数值冒充
当前有效值；可以把最后有效值留作灰色历史参考，但必须同时显示“编码器不可用”。

有效样本示例：

```json
{
  "schema": "umi_gripper_sample_v1",
  "calibration_id": "UMI_MANUAL_GRIPPER_20260824_V1",
  "protocol": "stm32_combined_v1",
  "sequence": 123,
  "imu_counter": 456,
  "device_time_us": 1002565,
  "sensor_pair_delta_us": 65,
  "host_monotonic_ns": 556680380000,
  "raw_flags": 3,
  "raw_count": 2200,
  "angle_deg": 48.33984375,
  "direction": "closing",
  "closure_ratio": 0.4997637394,
  "estimated_no_load_gap_mm": 33.46580583,
  "no_load_uncertainty_mm": 1.5,
  "dual_closing_distance_mm": 33.43419417,
  "single_jaw_travel_mm": 16.71709708,
  "loaded_object_size_valid": false,
  "valid": true,
  "status": "OK"
}
```

## 4. App 显示规则

1. 主显示使用 `closure_ratio×100%`；原始角度放到诊断页。
2. 间距标签必须写成“空载间距估计 ±1.5 mm”，不能写“器械直径/物体尺寸”。
3. `direction=unknown` 常见于刚启动或静止，数据仍有效，不显示硬件故障。
4. `valid=false`、`status=ENCODER_INVALID` 或最新样本年龄超过 100 ms 时，主状态显示
   不可用并停止更新有效数字。
5. UI 只需以 20–60 Hz 读取 `latest()`；不要用 400 Hz 重绘。需要完整数据集时通过
   回调或 JSONL 记录全部样本。
6. App 每秒读取一次 `health()`。任何 `FAULT`、`serial_errors>0`、
   `device_queue_overflow_flags>0`、`device_time_regressions>0` 或持续
   `sequence_gaps>0` 都应显示链路告警；
   `callback_queue_drops>0` 表示 App 消费过慢，不代表传感器本身丢帧。

## 5. 产品进程内接入（推荐）

现有 VINS/录制进程已经从 `StreamDecoder` 得到 `ImuPacket` 时：

```python
from product_calibration.gripper_encoder import GripperEncoderProcessor

encoder = GripperEncoderProcessor.from_profile(
    "/home/robot/ego_vio_calib_kit/product_calibration/umi_manual_gripper_20260824.yaml"
)

def on_stm32_packet(packet):
    gripper_sample = encoder.process(packet)
    app_publish(gripper_sample.to_dict())  # 后续接本地socket/WebSocket/ROS适配器
```

这里的 `app_publish` 是后续 App 选定传输后的薄适配层。无论最终选择什么 IPC，发送的
JSON 内容必须符合 `gripper_encoder_sample_v1.schema.json`。

## 6. App 单独运行/台架接入

只有确认 VINS 和其他采集程序没有占用 STM32 串口时才能使用：

```python
from product_calibration.gripper_encoder import GripperEncoderCollector

collector = GripperEncoderCollector(
    port="/dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_...-port0"
)
collector.start()
try:
    sample = collector.wait_for_sample(timeout=1.0)
    if sample is not None:
        app_render(sample.to_dict())
    health = collector.health().to_dict()
finally:
    collector.stop()
```

也可以轮询 `collector.latest()`，它始终返回最近的不可变样本，不阻塞读取线程。

## 7. 全速 JSONL 记录

```python
from pathlib import Path
from product_calibration.gripper_encoder import GripperEncoderCollector, JsonlSampleRecorder

with JsonlSampleRecorder(Path("gripper_samples.jsonl")) as recorder:
    with GripperEncoderCollector(on_sample=recorder.write) as collector:
        run_until_capture_finishes()
    print(collector.health().to_dict())
```

每行都是一个完整 `umi_gripper_sample_v1` JSON 对象。正式数据集必须同时保存标定 YAML
及其 SHA-256、健康快照、软件 Git 提交和 STM32 固件哈希。

现有终端工具也支持保存：

```bash
cd /home/robot/ego_vio_calib_kit
./umi_gripper_live.sh --jsonl /绝对路径/gripper_samples.jsonl
```

## 8. 当前验收边界

- 完全张开空载间距：66.90 mm。
- 两个夹爪近似对称移动，单边行程为双边闭合距离的一半。
- 空载距离盲测：最大误差 1.195 mm、平均误差 0.829 mm；接口按 ±1.5 mm 标记。
- 原始角度和闭合比例可用于手动 UMI 状态记录。
- 夹持器械后软垫压缩取决于手力；没有力传感器/压缩模型时，
  `loaded_object_size_valid` 永远为 `false`。
