# STM32 63-byte IMU + encoder PC protocol v1

This is an isolated, hardware-independent implementation based on the handoff
documents received on 2026-08-19. It is not yet a firmware/HIL acceptance.

## Wire contract

- Header: `A5 5A`
- Version: `1`
- Packet length: `63`
- CRC: CRC-16/CCITT-FALSE over bytes `0..60`, little-endian result at `61..62`
- Embedded KT-EX9 frame: bytes `24..60`, still validated with its own checksum
- Encoder angle: lower 14 bits of the AS5047P response, converted with
  `raw * 360 / 16384`

`CombinedImuEncoderReader` exposes two callbacks:

- `on_packet(CombinedPacket)` for lossless recording and encoder metadata.
- `on_sample(ImuSample)` for the existing recorder/VINS path.

The encoder is deliberately not fused into VINS. The existing 40-byte `<dI7f>`
`imu.bin` layout is unchanged.

## Timestamp policy

The packet-arrival clock is `time.monotonic()`, never wall-clock time. The two
MCU `uint32` microsecond counters are unwrapped independently. MCU acquisition
time is mapped into the PC monotonic domain, with a centered long-window scale
fit so USB batching does not become IMU sample jitter. Wall time is recorded
only as provenance and is not used as an acquisition timestamp.

An MCU reboot that is not a genuine `UINT32_MAX -> 0` wrap is rejected and
counted as a protocol error. A real implementation must start a new clock epoch
after such a reboot rather than silently joining two time domains.

## Sidecars

`CombinedPacketRecorder` writes:

- `imu_encoder_packets.bin`: exact concatenated 63-byte packets.
- `encoder_ts.csv`: flags, raw MCU clocks, signed IMU/encoder separation,
  unwrapped PC-monotonic timestamps, raw angle, and converted degrees.

The firmware build/mode and firmware binary SHA-256 are not carried in protocol
v1 and therefore must be saved in the capture session manifest before HIL use.

## Offline verification

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python3 -m pytest -q tests/test_imu_encoder_protocol.py
```

The simulation covers the known CRC vector, fragmented/noisy byte streams,
corrupt packet recovery, flags, sequence wrap/gaps, queue overflow, repeated MCU
timer wrap, USB batch arrival, clock-scale drift, byte-exact sidecars, legacy
40-byte recording, and the existing VINS `feed_imu(ImuSample)` contract.

## Still blocked without hardware/firmware

- Verification against the actual STM32 firmware binary and source commit.
- Baud-rate throughput, disconnect/reconnect, and long-run queue behavior.
- Confirmation that firmware mode C2 and its flags match the handoff document.
- Camera/IMU time offset and complete HIL acceptance.
