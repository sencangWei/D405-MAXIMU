# ESP32-S3 Mini KT-EX9 + AS5047P Test Firmware

Temporary test firmware for collecting the KT-EX9-2J-2-F1 IMU and AS5047P
magnetic encoder on one ESP32-S3 clock.

- KT-EX9 UART is consumed at its full 400 Hz rate.
- One AS5047P angle is read after every valid IMU frame.
- USB CDC prints the newest combined sample every 40 IMU frames, about 10
  lines per second.
- IMU checksum, stream resynchronization, counter discontinuities, AS5047P
  error flag, and AS5047P parity are reported.

This directory is an independent PlatformIO project. It has no build or
runtime dependency on the repository's SLAM software.

## Power wiring

The KT-EX9 and AS5047P are 3.3 V devices. Do not apply 5 V to either sensor.

```text
Computer USB -> ESP32-S3 Mini USB connector

USB 5V/VBUS -> external 5V-to-3.3V regulator input
Regulator 3.3V -> KT-EX9 VDD and AS5047P VCC
Regulator GND -> KT-EX9 GND, AS5047P GND, and ESP32-S3 GND
```

Use a regulator rated for at least 500 mA continuous output. The ESP32-S3
Mini remains powered by its USB connector; do not connect the external
regulator's 3.3 V output to the Mini's 3V3 pin.

## Signal wiring

### KT-EX9 UART

| KT-EX9 connector | Function | ESP32-S3 Mini |
|---|---|---|
| Pins 10, 11, 12 | 3.3 V VDD | External regulator 3.3 V |
| Pins 13, 14, 15 | GND | Common GND |
| Pin 19 | UART-TXD | GPIO 8, UART RX |
| Pin 21 | UART-RXD | GPIO 9, UART TX |

UART format is 921600 baud, 8 data bits, no parity, and 1 stop bit.

### AS5047P SPI

| Encoder signal | ESP32-S3 Mini |
|---|---|
| VCC | External regulator 3.3 V |
| CS | GPIO 10 |
| SCK/CLK | GPIO 17 |
| MISO | GPIO 16 |
| MOSI | GPIO 15 |
| GND | Common GND |

SPI uses mode 1 at 1 MHz. GPIO assignments are centralized in
`include/board_config.h`.

## Build and upload

Install PlatformIO, open a terminal in this directory, and run:

```powershell
pio run -e esp32s3
pio run -e esp32s3 -t upload
pio device monitor -b 115200
```

If more than one serial device is connected, specify the upload port:

```powershell
pio run -e esp32s3 -t upload --upload-port COM5
pio device monitor --port COM5 -b 115200
```

The initial build target is `esp32-s3-devkitc-1`. After the exact S3 Mini
arrives, confirm its flash size and exposed GPIO labels before the first
upload. If its pins differ, only `include/board_config.h` needs GPIO changes.

## Expected terminal output

At startup:

```text
ESP32-S3 KT-EX9 + AS5047P test firmware
IMU: 921600 8N1, RX=GPIO8 TX=GPIO9, expected 400Hz
ENC: CS=10 MOSI=15 MISO=16 SCK=17, Mode1, 1MHz
POWER: IMU and encoder require regulated 3.3V; all grounds common
```

With both sensors running:

```text
t_us=12345678 rate=400.01Hz cnt=120 gyro=[...] accel=[...] temp=26.50C enc_raw=8192 enc=180.00deg enc_ok=1 frame=0x2000 ok=120 bad=0 resync=0 drops=0
```

If the IMU is absent or stops sending:

```text
WAITING_IMU ok=0 bad=0 resync=0 drops=0
```

`enc_ok=1` requires a clear AS5047P error flag and valid even parity.

## Protocol tests

The protocol layer is hardware-independent. On this Windows development
machine, the PlatformIO MinGW toolchain can run it with:

```powershell
$env:PATH="$env:USERPROFILE\.platformio\packages\toolchain-gccmingw32\bin;$env:PATH"
pio test -e native
```

Hardware acceptance still requires the purchased S3 Mini, the KT-EX9, and
the encoder to be connected. A clean build alone does not validate electrical
wiring, sensor power, actual 400 Hz input, or encoder magnet alignment.
