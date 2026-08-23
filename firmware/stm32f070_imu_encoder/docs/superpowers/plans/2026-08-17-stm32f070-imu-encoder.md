# STM32F070 IMU + AS5047P 联合采集 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为现有 STM32F070F6P6 转接板实现 400 Hz KT-EX9 UART 首字节计时、AS5047P SPI 配对读取、二进制联合输出和 PC 记录工具。

**Architecture:** 纯 C++17 协议层负责 IMU 流解析、AS5047P 校验及 63 字节联合帧，既能在 Windows 原生测试，也能供 STM32 固件复用。STM32Cube HAL 初始化外设，USART2 RXNE ISR 只做收字节和入队，主循环完成 SPI 读取与 USART1 非阻塞输出；Python 工具负责 PC 端重同步、记录和统计。

**Tech Stack:** PlatformIO 6.1、STM32CubeF0 CMSIS/启动文件、GNU Arm Embedded、C++17、Unity native tests、Python 3 `unittest`、pyserial（仅实际串口运行需要）

## Global Constraints

- 目标 MCU 固定为 STM32F070F6P6：48 MHz、32 KB Flash、6 KB SRAM。
- 不修改原理图、PCB、现有 VIO 主程序或 `firmware/esp32_s3_imu_encoder/`。
- IMU UART 固定为 USART2 921600 8N1；PC UART 固定为 USART1 921600 8N1。
- AS5047P 固定为 SPI1 Mode 1、MSB first、750 kHz（48 MHz / 64）。
- 不使用动态内存、JSON、运行时浮点格式化或阻塞式 PC 串口发送。
- 编码器异常不能中止 IMU 数据输出。
- 首次烧录使用 ST-Link/SWD，不在本计划内实现 USB bootloader。

---

## 文件结构

```text
firmware/stm32f070_imu_encoder/
  boards/stm32f070f6p6.json       PlatformIO 自定义目标板
  include/kt_ex9_protocol.h      KT-EX9 固定帧解析
  include/as5047p_protocol.h     AS5047P 命令和响应校验
  include/combined_packet.h      63 字节联合协议和 CRC
  include/capture_pipeline.h     与硬件无关的帧配对状态
  include/stm32f0xx_hal_conf.h   Cube HAL 模块选择
  src/main.cpp                   时钟、GPIO、UART、SPI、TIM3 扩展计时与主循环
  src/stm32f0xx_hal_msp.c        HAL 底层时钟和引脚初始化
  src/stm32f0xx_it.c             异常与 USART2 ISR 转发
  test/test_protocol/test_main.cpp
  test/test_pipeline/test_main.cpp
  tools/combined_capture.py      PC 串口解析、CSV、统计
  tools/test_combined_capture.py PC 解析器测试
  platformio.ini                 native 与 STM32 构建环境
  README.md                      烧录、运行和上板验收说明
```

### Task 1: 建立协议测试和可复现构建入口

**Files:**
- Create: `firmware/stm32f070_imu_encoder/platformio.ini`
- Create: `firmware/stm32f070_imu_encoder/boards/stm32f070f6p6.json`
- Create: `firmware/stm32f070_imu_encoder/test/test_protocol/test_main.cpp`
- Create: `firmware/stm32f070_imu_encoder/include/kt_ex9_protocol.h`
- Create: `firmware/stm32f070_imu_encoder/include/as5047p_protocol.h`
- Create: `firmware/stm32f070_imu_encoder/include/combined_packet.h`

**Interfaces:**
- Produces: `kt_ex9::StreamParser::feed(uint8_t, uint32_t)`，成功时返回含原始 37 字节、计数器和首字节时间戳的 `Frame`。
- Produces: `as5047p::makeReadCommand()`、`as5047p::makeNopCommand()`、`as5047p::isValidResponse()`。
- Produces: `combined::encode(const Sample&) -> std::array<uint8_t, 63>` 和 `combined::crc16CcittFalse()`。

- [ ] **Step 1: 写协议层失败测试**

  测试必须覆盖 KT-EX9 正常帧、坏校验、噪声重同步、候选 `0xEB` 时间戳保留、AS5047P 命令偶校验、响应错误位，以及联合帧的精确偏移和 CRC。关键断言为：

  ```cpp
  TEST_ASSERT_EQUAL_UINT32(123456U, parsed.first_byte_rx_us);
  TEST_ASSERT_EQUAL_UINT8(0xA5, packet[0]);
  TEST_ASSERT_EQUAL_UINT8(0x5A, packet[1]);
  TEST_ASSERT_EQUAL_UINT8(63, packet[3]);
  TEST_ASSERT_EQUAL_UINT16(
      combined::crc16CcittFalse(packet.data(), 61),
      combined::readLe16(packet.data() + 61));
  ```

- [ ] **Step 2: 运行测试并确认按预期失败**

  Run: `pio test -e native -f test_protocol`

  Expected: FAIL，原因是三个协议头文件或目标接口尚未实现，而不是测试框架损坏。

