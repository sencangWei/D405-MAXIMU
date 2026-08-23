# STM32F070 Mode B and Ubuntu Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port confirmed-header encoder acquisition to STM32F070F6P6 and produce a verified Ubuntu 22.04 build, flash, and acceptance handoff package.

**Architecture:** USART2 RX interrupts timestamp bytes and push them into a fixed 64-entry SPSC queue. The main loop parses KT-EX9 bytes, performs the blocking AS5047P read immediately after `EB 90 22`, retains one pending encoder sample until the IMU checksum resolves, and emits the unchanged 63-byte packet through the existing non-blocking USART1 TX queue.

**Tech Stack:** Bare-metal STM32F0 CMSIS, C++17, PlatformIO 6, Unity native tests, Python 3.10+ protocol tests, ST-Link, Ubuntu 22.04.

**Command convention:** Run every command below from the repository root (`D405-MAXIMU`).

## Global Constraints

- Target MCU and pins remain STM32F070F6P6: PA2/PA3 USART2, PA4–PA7 SPI1, PA9/PA10 USART1, PA13/PA14 SWD.
- Keep the version-1 63-byte wire protocol byte-for-byte compatible.
- Keep 921600 baud on both UARTs, SPI Mode 1 at 750 kHz, and the existing 1 MHz extended TIM3 clock.
- USART2 ISR must never perform SPI, CRC, packet encoding, heap allocation, or USART1 output.
- Use only fixed storage; the target has 6 KB RAM.
- A valid IMU frame must still be emitted when the encoder is absent or invalid.
- Every production behavior change follows RED → GREEN → full regression verification.

---

### Task 1: Expose confirmed-header parsing

**Files:**
- Modify: `firmware/stm32f070_imu_encoder/include/kt_ex9_protocol.h`
- Test: `firmware/stm32f070_imu_encoder/test/test_pipeline/test_main.cpp`

**Interfaces:**
- Produces: `kt_ex9::ParseResult::HeaderConfirmed` exactly on the third byte of a valid `EB 90 22` candidate.
- Preserves: `FrameReady`, `BadChecksum`, `first_byte_rx_us`, checksum validation, resynchronization counters.

- [ ] **Step 1: Add the failing parser test**

Add a test that feeds `EB`, `90`, and `22` separately and requires results `None`, `None`, and `HeaderConfirmed`. Extend the existing full-frame helper test so bytes after the third still return `None` until the final byte returns `FrameReady`.

```cpp
void test_parser_reports_confirmed_header_on_third_byte() {
    kt_ex9::StreamParser parser;
    kt_ex9::Frame frame{};
    TEST_ASSERT_EQUAL_INT(
        static_cast<int>(kt_ex9::ParseResult::None),
        static_cast<int>(parser.feed(0xEBU, 1000U, frame)));
    TEST_ASSERT_EQUAL_INT(
        static_cast<int>(kt_ex9::ParseResult::None),
        static_cast<int>(parser.feed(0x90U, 1011U, frame)));
    TEST_ASSERT_EQUAL_INT(
        static_cast<int>(kt_ex9::ParseResult::HeaderConfirmed),
        static_cast<int>(parser.feed(0x22U, 1022U, frame)));
}
```

- [ ] **Step 2: Run native tests and verify RED**

```bash
pio test -d firmware/stm32f070_imu_encoder -e native -f test_pipeline
```

Expected: compilation fails because `ParseResult::HeaderConfirmed` does not exist.

- [ ] **Step 3: Implement the minimal parser event**

Add `HeaderConfirmed` to `ParseResult`. After storing the third correct header byte and setting `size_` to3, return `HeaderConfirmed`; subsequent bytes retain existing behavior.

```cpp
enum class ParseResult : uint8_t {
    None,
    HeaderConfirmed,
    FrameReady,
    BadChecksum,
};

buffer_[size_++] = byte;
if (size_ == 3U) {
    return ParseResult::HeaderConfirmed;
}
```

