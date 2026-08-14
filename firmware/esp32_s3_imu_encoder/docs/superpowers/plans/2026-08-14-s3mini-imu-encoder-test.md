# ESP32-S3 Mini IMU + Encoder Test Firmware Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce flashable ESP32-S3 Mini firmware that binds one AS5047P angle reading to every valid 400 Hz KT-EX9 UART frame and displays combined diagnostics at 10 Hz over USB CDC.

**Architecture:** Pure header-only protocol helpers are tested in PlatformIO's native environment. Arduino hardware code uses UART1 for KT-EX9, SPI for AS5047P, and native USB CDC for rate-limited status output.

**Tech Stack:** PlatformIO, Arduino-ESP32, C++17, Unity, ESP32-S3 hardware UART, SPI, USB CDC.

## Global Constraints

- Only paths below `firmware/esp32_s3_imu_encoder/` may change.
- Work remains on branch `firmware/esp32-s3-imu-encoder`; `main` is not modified.
- KT-EX9 input is 921600 baud, 8N1, 37 bytes, header `EB 90 22`, nominally 400 Hz.
- AS5047P uses SPI mode 1 at 1 MHz and reads `ANGLEUNC` address `0x3FFE`.
- Full-rate processing is 400 Hz; human-readable USB output is 10 Hz.
- Sensor power is external regulated 3.3 V; firmware never controls sensor power.

---

### Task 1: Add the isolated PlatformIO project and failing protocol tests

**Files:**
- Create: `firmware/esp32_s3_imu_encoder/platformio.ini`
- Create: `firmware/esp32_s3_imu_encoder/.gitignore`
- Create: `firmware/esp32_s3_imu_encoder/test/test_protocol/test_main.cpp`

**Interfaces:**
- Consumes: the protocol constants and behavior approved in the design spec.
- Produces: executable Unity tests that initially fail because the protocol headers do not exist.

- [ ] Create an ESP32-S3 production environment and a native Unity test environment.
- [ ] Add tests for AS5047P command parity, response fields, KT-EX9 checksum, payload decoding, stream resynchronization, bad-checksum accounting, and counter discontinuities.
- [ ] Run `pio test -e native` and confirm compilation fails because `as5047p_protocol.h` and `kt_ex9_protocol.h` are missing.
- [ ] Commit the red-test state.

### Task 2: Implement pure protocol helpers and make tests pass

**Files:**
- Create: `firmware/esp32_s3_imu_encoder/include/as5047p_protocol.h`
- Create: `firmware/esp32_s3_imu_encoder/include/kt_ex9_protocol.h`

**Interfaces:**
- `as5047p::makeReadCommand(uint16_t) -> uint16_t`
- `as5047p::hasEvenParity(uint16_t) -> bool`
- `as5047p::hasError(uint16_t) -> bool`
- `as5047p::data(uint16_t) -> uint16_t`
- `kt_ex9::verifyChecksum(const uint8_t*, size_t) -> bool`
- `kt_ex9::parseFrame(const uint8_t*, size_t, Sample&) -> bool`
- `kt_ex9::StreamParser::feed(uint8_t, Sample&) -> ParseResult`
- `kt_ex9::isCounterDiscontinuity(uint32_t, uint32_t) -> bool`

- [ ] Implement the minimum AS5047P helpers required by the tests.
- [ ] Implement KT-EX9 little-endian decoding and the fixed-size sliding stream parser.
- [ ] Run `pio test -e native` and require all Unity tests to pass.
- [ ] Commit the green protocol implementation.

### Task 3: Implement hardware acquisition and the 10 Hz terminal view

**Files:**
- Create: `firmware/esp32_s3_imu_encoder/include/board_config.h`
- Create: `firmware/esp32_s3_imu_encoder/src/main.cpp`

**Interfaces:**
- UART1 RX GPIO 8 receives IMU pin 19 TXD; UART1 TX GPIO 9 drives IMU pin 21 RXD.
- SPI CS/SCK/MISO/MOSI use GPIO 10/17/16/15.
- Every `ParseResult::Frame` triggers one two-transfer AS5047P read and updates one combined sample.
- Every 40 valid IMU frames prints one status line; absence of valid IMU frames prints one waiting line per second.

- [ ] Add all board-dependent constants to `board_config.h`.
- [ ] Initialize USB CDC, UART1, SPI, and the AS5047P chip-select pin.
- [ ] Drain all available UART bytes on every loop iteration and parse them without blocking delays.
- [ ] On each valid IMU frame, timestamp with `esp_timer_get_time()`, read and validate AS5047P, update rate/drop statistics, and print only on the 40-frame boundary.
- [ ] Build with `pio run -e esp32s3` and require exit code zero.
- [ ] Commit the flashable firmware.

### Task 4: Add operator instructions and run final verification

**Files:**
- Create: `firmware/esp32_s3_imu_encoder/README.md`

**Interfaces:**
- Documents exact power wiring, signal wiring, build, upload, monitor, expected output, and GPIO adjustment location.

- [ ] Document the external 3.3 V regulator and mandatory common ground.
- [ ] Document `pio run -e esp32s3 -t upload` and `pio device monitor -b 115200`.
- [ ] Run `pio test -e native`, `pio run -e esp32s3`, `git diff --check`, and a path-scope check.
- [ ] Commit documentation, push the feature branch, and report that hardware validation remains pending until the S3 Mini and IMU are connected.
