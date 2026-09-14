# Native RK3576 UMI collector

This directory contains the C++17 acquisition path for the D405 and the STM32
combined IMU/encoder stream. The process itself does not import or execute
Python. It uses the librealsense C/C++ API exported by the release's ARM64
RSUSB shared object, plus the board's Rockchip MPP GStreamer encoders.

The current scope is deliberately narrow:

- select one explicit D405 SDK serial and USB descriptor serial;
- capture RGB YUYV and left/right infrared Y8 at 1280x720, 30 Hz;
- parse and persist the STM32 63-byte combined stream at 921600 baud;
- write the existing v4 session/index/manifest contract;
- record RGB and both infrared eyes as Annex-B H.265;
- optionally expose a 1280x720 MJPEG RGB source at 15 Hz for the App-compatible
  loopback preview service, both while idle and while recording.

The C++ process owns acquisition and encoding only. The adjacent Python adapter
implements the frozen EGO recorderctl, preview, Catalog and signed transfer
contracts without changing App code.

Build on an Ubuntu 24.04 aarch64 RK3576 target from the root of a staged
release:

```sh
./native/build-on-rk3576.sh
```

The build is intentionally strict (`-Werror`). It also creates and verifies the
exact SONAME link required by the bundled RSUSB DSO without overwriting an
existing mismatched file.

Example bounded bench capture:

```sh
./native/bin/umi-record-native \
  --output-root /home/pi/umi-sessions \
  --duration 5 \
  --d405-sdk-serial 260322273737 \
  --d405-usb-serial 260323071293 \
  --stm32-port /dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_f6a5f836b505f011ae3b8c1272aab386-if00-port0
```

Add `--until-signal` for SIGINT/SIGTERM sealing. Add
`--preview-mjpeg-port PORT` to tee RGB into the loopback preview source while
recording. `--preview-only --preview-mjpeg-port PORT` starts the idle preview
source while still opening all three D405 profiles. A release is acceptable
only after capture, preview, Catalog publication and signed Range transfer pass
on the target hardware.
