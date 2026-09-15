# Hardware status — 2026-09-15

## Scope

This record covers the STM32F070F6P6 firmware that clears the AS5047P
latched error register, reports its `ERRFL` cause bits, and reports UART queue
faults as one-shot events without changing the 63-byte `stm32_combined_v1`
wire layout.

## Target and rollback identity

- CP2102N serial: `f6a5f836b505f011ae3b8c1272aab386`
- ST-LINK/V2 serial: `000000000001`
- MCU probe: device `0x0445`, 32 KiB flash, 6 KiB SRAM
- Pre-flash full-flash backup (operator host):
  `/home/robot/umi161-stm32-backup-20260915-cfE02u/flash-before.bin`
- Pre-flash full-flash SHA-256:
  `1ad8a1fdc7776720faf75e6e7365a2ae9c42744c7c74d7044fc99a1a749da74c`
- Previous release firmware SHA-256:
  `1030e78c9fe4f7b949350b100d6389f219a8b650c649c834693619006953cd8a`

## Build and flash evidence

- Python protocol/interface tests: 24 passed, 0 failed.
- Native C++ protocol/pipeline tests: 21 passed, 0 failed.
- Clean STM32 target build: passed; 2896 bytes flash and 1544 bytes RAM used.
- Flashed `firmware.bin` SHA-256:
  `7eb0008d3c9e0e6335ce3cfcfe859a09a6c4d0e6bb8a1aaf11026beff1b3fabb`
- The first `st-flash` attempt erased four pages but its SRAM flash loader
  failed before writing. OpenOCD then programmed and verified the same target
  successfully; the failure was not hidden or treated as a successful flash.
- A full 32 KiB post-flash readback matched the release binary byte-for-byte;
  all remaining flash bytes were `0xFF`.

## Live sensor result

Before the one-shot fix, the RK3576 observed 1199/1199 packets at 399.538 Hz
with flags permanently equal to `0x0023`: IMU and encoder were valid, but one
startup UART/queue event remained latched forever and blocked every later
capture. The new deterministic tests reproduce that old behavior and require
both bit 5 and bit 6 to clear after one successfully queued report.

After flashing the fix, a local three-second window produced 1199 valid
packets at 399.607 Hz. Flags were `0x0003` for all 1199 packets. Encoder error,
parity error, sequence gap, and sequence regression counts were all zero.

This is a PASS for normal-boot STM32 transport and angle acquisition. The
AS5047P error/recovery path is covered by deterministic unit tests, but was not
triggered on hardware during this boot; induced-error HIL recovery therefore
remains pending.

## RK3576 repeated-capture result

The flashed sensor set was reconnected to `192.168.113.161` and sampled before
recording: 1198 valid packets at 399.305 Hz, all flags `0x0003`, zero CRC errors
and zero sequence gaps. Five consecutive 10-second C++ capture/stop/seal cycles
then completed successfully:

| Cycle | Stereo pairs | STM32 samples | Flags | Sequence gaps | Manifest |
| --- | ---: | ---: | --- | ---: | --- |
| 1 | 300 | 3999 | all `0x0003` | 0 | `00114ce4...` |
| 2 | 300 | 3999 | all `0x0003` | 0 | `70a8377d...` |
| 3 | 300 | 4000 | all `0x0003` | 0 | `24c46f68...` |
| 4 | 300 | 4000 | all `0x0003` | 0 | `a53ab757...` |
| 5 | 300 | 3999 | all `0x0003` | 0 | `ce129b35...` |

All five jobs reported `complete_local`, exit code 0, IMU runtime state `ok`,
and IMU quality `PASSED`. Each session's 13 manifest file hashes were verified.
After cycle 5, preview ownership recovered and delivered 20 JPEG frames at
1280x720; closing the session returned client/session counts to zero.
