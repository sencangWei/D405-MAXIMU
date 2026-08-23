# STM32F070 KT-EX9 + AS5047P 联合采集固件

本目录是独立固件工程，不修改 `ego_vio` 或 ESP32 临时测试固件。目标硬件为当前 IMU 转接板上的 STM32F070F6P6、CP2102N、KT-EX9 UART 接口和 AS5047P SPI 接口。

当前状态：Mode B 协议测试、PC 解析器测试、STM32 目标编译和实板烧录均已完成；
D405 + STM32 的 3 小时相机/IMU传输压力测试通过。磁编码器角度到夹距的机械标定仍待完成，
不能视为编码器产品验收。证据边界见 `release/HARDWARE_STATUS_20260823.md`。

## 工作方式

- USART2 在 921600 8N1 下接收 KT-EX9 的 37 字节、400 Hz 帧。
- 收到候选首字节 `0xEB` 时，记录扩展后的 1 MHz TIM3 计数。
- 确认完整帧头 `EB 90 22` 后，立即以 SPI1 Mode 1、750 kHz 读取一次 AS5047P；读取期间 USART2 中断继续把 IMU 后续字节放入固定队列。
- 收完 37 字节并校验成功后，才把该编码器响应与 IMU 帧组成联合帧；坏校验帧对应的编码器响应会被丢弃，不会串到下一帧。
- USART1 通过 CP2102N 以 921600 8N1 输出固定 63 字节联合帧。
- 编码器未连接或响应无效时，IMU 帧仍继续输出；PC 端通过 `ENCODER_VALID` 和编码器错误位判断角度是否可用。

这属于同一 MCU 时间基准下的硬件侧配对，不是 IMU 和编码器由同一触发脉冲同时采样。IMU 时间戳表示 STM32 收到首字节并置位 RXNE 的时刻，不是 IMU 内部采样时刻。Mode B 将编码器读取从“完整 IMU 帧之后”提前到“确认 3 字节帧头之后”，用于缩小两者时间差。

## 引脚

| 功能 | STM32 引脚 |
|---|---|
| IMU UART TX/RX | PA2 / PA3 |
| AS5047P CS/SCK/MISO/MOSI | PA4 / PA5 / PA6 / PA7 |
| CP2102N UART TX/RX | PA9 / PA10 |
| SWDIO / SWCLK | PA13 / PA14 |

## 构建与烧录

在本目录运行：

```powershell
pio test -e native
python -m unittest discover -s tools -p "test_*.py" -v
pio run -e stm32f070f6p6
```

生成文件：

```text
.pio/build/stm32f070f6p6/firmware.elf
.pio/build/stm32f070f6p6/firmware.bin
```

首次烧录使用 ST-Link，通过 J4 按网络名连接：

| ST-Link | 转接板 J4 |
|---|---|
| GND | GND |
| SWDIO | SWDIO |
| SWCLK | SWCLK |
| NRST | NRST |
| VAPP/TVCC 电压参考 | 3V3 |

不要根据排针位置猜针序，按 J4 丝印或万用表网络确认。烧录时可让转接板由 USB 正常供电；若廉价 ST-Link 的 `3V3` 是电源输出而不是电压检测脚，不要在 USB 已供电时再用它给板子供电。

```powershell
pio run -e stm32f070f6p6 -t upload
```

Ubuntu 22.04 的环境安装、udev、烧录接线和到板验收步骤见
[`docs/UBUNTU_22_04_FLASH_AND_ACCEPTANCE.md`](docs/UBUNTU_22_04_FLASH_AND_ACCEPTANCE.md)。

## PC 接收

先安装运行期依赖：

```powershell
pip install pyserial
```

将 `COM5` 换成 CP2102N 实际端口：

```powershell
python tools/combined_capture.py --port COM5 --baud 921600 --csv capture.csv --raw capture.bin
```

程序每秒显示帧率、CRC错误、丢弃字节以及本秒内“编码器读取开始时间－IMU首字节接收时间”的范围。按 `Ctrl+C` 停止并关闭文件。

## Python 接口模块

供电脑端 APP 集成时，不需要复制命令行采集逻辑。直接使用：

- `tools/imu_encoder_protocol.py`：协议、CRC和结构化联合样本；
- `tools/imu_encoder_client.py`：CP2102N后台串口客户端；
- `tools/example_imu_encoder.py`：最小实时读取示例。

完整API、63字节协议、时间戳和异常处理说明见
[`docs/IMU_ENCODER_PYTHON_INTERFACE.md`](docs/IMU_ENCODER_PYTHON_INTERFACE.md)。

## 63 字节联合帧

所有多字节整数均为小端：

| 偏移 | 长度 | 字段 |
|---:|---:|---|
| 0 | 2 | `A5 5A` |
| 2 | 1 | 版本 `1` |
| 3 | 1 | 总长度 `63` |
| 4 | 2 | flags |
| 6 | 4 | 输出序号 |
| 10 | 4 | IMU首字节接收时间，微秒 |
| 14 | 4 | 编码器SPI读取开始时间，微秒 |
| 18 | 4 | IMU内部计数器 |
| 22 | 2 | AS5047P完整响应 |
| 24 | 37 | KT-EX9原始帧 |
| 61 | 2 | CRC-16/CCITT-FALSE |

flags：bit0 IMU有效、bit1编码器有效、bit2编码器错误位、bit3编码器奇偶校验错误、bit4 IMU计数器缺口、bit5 IMU接收队列溢出或USART接收错误、bit6 PC发送队列溢出。

## 上板验收顺序

1. 不接IMU和编码器，烧录后确认CP2102N仍能正常枚举；此时没有联合帧输出属于正常现象。
2. 只接KT-EX9，确认PC约收到400帧/秒；编码器错误标志允许存在，IMU计数器必须连续。
3. 再接AS5047P和磁钢，确认bit1置位、角度随旋转变化，bit2/bit3不持续出现。
4. 先连续记录10分钟做快速验收，再连续记录3小时做交付压力测试；确认IMU/发送队列溢出为0、CRC错误为0、计数器和序号连续，并统计时间差的最小值、均值、P95、P99和最大值。
5. 若要测物理UART边沿到MCU时间戳的绝对偏差，使用逻辑分析仪或示波器；与D405联合对齐时再连接相机。