- [ ] **Step 4: Run the focused test and verify GREEN**

```bash
pio test -d firmware/stm32f070_imu_encoder -e native -f test_pipeline
```

Expected: all `test_pipeline` cases pass.

- [ ] **Step 5: Commit**

```bash
git add firmware/stm32f070_imu_encoder/include/kt_ex9_protocol.h \
        firmware/stm32f070_imu_encoder/test/test_pipeline/test_main.cpp
git commit -m "feat(firmware): report confirmed IMU header"
```

### Task 2: Add pending encoder association and RX buffering

**Files:**
- Modify: `firmware/stm32f070_imu_encoder/include/capture_pipeline.h`
- Test: `firmware/stm32f070_imu_encoder/test/test_pipeline/test_main.cpp`

**Interfaces:**
- Produces: `capture::PipelineEvent { None, EncoderReadRequested, BadImuFrame, SampleReady }`.
- Produces: `Pipeline::onImuByte(uint8_t, uint32_t) -> PipelineEvent`.
- Produces: `Pipeline::storePendingEncoder(uint16_t response, uint32_t read_us)`.
- Produces: `Pipeline::noteImuQueueOverflow(uint32_t count = 1U)`.
- Produces: `capture::ImuRxBuffer<64>` with `push`, `pop`, `empty`, and `overflows`.

- [ ] **Step 1: Replace helper assumptions with failing Mode B tests**

Add focused tests for these behaviors:

```cpp
void test_valid_frame_uses_encoder_captured_at_header() {
    capture::Pipeline pipeline;
    const auto frame = makeImuFrame(10U);
    for (size_t i = 0; i < frame.size(); ++i) {
        const auto event = pipeline.onImuByte(frame[i], 1000U + i * 11U);
        if (i == 2U) {
            TEST_ASSERT_EQUAL_INT(
                static_cast<int>(capture::PipelineEvent::EncoderReadRequested),
                static_cast<int>(event));
            pipeline.storePendingEncoder(0x9234U, 1044U);
        }
    }
    combined::Sample output{};
    TEST_ASSERT_TRUE(pipeline.popOutput(output));
    TEST_ASSERT_EQUAL_UINT32(1000U, output.imu_first_byte_rx_us);
    TEST_ASSERT_EQUAL_UINT32(1044U, output.encoder_read_us);
    TEST_ASSERT_EQUAL_HEX16(0x9234U, output.encoder_response);
}
```

Also add:

- a bad-checksum frame with a stored pending sample followed by a valid frame without a stored sample; the valid output must not contain the stale response;
- a valid frame without a pending encoder sample; IMU valid is high and encoder valid is low;
- `ImuRxBuffer<2>` rejects its third queued event, reports one overflow, and preserves FIFO order;
- `noteImuQueueOverflow()` makes the next emitted sample carry `kImuQueueOverflow`.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
pio test -d firmware/stm32f070_imu_encoder -e native -f test_pipeline
```

Expected: compilation fails on the missing `PipelineEvent`, new Pipeline API, and `ImuRxBuffer`.

- [ ] **Step 3: Implement fixed RX storage**

Keep the existing `SpscQueue` and add:

```cpp
struct ImuRxByte {
    uint8_t value = 0U;
    uint32_t rx_us = 0U;
};

