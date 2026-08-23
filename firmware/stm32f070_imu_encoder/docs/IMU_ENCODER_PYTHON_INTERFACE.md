# IMU＋磁编码器 Python 接口文档

本文档供电脑端 APP 开发者对接 STM32F070 联合采集板。板上 MCU 已经把
KT-EX9 IMU 帧与确认帧头后立即读取的 AS5047P 角度绑定为同一个数据包；电脑端不要再按两个
串口的到达时间重新配对。

## 1. 交付文件

把下面两个文件复制到 APP 工程的同一 Python 包内即可：

```text
tools/imu_encoder_protocol.py  # 二进制协议、CRC、结构化数据
tools/imu_encoder_client.py    # CP2102N串口、后台线程、接收队列
```

参考程序：

```text
tools/example_imu_encoder.py   # 实时读取并以10Hz打印
tools/combined_capture.py      # 保存全部400Hz数据到CSV和原始bin
```

要求 Python 3.10 或更高版本。仅串口客户端依赖 pyserial：

```powershell
pip install pyserial
```

## 2. 最小调用方法

插入采集板 USB-C，在 Windows 设备管理器中找到 CP2102N 对应的 COM 口，然后运行：

```powershell
python tools/example_imu_encoder.py --port COM7
```

在 APP 代码中使用：

```python
from imu_encoder_client import ImuEncoderClient, ImuEncoderClientError

try:
    with ImuEncoderClient("COM7") as client:
        while app_is_collecting:
            sample = client.read(timeout=0.1)
            if sample is None:
                continue

            save_imu(
                sample.pc_unix_ns,
                sample.imu_first_byte_rx_us,
                sample.imu_counter,
                sample.imu.gx,
                sample.imu.gy,
                sample.imu.gz,
                sample.imu.ax,
                sample.imu.ay,
                sample.imu.az,
                sample.imu.temperature_c,
            )

            if sample.encoder_valid:
                show_angle(sample.encoder_angle_deg)
            else:
                show_encoder_error(int(sample.flags))
except ImuEncoderClientError as exc:
    show_device_error(str(exc))
```

`read()` 是队列读取接口。正式录制必须在 APP 工作线程中连续调用，保存全部样本；
不要让 10 Hz GUI 定时器每次只读取一帧，否则队列会不断积压。界面只需显示实时值
时使用 `latest()`。如果必须由定时器消费队列，每次触发应循环调用
`read(timeout=0)` 直到返回 `None`，录制全部取出的帧，并只把最后一帧送去刷新界面。
后台串口线程始终接收完整400 Hz数据，与界面刷新频率无关。

## 3. 客户端 API

### `ImuEncoderClient`

```python
ImuEncoderClient(
    port: str,
    baudrate: int = 921600,
    queue_size: int = 2048,
)
```

| 成员 | 含义 |
|---|---|
| `start()` | 打开串口并启动后台接收线程；关闭后再次调用会开始一个清空旧队列和统计的新会话；上下文管理器会自动调用 |
| `read(timeout=None)` | 返回下一条 `CombinedSample`；超时返回 `None`；接收线程故障时抛出 `ImuEncoderClientError` |
| `latest()` | 返回最近解析成功的样本；尚未收到时返回 `None` |
| `stats` | 返回不可变的 `ClientStats` 快照 |
| `close()` | 关闭串口并停止线程；可重复调用 |

串口固定为 8 数据位、无校验、1 停止位、无软硬件流控。一个物理采集板创建一个
客户端实例。

`ClientStats` 字段：

| 字段 | 含义 |
|---|---|
| `frames_received` | PC 端成功完成 CRC 校验的联合帧数 |
| `consumer_queue_drops` | APP 消费过慢导致本地队列丢弃的旧样本数 |
| `crc_errors` | 联合帧 CRC 错误次数 |
| `discarded_bytes` | 为重新寻找帧头而丢弃的字节数 |
| `last_error` | 最近的串口接收异常文本；正常为 `None` |

`queue_size` 必须大于0。默认队列2048帧，约对应5.1秒的400 Hz数据。队列满时丢
最旧数据并保留最新数据，串口线程不会等待 GUI。正式录制时应持续读取并监控
`consumer_queue_drops == 0`。如果只是用 `latest()` 做实时预览、不消费队列，该统计
增长属于预览数据未被消费；开始正式录制前应关闭并重新 `start()`，或先排空旧队列。

