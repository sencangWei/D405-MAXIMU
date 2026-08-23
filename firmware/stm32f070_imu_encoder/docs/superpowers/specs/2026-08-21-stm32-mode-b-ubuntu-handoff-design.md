# STM32F070 模式B移植与 Ubuntu 22.04 烧录交接设计

日期：2026-08-21

## 目标

把 ESP32-S3 上已经连续两次通过3小时零缺口测试的模式B移植到正式
STM32F070F6P6联合采集板，并准备一份可复制到 Ubuntu 22.04 电脑直接编译、
使用ST-Link首次烧录和进行台架验收的交接包。

模式B的定义：收到KT-EX9候选帧的三个确认帧头字节`EB 90 22`后，立即读取
AS5047P；完整37字节IMU帧校验成功后，才把该编码器样本与IMU组成63字节联合包。

## 不在本次范围内

- 不修改PCB、引脚分配和63字节PC协议；
- 不修改D405/VINS程序或电脑APP；
- 不做编码器角度到夹爪直线距离的机械标定；
- 不把ESP32压测结果当作STM32正式板硬件验收结果。

## 已锁定硬件与接口

- MCU：STM32F070F6P6，内部8 MHz HSI经PLL得到48 MHz，无外部晶振；
- KT-EX9：USART2，PA2/PA3，921600 baud，8N1，37字节，约400 Hz；
- AS5047P：SPI1，PA4=CS、PA5=SCK、PA6=MISO、PA7=MOSI，Mode 1，750 kHz；
- PC链路：USART1，PA9/PA10，经CP2102N输出921600 baud；
- 烧录：J4的3V3参考、SWDIO、SWCLK、NRST、GND连接ST-Link；
- 时间基准：TIM3生成1 MHz扩展32位微秒计时，约71.58分钟回绕一次。

## 选择的接收架构

采用“USART2接收中断＋固定字节环形队列＋主循环解析和SPI读取”。

### USART2中断职责

每次RXNE中断只执行：

1. 锁存当前`micros32()`；
2. 读取USART2 RDR；
3. 将`{byte, rx_us}`写入固定容量SPSC环形队列；
4. 若队列已满，增加溢出计数并置位sticky错误标志；
5. 清理ORE/FE/NE/PE硬件错误标志。

中断内禁止执行SPI、CRC、联合包编码、动态内存分配或USB/USART1发送。

环形队列容量固定为64个字节事件。按921600 baud计算约能缓存4.3 ms输入，远大于
一次约47 µs的AS5047P双传输时间；同时只消耗约512字节RAM，适合6 KB RAM的F070F6。

### 主循环职责

主循环持续从环形队列弹出字节并送入KT-EX9流解析器：

- `HeaderConfirmed`：在主循环中记录`encoder_read_us`并立即读取AS5047P，将结果保存
  为当前候选帧的pending编码器样本；
- `BadChecksum`：丢弃pending编码器样本，绝不把它配给下一帧；
- `FrameReady`：消费pending编码器样本，生成联合样本并放入现有输出队列；
- 有效IMU帧但pending编码器不存在/无效：仍输出IMU，编码器有效位保持0；
- USART1仍使用现有TXE中断和固定发送队列，PC不读取时不得阻塞采集路径。

SPI在主循环中执行，USART2 RX中断优先级高于USART1 TX中断，因此SPI等待期间剩余
IMU字节仍能进入接收环形队列。禁止在USART2中断内直接读取编码器，否则约47 µs的
阻塞会跨越多个UART字节并导致overrun风险。

## 数据关联与错误恢复

系统同一时间最多只有一个已确认但尚未校验完成的IMU候选帧和一个pending编码器
样本。pending样本只能被消费一次。

- 只有完整IMU校验成功才能增加联合包sequence；
- 校验失败必须清空pending样本；
- 新帧不得复用上一坏帧的角度；
- 编码器EF或奇偶校验错误不抑制有效IMU输出；
- 接收环形队列溢出沿用`IMU_QUEUE_OVERFLOW` sticky标志，在后续有效联合包中报告；
- 输出队列溢出继续使用`PC_TX_QUEUE_OVERFLOW`；
- IMU counter不连续继续使用`IMU_COUNTER_GAP`；
- 所有队列、pending状态和序号均使用静态存储，不使用堆。

## PC协议兼容性

输出继续使用版本1、固定63字节二进制联合包：

- 帧头`A5 5A`；
- `imu_first_byte_rx_us`仍是候选`0xEB`进入USART2中断的时间；
- `encoder_read_us`改为确认第三个帧头字节后、拉低编码器CS之前的时间；
- flags、sequence、IMU counter、AS5047P响应、37字节原始IMU帧和CRC偏移全部不变。

现有Python解析器、CSV记录器和APP接口不需要因模式B改变字段或字节偏移。

## 测试驱动要求

实现前先增加并看到以下测试按预期失败：

1. 流解析器在第三个正确帧头字节返回`HeaderConfirmed`；
2. pending编码器样本只能消费一次；
3. 坏校验帧清除pending样本，下一帧不能拿到旧角度；
4. 有效帧使用帧头阶段保存的编码器时间和响应，而不是完整帧后的时间；
5. 没有有效编码器样本时仍输出有效IMU；
6. 接收字节环形队列满时置位sticky溢出，采集路径不阻塞；
7. 现有counter缺口、输出队列溢出、编码器错误和63字节协议测试继续通过。

完成最小实现后运行：

```bash
pio test -e native
python3 -m unittest discover -s tools -p 'test_*.py' -v
pio run -e stm32f070f6p6
```

必须记录测试数量、Flash/RAM占用、`firmware.bin`大小和SHA-256。离线编译通过不等于
实物板通过。

## Ubuntu 22.04交接包

交接包同时保存在仓库和Windows桌面压缩包中，包含：

- `firmware/stm32f070_imu_encoder/`完整源代码；
- 已验证构建生成的`firmware.bin`和SHA-256文件；
- 63字节协议及Python接口文档；
- Ubuntu 22.04环境安装、PlatformIO构建、ST-Link烧录、串口权限说明；
- J4接线表和禁止双路3V3供电的警告；
- 首次上板检查与实物验收命令；
- 当前D405/VINS电脑需要后续改为解析63字节联合包的明确说明。

## 明日实物板验收顺序

1. 断电测3V3-GND和5V-GND无短路；
2. 只接USB给板供电，测5V、3V3和MCU/CP2102N供电；
3. ST-Link只接信号与电压参考，禁止在USB供电时再用ST-Link 3V3输出供电；
4. 识别MCU并烧录，读回/校验成功；
5. 不接传感器，确认CP2102N稳定枚举；
6. 只接KT-EX9，确认约400 Hz、IMU有效、计数连续，编码器无效不影响IMU；
7. 再接AS5047P，确认角度有效并随磁钢转动；
8. 先做10分钟筛选，再做至少3小时模式B耐久并跨过计时器回绕；
9. 通过后才接入D405/VINS电脑进行联合时间轴验证。

STM32正式板PASS门槛：平均399–401 Hz；sequence和IMU counter缺失为0；CRC、丢弃
字节、读取超时、编码器错误、所有队列溢出和时间戳回退均为0；长测至少跨过一次
32位微秒计时器回绕。
