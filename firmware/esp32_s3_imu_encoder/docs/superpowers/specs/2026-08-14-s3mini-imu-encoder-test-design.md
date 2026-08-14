# ESP32-S3 Mini IMU + Encoder Test Firmware Design

## Repository isolation

Development occurs on branch `firmware/esp32-s3-imu-encoder`, based on
`main`. Every new firmware file lives below
`firmware/esp32_s3_imu_encoder/`. Existing camera, SLAM, VIO, configuration,
script, and test paths remain unchanged.

After hardware verification, the branch may be merged into `main` as an
additive directory-only change. Until the user approves that merge, `main`
remains untouched.

## Goal

Build temporary ESP32-S3 Mini firmware that continuously receives the
KT-EX9-2J-2-F1 IMU at 400 Hz, reads one AS5047P angle for every valid IMU
frame, and prints a human-readable combined status line at 10 Hz over native
USB CDC.

This firmware is for sensor and synchronization testing. It does not replace
the STM32F070F6P6 firmware planned for the final JLC carrier board and has no
runtime dependency on the repository's SLAM software.

## Hardware and pin configuration

The initial PlatformIO target is `esp32-s3-devkitc-1` with 4 MB flash so the
project can compile before the exact S3 Mini vendor is known. All
board-dependent GPIO values live in one configuration header and can be
changed after the Mini board arrives.

Default GPIO assignment:

| Interface | Signal | ESP32-S3 GPIO | External connection |
|---|---|---:|---|
| AS5047P SPI | CS | 10 | Encoder CS |
| AS5047P SPI | MOSI | 15 | Encoder MOSI |
| AS5047P SPI | MISO | 16 | Encoder MISO |
| AS5047P SPI | SCK | 17 | Encoder SCK |
| KT-EX9 UART | RX | 8 | IMU pin 19 UART-TXD |
| KT-EX9 UART | TX | 9 | IMU pin 21 UART-RXD |

The S3 Mini is powered from its USB connector. A separate regulated 3.3 V
supply rated for at least 500 mA powers the IMU and encoder. The regulator,
S3 Mini, IMU, and encoder grounds must be common. Neither sensor may receive
5 V.

## Input protocols

### KT-EX9 UART

- Baud rate: 921600
- Format: 8 data bits, no parity, 1 stop bit
- Nominal output rate: 400 Hz
- Frame size: 37 bytes
- Header and length bytes: `EB 90 22`
- Checksum: low eight bits of the sum of bytes 0 through 35, stored in byte 36
- Payload: seven little-endian `float` values at bytes 4 through 31, followed
  by a little-endian `uint32_t` counter at bytes 32 through 35

The parser consumes a continuous byte stream, resynchronizes on
`EB 90 22`, rejects invalid checksums, and continues without resetting the
board.

### AS5047P SPI

- SPI mode: mode 1
- Bit order: MSB first
- Initial clock: 1 MHz
- Angle register: `ANGLEUNC`, address `0x3FFE`
- Read sequence: send the parity-protected read command twice and use the
  second response because the sensor returns data through a pipelined SPI
  transaction
- Angle field: response bits 13 through 0
- Angle conversion: `raw * 360.0 / 16384.0`

The response error flag and even parity are checked for every reading.

## Sampling and synchronization

A valid IMU frame is the sampling trigger. Immediately after a 37-byte IMU
frame passes header and checksum validation, firmware records a 64-bit
microsecond timestamp and performs one AS5047P angle read. The resulting IMU
payload, IMU counter, timestamp, encoder raw angle, encoder degrees, and
encoder validity form one combined sample.

This produces up to 400 combined samples per second and provides an explicit
one-to-one relationship between IMU counter values and encoder angles. No
independent encoder timer or interpolation buffer is included in this test
firmware.

## Runtime behavior and USB output

Input processing runs continuously at the full sensor rate. Human-readable
USB output is rate-limited to 10 Hz by printing the newest combined sample
after each block of 40 valid IMU frames. A printed line contains:

- MCU timestamp in microseconds
- observed IMU rate
- IMU counter
- gyroscope X/Y/Z
- accelerometer X/Y/Z
- temperature
- encoder raw value and degrees
- encoder error/parity status
- valid IMU frame count
- bad-checksum count
- resynchronization count
- detected counter discontinuities

The firmware prints a compact waiting/status line once per second if no valid
IMU frame is available. A disconnected or invalid encoder does not stop IMU
parsing; the latest combined record marks the encoder invalid. USB printing
must never delay or replace UART byte consumption.

## Error and drop accounting

- `frames_ok` increments after a complete IMU frame passes checksum.
- `frames_bad` increments when a matching header has an invalid checksum.
- `resyncs` counts discarded bytes while seeking the next frame header.
- `dropped_frames` increments when the current IMU counter is neither the
  previous counter plus one nor the permitted wrap value 1.
- Encoder readings are valid only when the AS5047P error flag is clear and
  the response has even parity.

Counters remain monotonic for the lifetime of the firmware and are intended
for terminal diagnostics rather than permanent storage.

## Planned project layout

```text
firmware/esp32_s3_imu_encoder/
|-- .gitignore
|-- README.md
|-- platformio.ini
|-- include/
|   |-- board_config.h
|   |-- as5047p_protocol.h
|   `-- kt_ex9_protocol.h
|-- src/
|   `-- main.cpp
|-- test/
|   `-- test_protocol/
|       `-- test_main.cpp
`-- docs/
    `-- superpowers/
        |-- specs/
        `-- plans/
```

- `board_config.h`: GPIO, UART, SPI, and display-rate constants.
- `as5047p_protocol.h`: pure AS5047P command and response helpers.
- `kt_ex9_protocol.h`: pure IMU frame constants, validation, payload
  decoding, and streaming parser.
- `main.cpp`: hardware initialization, event loop, one-to-one sampling,
  statistics, and USB presentation.
- `test_main.cpp`: protocol-level tests for both sensors, including valid
  IMU parsing, bad checksum rejection, stream resynchronization, counter
  extraction, and AS5047P parity logic.

No PC application, wireless networking, persistent storage, JSON encoder,
root-level dependency, or final STM32 carrier-board firmware is part of this
scope.

## Verification and success criteria

Before hardware arrival:

1. Protocol tests demonstrate failure before implementation and pass after
   the minimum implementation is added.
2. PlatformIO production and test environments compile without warnings or
   errors for the configured ESP32-S3 target.
3. Git changes contain no modified path outside
   `firmware/esp32_s3_imu_encoder/`.

After the S3 Mini and sensors are connected:

1. USB terminal remains present and prints approximately 10 lines per second.
2. `frames_ok` grows at approximately 400 frames per second.
3. `frames_bad` and `dropped_frames` remain zero during a stationary
   60-second test.
4. Encoder parity is valid and its angle follows magnet rotation.
5. Disconnecting the encoder leaves IMU reception running.
6. Disconnecting the IMU produces the waiting message without resetting or
   freezing the S3 Mini.