## 4. `CombinedSample` 字段

| 字段 | 类型/单位 | 含义 |
|---|---|---|
| `pc_unix_ns` | `int`，Unix ns | PC 解析出该帧时的系统时间 |
| `sequence` | `uint32` | STM32 联合输出序号 |
| `flags` | `PacketFlag` | 有效性和异常状态位 |
| `imu_first_byte_rx_us` | `uint32`，µs | STM32 捕获 IMU UART 首字节的定时器值 |
| `encoder_read_us` | `uint32`，µs | STM32 开始读取编码器的定时器值 |
| `sensor_gap_us` | `int`，µs | 上述两时刻的无符号差，已处理单次回绕 |
| `imu_counter` | `uint32` | KT-EX9 帧内计数器 |
| `imu.gx/gy/gz` | `float`，°/s | KT-EX9 原始角速度 |
| `imu.ax/ay/az` | `float`，g | KT-EX9 原始加速度 |
| `imu.temperature_c` | `float`，℃ | KT-EX9 温度 |
| `encoder_response` | `uint16` | AS5047P 完整 SPI 响应，含奇偶校验和错误位 |
| `encoder_raw` | `0..16383` | AS5047P 14 位原始角度 |
| `encoder_angle_deg` | `float`，° | `encoder_raw × 360 / 16384`，范围 `[0,360)` |
| `imu_frame` | 37字节 | 原始 KT-EX9 帧 |
| `raw_packet` | 63字节 | 原始联合帧 |

便利布尔字段：`imu_valid`、`encoder_valid`、`encoder_error`、
`encoder_parity_error`、`imu_counter_gap`、`imu_queue_overflow` 和
`pc_tx_queue_overflow`。

注意：`pc_unix_ns` 是 Python 完成该帧解析时取得的系统时间，每个解析出的帧各自打
点，但仍包含 USB、串口驱动、批量缓存和线程调度延迟。同一批数据的这些时间可能
非常接近，并不代表传感器真实采样间隔。同步和帧间隔分析优先使用 MCU 微秒时间；
Unix 时间只用于和相机或其他电脑端设备建立同一时间域，并应通过一段数据拟合 MCU
时钟到 PC 时钟的映射，不能把它当作传感器采样时刻。

## 5. 63 字节线协议（版本 1）

所有多字节整数和 IEEE-754 `float32` 均为小端。

| 偏移 | 长度 | 类型 | 字段 |
|---:|---:|---|---|
| 0 | 2 | bytes | 帧头 `A5 5A` |
| 2 | 1 | `uint8` | 协议版本 `1` |
| 3 | 1 | `uint8` | 总长度 `63` (`0x3F`) |
| 4 | 2 | `uint16` | flags |
| 6 | 4 | `uint32` | STM32输出序号 |
| 10 | 4 | `uint32` | IMU首字节接收时间，µs |
| 14 | 4 | `uint32` | 编码器SPI读取开始时间，µs |
| 18 | 4 | `uint32` | IMU内部计数器 |
| 22 | 2 | `uint16` | AS5047P完整响应 |
| 24 | 37 | bytes | KT-EX9原始帧 |
| 61 | 2 | `uint16` | CRC-16/CCITT-FALSE |

CRC 参数：多项式 `0x1021`、初值 `0xFFFF`、不反射、无最终异或；计算范围为
字节 0 到 60，接收 CRC 位于 61–62，按小端存放。

### flags

| 位 | Python枚举 | 含义 |
|---:|---|---|
| 0 | `IMU_VALID` | KT-EX9帧头、长度和校验有效 |
| 1 | `ENCODER_VALID` | AS5047P响应偶校验正确且错误位未置位 |
| 2 | `ENCODER_ERROR` | AS5047P响应错误位为1 |
| 3 | `ENCODER_PARITY_ERROR` | AS5047P响应不满足偶校验 |
| 4 | `IMU_COUNTER_GAP` | 当前IMU计数器相对上一帧不连续 |
| 5 | `IMU_QUEUE_OVERFLOW` | STM32内部IMU接收队列曾溢出或USART发生接收错误 |
| 6 | `PC_TX_QUEUE_OVERFLOW` | STM32到CP2102N发送队列曾溢出 |