template <size_t Capacity>
class ImuRxBuffer {
public:
    bool push(const ImuRxByte& value) {
        if (!queue_.push(value)) {
            ++overflows_;
            return false;
        }
        return true;
    }
    bool pop(ImuRxByte& value) { return queue_.pop(value); }
    bool empty() const { return queue_.empty(); }
    uint32_t overflows() const { return overflows_; }

private:
    SpscQueue<ImuRxByte, Capacity> queue_{};
    volatile uint32_t overflows_ = 0U;
};
```

- [ ] **Step 4: Implement minimal Mode B coordinator**

The pipeline owns the parser, one pending encoder response/time, counter continuity state, output queue, and sticky flags. Required event behavior:

```cpp
const auto result = parser_.feed(byte, rx_us, frame);
if (result == kt_ex9::ParseResult::HeaderConfirmed) {
    pending_encoder_valid_ = false;
    return PipelineEvent::EncoderReadRequested;
}
if (result == kt_ex9::ParseResult::BadChecksum) {
    pending_encoder_valid_ = false;
    return PipelineEvent::BadImuFrame;
}
if (result == kt_ex9::ParseResult::FrameReady) {
    emitFrame(frame);
    return PipelineEvent::SampleReady;
}
return PipelineEvent::None;
```

`emitFrame` copies the pending response/time only when present, calculates the same encoder validity flags, always emits a valid IMU, consumes pending once, and retains the existing output-overflow and counter-gap behavior.

- [ ] **Step 5: Run focused tests and verify GREEN**

```bash
pio test -d firmware/stm32f070_imu_encoder -e native -f test_pipeline
```

Expected: all pipeline tests pass, including stale-sample rejection and overflow reporting.

- [ ] **Step 6: Commit**

```bash
git add firmware/stm32f070_imu_encoder/include/capture_pipeline.h \
        firmware/stm32f070_imu_encoder/test/test_pipeline/test_main.cpp
git commit -m "feat(firmware): bind encoder sample at IMU header"
```

### Task 3: Move byte parsing out of USART2 ISR

**Files:**
- Modify: `firmware/stm32f070_imu_encoder/src/main.cpp`
- Test: `firmware/stm32f070_imu_encoder/test/test_pipeline/test_main.cpp`

**Interfaces:**
- Consumes: `capture::ImuRxBuffer<64>`, `PipelineEvent::EncoderReadRequested`, and `Pipeline::storePendingEncoder`.
- Preserves: non-blocking USART1 TX queue, TIM3 timebase, AS5047P transaction, pin mapping, and 63-byte output.

- [ ] **Step 1: Add the ISR/main integration contract test before editing `main.cpp`**

Add a native test that pushes a complete timestamped frame into `ImuRxBuffer<64>`, drains it through `Pipeline::onImuByte`, injects the encoder response only on `EncoderReadRequested`, and confirms one correct output. This proves buffering preserves first-byte time and association.

- [ ] **Step 2: Run the new integration contract**

```bash
pio test -d firmware/stm32f070_imu_encoder -e native -f test_pipeline
```

Expected: the test passes because Task 2 deliberately established and unit-tested the buffering and association behavior before hardware wiring. This task changes only target-specific ISR/main integration; its acceptance gates are the unchanged native contract plus a warning-free STM32 target build.

- [ ] **Step 3: Make USART2 ISR enqueue only**

Create `capture::ImuRxBuffer<64U> g_imu_rx`. Replace direct parser invocation with:

```cpp
if ((status & USART_ISR_RXNE) != 0U) {
    const uint8_t byte = static_cast<uint8_t>(USART2->RDR);
    g_imu_rx.push({byte, rx_us});
}
```

- [ ] **Step 4: Drain and act in the main loop**

Add `serviceImuInput()` before output service:

```cpp
capture::ImuRxByte input{};
while (g_imu_rx.pop(input)) {
    const auto event = g_pipeline.onImuByte(input.value, input.rx_us);
    if (event == capture::PipelineEvent::EncoderReadRequested) {
        const uint32_t read_us = micros32();
        const uint16_t response = readEncoder();
        g_pipeline.storePendingEncoder(response, read_us);
    }
}
```

Track `g_imu_rx.overflows()` in the main loop and call `noteImuQueueOverflow(delta)` when it increases. Include `!g_imu_rx.empty()` in the WFI work-ready predicate. Do not change interrupt priorities: TIM3=0, USART2=1, USART1=2.

- [ ] **Step 5: Run full native C++ tests**

```bash
pio test -d firmware/stm32f070_imu_encoder -e native
```

Expected: all protocol and pipeline tests pass with zero failures.

- [ ] **Step 6: Build the STM32 target**

```bash
pio run -d firmware/stm32f070_imu_encoder -e stm32f070f6p6
```

Expected: exit0, no warnings under `-Werror`, Flash below32768 bytes, RAM below6144 bytes.

- [ ] **Step 7: Commit**

```bash
git add firmware/stm32f070_imu_encoder/src/main.cpp \
        firmware/stm32f070_imu_encoder/test/test_pipeline/test_main.cpp