- [ ] **Step 3: 实现最小协议层**

  使用固定长度 `std::array` 和显式小端写入。联合帧常量必须固定：

  ```cpp
  constexpr size_t kPacketSize = 63;
  constexpr uint8_t kSync0 = 0xA5;
  constexpr uint8_t kSync1 = 0x5A;
  constexpr uint8_t kVersion = 1;
  constexpr uint16_t kCrcInitial = 0xFFFF;
  constexpr uint16_t kCrcPolynomial = 0x1021;
  ```

  CRC 覆盖字节 0..60；AS5047P 第二次传输命令使用有效偶校验的 NOP；IMU 流解析不得使用 `memmove` 或动态分配。

- [ ] **Step 4: 运行协议测试并确认通过**

  Run: `pio test -e native -f test_protocol`

  Expected: PASS，0 failures。

- [ ] **Step 5: 提交协议层**

  ```powershell
  git add firmware/stm32f070_imu_encoder
  git commit -m "feat: add STM32 combined sensor protocol"
  ```

### Task 2: 实现并测试采集配对状态机

**Files:**
- Create: `firmware/stm32f070_imu_encoder/include/capture_pipeline.h`
- Create: `firmware/stm32f070_imu_encoder/test/test_pipeline/test_main.cpp`

**Interfaces:**
- Consumes: `kt_ex9::Frame`、`combined::Sample`。
- Produces: `capture::Pipeline::onImuByte(uint8_t, uint32_t)`、`popPendingImu()`、`completeEncoderRead(uint16_t, uint32_t)`、`popOutput()`。

- [ ] **Step 1: 写状态机失败测试**

  覆盖有效 IMU 帧入队、编码器有效/错误响应、IMU 计数器缺口、编码器缺失仍输出、RX 队列溢出和 TX 队列溢出。关键行为：

  ```cpp
  TEST_ASSERT_TRUE(pipeline.popPendingImu(pending));
  pipeline.completeEncoderRead(0xFFFF, 123500U);
  TEST_ASSERT_TRUE(pipeline.popOutput(output));
  TEST_ASSERT_BITS_HIGH(combined::kEncoderError, output.flags);
  TEST_ASSERT_BITS_LOW(combined::kEncoderValid, output.flags);
  ```

- [ ] **Step 2: 运行并确认测试失败**

  Run: `pio test -e native -f test_pipeline`

  Expected: FAIL，原因是 `capture_pipeline.h` 尚未实现。

- [ ] **Step 3: 实现固定容量状态机**

  使用容量 4 的 IMU 队列和容量 4 的输出队列。中断入口只调用 `onImuByte()`；主循环先 `popPendingImu()`，完成一次 SPI 读取后调用 `completeEncoderRead()`。溢出采用计数和粘滞标志，不阻塞 ISR。

- [ ] **Step 4: 运行全部 native 测试**

  Run: `pio test -e native`

  Expected: `test_protocol` 和 `test_pipeline` 全部 PASS。

- [ ] **Step 5: 提交状态机**

  ```powershell
  git add firmware/stm32f070_imu_encoder/include/capture_pipeline.h firmware/stm32f070_imu_encoder/test/test_pipeline
  git commit -m "feat: add deterministic sensor capture pipeline"
  ```

### Task 3: 实现 PC 二进制解析、CSV 和统计

**Files:**
- Create: `firmware/stm32f070_imu_encoder/tools/combined_capture.py`
- Create: `firmware/stm32f070_imu_encoder/tools/test_combined_capture.py`

**Interfaces:**
- Consumes: 任意分块的串口字节流。
- Produces: `PacketParser.feed(data: bytes) -> list[CombinedPacket]`、`TimerUnwrapper.extend(value: int) -> int`、命令行 `--port --baud --csv --raw`。

- [ ] **Step 1: 写 Python 失败测试**

  使用标准库 `unittest`，覆盖分包、粘包、前置噪声、CRC 损坏、32 位计时器回卷和 `encoder_read_us - imu_first_byte_rx_us` 跨回卷差值。

  ```python
  parser = PacketParser()
  self.assertEqual([], parser.feed(b"noise" + packet[:17]))
  decoded = parser.feed(packet[17:] + packet)
  self.assertEqual(2, len(decoded))
  self.assertEqual(0x1234, decoded[0].encoder_raw)
  ```

- [ ] **Step 2: 运行并确认测试失败**

  Run: `python -m unittest tools.test_combined_capture -v`

  Expected: FAIL，原因是解析器接口尚未实现。

- [ ] **Step 3: 实现最小 PC 工具**

  解析器以同步头、固定长度、版本和 CRC 四重条件重同步。导入 `serial` 必须放在实际打开串口的路径内，使单元测试无需安装 pyserial。CSV 每行写 PC `time.time_ns()`、展开后的两个 MCU 时间、IMU 计数器、7 个 float、编码器响应/角度和 flags；原始文件逐帧写完整 63 字节。

- [ ] **Step 4: 运行 Python 测试**

  Run: `python -m unittest tools.test_combined_capture -v`

  Expected: PASS。

