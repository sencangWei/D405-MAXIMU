# UMI RK3576 collection endpoint

This directory contains the RK3576 device-side collection adapter for the UMI
D405 + STM32 IMU/magnetic-encoder assembly. It does not modify or embed the
mobile/desktop App.

## Data paths

- RGB record: 1280x720 at 30 Hz, Rockchip MPP H.265.
- Left and right IR record: separate 1280x720 at 30 Hz MPP H.265 streams.
- STM32 record: original 63-byte `stm32_combined_v1` packets plus a timestamp
  index; IMU and magnetic encoder remain in the same MCU clock domain.
- App preview: RGB 1280x720 multipart MJPEG, target 15 Hz, using the existing
  EGO `stereo.mjpg` preview contract.
- App lifecycle: existing `recorderctl_v1`, M02 Catalog, immutable snapshot,
  signed HTTP Range/ETag transfer and final receipt contracts.

The C++17 executable owns D405/STM32 acquisition and hardware encoding. The
Python adapter maps the native sealed session to existing App contracts.
Shared EGO runtime code is a package-time dependency and is not duplicated in
this repository.

## Precision boundary

The two IR H.265 streams are compact and **lossy** (`lossless=false` in the
native manifest). They passed the bound RK3576 capture/decode and available
SLAM comparison, but that evidence does not make them equivalent to this
repository's raw DB3/FFV1 precision baseline. Use the lossless pipeline when a
dataset must retain the repository's strongest precision guarantee.

RGB is preview-only for the VINS stereo interpretation: RGB and left IR are
not a valid stereo pair. SLAM stereo input remains left IR + right IR.

## Build and release

`native/build-on-rk3576.sh` builds on Ubuntu 24.04 aarch64. A staged release
must additionally provide:

- the RSUSB-enabled librealsense 2.58.2 Python DSO in `runtime/`;
- the App contract runtime frozen from
  `JO-ara-dev/ego-recorder@21520fac865c69def3fc6d3c8a9d67abd16ba50b`;
- locked Python 3.12 aarch64 dependencies;
- generated ARM64 binaries, release manifest and `SHA256SUMS`.

`check-host.sh` disables Python bytecode writes so verifying a package cannot
mutate files covered by `SHA256SUMS`; `install.sh` rechecks every file before
copying and again before atomically selecting the immutable version.

The full package is intentionally not tracked here because generated binaries
and `.so` files are ignored. The immutable `0.2.3` aarch64 archive is bound to
the source commit recorded in `RELEASE_PROVENANCE.json`; verify its filename,
size, archive SHA-256 and native-binary SHA-256 before installation.

The source tree includes matching public librealsense headers and the upstream
license. Generated files under `native/bin/`, `runtime/` and `vendor/` must not
be committed.

## Per-device configuration

Copy `service/umi-recorder.env.example` outside the immutable release, then set
the unit's own `UMI_DEVICE_ID`, D405 SDK serial, D405 USB descriptor serial and
STM32 `/dev/serial/by-id/` path. A different assembly also requires its own
camera-to-IMU extrinsics; do not reuse an identity merely because the sensor
models match.

TLS private keys, trusted App keys, calibration files, recordings, Catalog
databases and logs remain outside Git. The supplied services bind preview and
Admin endpoints to loopback.

All four hardware identity fields are mandatory. The controller and preview
service fail closed when any field is absent or still contains a `CHANGE_ME`
placeholder; there are no bench-device fallback serials.

STM32 flags bit 4 (`IMU_COUNTER_GAP`), bit 5 (`IMU_QUEUE_OVERFLOW`) and bit 6
(`PC_TX_QUEUE_OVERFLOW`) are hard capture failures, just like CRC, framing,
sequence and validity failures. A session carrying any of them cannot be
published as `PASSED`.

Active jobs are tied to boot ID, PID and Linux process start ticks. After a
power loss or worker crash, stale state is converted to `interrupted` instead
of permanently blocking the next App request. Publication uses a durable
prepare ledger; preflight completes a Catalog transaction left between the
final directory rename and the idempotent Catalog insert.

## Bound evidence

On the IP `.30` bench assembly, the final candidate recorded 600/600/600
RGB/left-IR/right-IR frames over 20 seconds while receiving 8001 STM32 packets.
Camera rates were about 30.008 Hz and STM32 about 400.005 Hz, with no reported
sequence gaps, timestamp regressions, invalid flags or encoder queue overflow.
The App-order preview handoff held 1280x720 at 14.606 Hz with a 1.468 second
maximum handoff gap, and MPP decoded all 600 frames from every H.265 stream.

This is `BENCH_OBSERVED`, not a claim that every physical assembly is accepted.
Each new RK3576 must repeat identity, preview, bounded collection, integrity,
Catalog and transfer checks before use.

## Operator documentation

See [DEPLOYMENT_AND_USAGE.zh-CN.md](DEPLOYMENT_AND_USAGE.zh-CN.md) for the
Chinese deployment, configuration, App integration, collection, transfer,
acceptance, rollback and troubleshooting guide. It is written so a new host
can install the immutable package without needing the original development
conversation.
