# Hardware status — 2026-09-15

## Scope

This record covers the STM32F070F6P6 firmware that clears the AS5047P
latched error register and reports its `ERRFL` cause bits without changing the
63-byte `stm32_combined_v1` wire layout.

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
  `f975784605f84d2334ea8399cc547b0ceb4b3d1c0ac8f8976fb862f603251dd4`
- `st-flash --reset write` erased four pages, wrote, and verified successfully.
- A full 32 KiB post-flash readback matched the release binary byte-for-byte;
  all remaining flash bytes were `0xFF`.

## Live sensor result

After a full power cycle, a one-second warm-up followed by a formal ten-second
window produced 4001 valid packets at 400.010728 Hz. Flags were `0x0003` for
all 4001 packets. Encoder error, parity error, sequence gap, and sequence
regression counts were all zero. Encoder raw values ranged from 18 to 16381.

This is a PASS for normal-boot STM32 transport and angle acquisition. The
AS5047P error/recovery path is covered by deterministic unit tests, but was not
triggered on hardware during this boot; induced-error HIL recovery therefore
remains pending. RK3576 capture/stop/restart validation also remains pending
until this sensor set is reconnected to `192.168.113.161`.
