# Hardware status — 2026-08-23

- The supplied `firmware.bin` was flashed to the STM32F070 board and the 63-byte
  `stm32_combined_v1` IMU transport was exercised on Ubuntu 22.04.
- A D405 + STM32 monitor-only run completed 10,800 seconds with result `PASS`:
  camera streams had zero skipped frames and the IMU formal window contained
  4,320,029 valid frames, zero bad frames, zero dropped frames, zero sequence
  gaps, and zero queue-overflow flags.
- Acceptance evidence SHA-256:
  `fc994e606a75a81882209e507cf29af0bce436501aab1f263622cbb135972e30`.
- This evidence accepts the camera/IMU transport path only. AS5047P angle validity,
  magnet geometry, zero point, radius conversion, and jaw-distance calibration
  remain pending and must not be represented as accepted.