- [ ] **Step 5: 提交 PC 工具**

  ```powershell
  git add firmware/stm32f070_imu_encoder/tools
  git commit -m "feat: add combined sensor capture tool"
  ```

### Task 4: 建立 STM32F070 目标并实现外设驱动

**Files:**
- Modify: `firmware/stm32f070_imu_encoder/platformio.ini`
- Modify: `firmware/stm32f070_imu_encoder/boards/stm32f070f6p6.json`
- Create: `firmware/stm32f070_imu_encoder/include/stm32f0xx_hal_conf.h`
- Create: `firmware/stm32f070_imu_encoder/src/main.cpp`
- Create: `firmware/stm32f070_imu_encoder/src/stm32f0xx_hal_msp.c`
- Create: `firmware/stm32f070_imu_encoder/src/stm32f0xx_it.c`

**Interfaces:**
- Consumes: Task 1/2 的协议层和状态机。
- Produces: 可由 ST-Link 烧录的 `.pio/build/stm32f070f6p6/firmware.elf` 与 `firmware.bin`。

- [ ] **Step 1: 添加最小目标并确认链接失败**

  `platformio.ini` 中 STM32 环境固定为：

  ```ini
  [env:stm32f070f6p6]
  platform = ststm32@19.7.1
  board = stm32f070f6p6
  framework = stm32cube
  upload_protocol = stlink
  build_flags = -std=gnu++17 -Os -ffunction-sections -fdata-sections
  ```

  Run: `pio run -e stm32f070f6p6`

  Expected: FAIL，原因是 `main`/系统初始化文件尚未实现，不允许是错误 MCU 或链接脚本容量错误。

- [ ] **Step 2: 实现时钟与固定引脚外设初始化**

  初始化内部 8 MHz HSI 经 PLL ×12 为 48 MHz；TIM3 为 1 MHz 16 位自由计数，并由溢出中断扩展为 32 位；USART2 PA2/PA3 和 USART1 PA9/PA10 均为 921600 8N1；SPI1 PA5/PA6/PA7 为 Mode 1、750 kHz；PA4 为高电平空闲 CS；PA6 配置弱上拉；保留 PA13/PA14 SWD。

- [ ] **Step 3: 实现最短 USART2 ISR 与主循环**

  `USART2_IRQHandler` 必须读取扩展后的 TIM3 微秒计数、读取 RDR，并在处理 ORE/FE/NE 后调用 `onImuByte()`。主循环每个待处理 IMU 帧进行两次 16 位 SPI 传输，然后编码联合帧；USART1 使用 TXE/TC 中断从固定输出队列非阻塞发送，不能在 ISR 内等待。

- [ ] **Step 4: 构建并检查容量**

  Run: `pio run -e stm32f070f6p6`

  Expected: SUCCESS，RAM 使用小于 6144 bytes，Flash 使用小于 32768 bytes，并生成 ELF/BIN。

- [ ] **Step 5: 重新运行主机测试**

  Run: `pio test -e native; python -m unittest tools.test_combined_capture -v`

  Expected: 全部 PASS。

- [ ] **Step 6: 提交固件目标**

  ```powershell
  git add firmware/stm32f070_imu_encoder
  git commit -m "feat: add STM32F070 sensor acquisition firmware"
  ```

### Task 5: 文档和交接验收

**Files:**
- Create: `firmware/stm32f070_imu_encoder/README.md`
- Modify: `D:/semg.claude/imu_carrier_v4_backup/HANDOFF_TO_CODEX.md`

**Interfaces:**
- Produces: 从 ST-Link 烧录到 PC 记录的完整操作说明和后续硬件验收清单。

- [ ] **Step 1: 写明烧录和运行命令**

  README 必须给出：

  ```powershell
  pio run -e stm32f070f6p6
  pio run -e stm32f070f6p6 -t upload
  pip install pyserial
  python tools/combined_capture.py --port COM5 --baud 921600 --csv capture.csv --raw capture.bin
  ```

  同时注明：代码编译不需要插设备；真实 400 Hz、丢帧、角度和延迟/抖动测试必须连接转接板、KT-EX9 和 AS5047P；校准物理边沿需逻辑分析仪；相机联合对齐才需要 D405。

- [ ] **Step 2: 更新项目交接文档**

  记录固件目录、协议版本、63 字节布局、构建命令、尚未完成的上板验证和“该方案属于同一 MCU 时间基准下的硬件侧绑定，不是双传感器同时触发”。

- [ ] **Step 3: 执行最终验证**

  ```powershell
  pio test -e native
  python -m unittest tools.test_combined_capture -v
  pio run -e stm32f070f6p6
  git diff --check
  git status --short
  ```

  Expected: 所有测试和构建成功；`git diff --check` 无输出；状态只包含本任务预期文档改动。

- [ ] **Step 4: 提交文档**

  ```powershell
  git add firmware/stm32f070_imu_encoder D:/semg.claude/imu_carrier_v4_backup/HANDOFF_TO_CODEX.md
  git commit -m "docs: add STM32 firmware bring-up guide"
  ```