编码器无效时，联合帧和 IMU 数据仍然返回。APP 必须先判断 `encoder_valid`，再显示
或使用角度；不要把无效响应掩码后的数值当成真实角度。

### 内嵌 KT-EX9 37 字节帧

| 联合帧偏移 | IMU帧内偏移 | 长度 | 字段 |
|---:|---:|---:|---|
| 24 | 0 | 3 | `EB 90 22` |
| 27 | 3 | 1 | IMU状态/类型原始字节 |
| 28 | 4 | 4 | `gx float32` |
| 32 | 8 | 4 | `gy float32` |
| 36 | 12 | 4 | `gz float32` |
| 40 | 16 | 4 | `ax float32` |
| 44 | 20 | 4 | `ay float32` |
| 48 | 24 | 4 | `az float32` |
| 52 | 28 | 4 | `temperature float32` |
| 56 | 32 | 4 | `counter uint32` |
| 60 | 36 | 1 | 低8位累加校验 |

## 6. 时间戳和同步说明

本板实现的是“同一 MCU 时间基准下的硬件侧配对”：

1. STM32在USART接收候选首字节 `0xEB` 时锁存1 MHz定时器。
2. 收到并确认完整帧头 `EB 90 22` 后，STM32立即记录编码器读取时间并通过SPI读取AS5047P；USART中断同时缓存后续IMU字节。
3. 收完37字节且IMU校验通过后，两个时间值、IMU计数器、IMU数据和编码器响应才写入同一个63字节包；坏IMU帧对应的编码器响应直接丢弃。

因此每个角度都明确绑定一帧 IMU，优于 PC 按串口到达时间配对。它不是两个传感器
由同一触发脉冲在同一物理时刻采样；`sensor_gap_us` 表示首字节RX时间到编码器SPI读取开始时间的偏移，主要包含后两个帧头字节的串口时间、主循环调度和少量软件处理。

MCU时间字段是 `uint32` 微秒，约每 `4294.967296` 秒（71.58分钟）回绕。长时间记录
可对每条时间轴使用 `TimerUnwrapper.extend()` 扩展为单调的64位微秒值。

## 7. 保存建议

至少保存：

```text
pc_unix_ns, imu_first_byte_rx_us, encoder_read_us, sensor_gap_us,
sequence, imu_counter, flags,
gx, gy, gz, ax, ay, az, temperature_c,
encoder_response, encoder_raw, encoder_angle_deg
```

需要直接生成参考 CSV 和原始包时：

```powershell
python tools/combined_capture.py --port COM7 --csv capture.csv --raw capture.bin
```

`capture.bin` 可用于以后用新版解析器重放，建议正式实验同时保存。陀螺仪传给 ROS、
OpenVINS 等使用 SI 单位的系统前，需要把 °/s 转为 rad/s；加速度从 g 转为 m/s²
时乘以所采用的重力常数。

## 8. APP端异常处理规则

- `read()` 超时返回 `None`：表示该时间段没有完整有效帧，不代表进程必须退出。
- `ImuEncoderClientError`：串口打开失败、USB拔出或后台读取失败，应提示用户重新连接。
- `crc_errors` 增长：检查USB线、供电、波特率和板上串口信号。
- `consumer_queue_drops` 增长：APP处理或磁盘写入太慢，应把保存移到工作线程。
- `imu_counter_gap`：记录该帧并标记缺口，不要用相邻到达时间假装补帧。
- 编码器相关错误：继续保存IMU，角度字段标记无效。

## 9. 联调验收

软件接口自动测试不需要硬件：

```powershell
python -m unittest discover -s tools -p "test_*.py" -v
```

接上实物后至少验证：

1. CP2102N稳定枚举，串口参数为921600 8N1。
2. 连续接收速率约400 Hz，`crc_errors == 0`。
3. `sequence`和`imu_counter`连续，两个队列溢出标志不出现。
4. 转动磁钢时角度在0–360°内连续变化，编码器有效标志保持为1。
5. 先连续录制10分钟，再连续录制3小时；`consumer_queue_drops == 0`，并保存最终统计和原始数据哈希。

没有连接实物板之前，只能确认协议解析、队列和错误处理逻辑，不能宣称完成硬件验收。