git commit -m "feat(firmware): receive IMU bytes during header SPI read"
```

### Task 4: Verify protocol tools and create Ubuntu handoff

**Files:**
- Modify: `firmware/stm32f070_imu_encoder/README.md`
- Create: `firmware/stm32f070_imu_encoder/docs/UBUNTU_22_04_FLASH_AND_ACCEPTANCE.md`
- Create: `firmware/stm32f070_imu_encoder/release/firmware.bin`
- Create: `firmware/stm32f070_imu_encoder/release/SHA256SUMS`
- Create: `firmware/stm32f070_imu_encoder/release/BUILD_INFO.txt`
- Create outside Git: Windows Desktop ZIP containing the firmware directory and handoff documents.

**Interfaces:**
- Consumes: the unchanged 63-byte protocol and `.pio/build/stm32f070f6p6/firmware.bin`.
- Produces: source and binary paths usable on Ubuntu22.04 with PlatformIO and ST-Link.

- [ ] **Step 1: Run all Python interface tests**

```powershell
Push-Location firmware/stm32f070_imu_encoder
python -m unittest discover -s tools -p 'test_*.py' -v
Pop-Location
```

Expected: every test passes, including CRC, queue, restart, invalid encoder, and recording behavior.

- [ ] **Step 2: Re-run all C++ tests and target build from a clean build directory**

```bash
pio run -d firmware/stm32f070_imu_encoder -t clean
pio test -d firmware/stm32f070_imu_encoder -e native
pio run -d firmware/stm32f070_imu_encoder -e stm32f070f6p6
```

Expected: tests and build exit0; record exact test counts and memory usage.

- [ ] **Step 3: Stage reproducible binary evidence**

Copy `.pio/build/stm32f070f6p6/firmware.bin` to `release/firmware.bin`. Write `SHA256SUMS` in standard `<hash>  firmware.bin` format. `BUILD_INFO.txt` records Git commit, build date, target, PlatformIO version, test counts, Flash/RAM usage, and the statement `Hardware acceptance pending`.

- [ ] **Step 4: Write exact Ubuntu22.04 instructions**

Document:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip udev
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip platformio pyserial
pio test -e native
pio run -e stm32f070f6p6
pio run -e stm32f070f6p6 -t upload
```

Also document PlatformIO udev rules, `lsusb`, ST-Link/J4 wiring by signal name, USB-powered target precautions, CP2102N discovery under `/dev/serial/by-id`, the Python capture command, and the exact no-short/power/IMU-only/encoder/10-minute/3-hour acceptance sequence.

- [ ] **Step 5: Verify release integrity**

Run on Windows:

```powershell
Get-FileHash -Algorithm SHA256 release\firmware.bin
```

Extract the ZIP to a temporary directory and confirm the handoff document, source, release binary, and checksum are present. Do not claim Ubuntu execution was performed unless it was actually run on Ubuntu22.04.

- [ ] **Step 6: Commit tracked delivery files**

```bash
git add firmware/stm32f070_imu_encoder/README.md \
        firmware/stm32f070_imu_encoder/docs/UBUNTU_22_04_FLASH_AND_ACCEPTANCE.md \
        firmware/stm32f070_imu_encoder/release
git commit -m "docs(firmware): prepare Ubuntu STM32 handoff"
```

- [ ] **Step 7: Update project handoff status**

Update `D:\semg.claude\imu_carrier_v4_backup\HANDOFF_TO_CODEX.md` with the selected mode, commits, test evidence, release hash, Ubuntu package path, and explicit remaining STM32 hardware acceptance steps.
