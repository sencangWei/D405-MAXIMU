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

The release also contains the device-hosted `web-console`: it uses the same
C++ RSUSB recorder through the adapter, extrapolates active duration from the
last monotonic device sample instead of resetting to stale cached values, and
offers an explicitly confirmed asynchronous deletion operation for one exact
completed recording. Deletion reuses the EGO secure Catalog tree primitive,
refuses active capture/transfer, tombstones the Catalog row, and removes a
prepared browser-download archive only after source deletion completes.

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

Release 0.2.4 adds exclusive D405 ownership across preview/capture, generation-
safe preview handoff, bounded encoder shutdown, packet-validated STM32 warmup,
and a D405 udev power policy that prevents runtime autosuspend between repeated
Web sessions. A one-second in-recording camera gap still fails closed; the
collector never seals a discontinuous session as complete.

Release 0.2.5 versions the device Web console inside the immutable release,
keeps the displayed capture clock monotonic across duplicate samples/renders,
and adds an explicitly confirmed, Catalog-authorized recording deletion flow.
QR provisioning is intentionally not enabled in this release; its fixed-code
binding payload and roaming identity behavior remain gated on the canonical
`ego-contracts` and `ego-device-platform` contracts.

Release 0.2.6 fixes a 0.2.5 regression where the new recording deletion left
the durable publication ledger at state PUBLISHED, so the next capture start
failed recovery with "published recording payload is unavailable". Deletion
now retires the ledger, and recovery self-heals published ledgers whose
payload was removed by a confirmed deletion while still failing closed when
a payload vanishes without one.

Release 0.3.0 makes interrupted captures visible, recoverable and deletable.
A power loss or a killed collector leaves an unsealed staging directory in
`incoming/.<session>.partial/`; no catalog row describes it, so the Web console
neither listed it nor let an operator reclaim the space. The console now has an
"Incomplete recordings" section backed by `recorderctl incomplete-list`, and
`incomplete-recover` / `incomplete-delete` perform the two repairs. Rescue is a
lossless byte-copy remux of the raw H.265 access units into MP4
(`adapter/umi_remux.py`, stdlib only) because this image cannot install
`h265parse` and no GStreamer element can turn an Annex-B stream into `hvc1`;
re-encoding was rejected as it would damage the y8 luma infrared streams that
stereo SLAM consumes. Each asset's source is released only after its MP4 is
remuxed, structurally verified and byte-compared, and the result is published as
an ordinary COMPLETE_LOCAL recording with `recovery_hint`/`display_name`
marking it as rescued and an honest `imu_quality_status` - a rescued session is
never presented as a verified capture. The 32 GB orphan that triggered this work
was left untouched: the operator decides when to spend the space.

Release 0.3.1 removes an over-strict rescue guard found on the board. A
capture killed by a camera or stream failure can leave a stream holding fewer
access units than its frame index claims; 0.3.0 refused such a session with
INCOMPLETE_GATE_FAILED even though the data was usable. The rescue now
reconciles the two: the published pair count is the minimum over the indexes and
the access units the streams actually carry, the shipped frame indexes are
truncated to that count so they never claim a frame no video holds, and the
manifest records the per-stream counts plus STREAM_LENGTH_MISMATCH.

Release 0.3.2 closes a gap the 0.3.1 board run exposed: a rescue that fails
before publishing leaves its work directory `.recover-<session>/` behind, and
once the operator deletes the orphan that directory is unreferenced - invisible
to the console and unreclaimable (3.1 GB in the observed case). The listing now
reports such a directory as `staging_leftover` with its size, deleting it
reclaims the space, and deleting an orphan also reclaims its work directory.
Rescue is refused for a work directory on its own, since there is no capture
left to rescue.

Release 0.3.3 fixes deletion idempotency for interrupted recordings. The
adapter treated any completed deletion record as a replay and returned without
doing anything, so a rescue work directory that appeared (or survived) after an
earlier deletion could never be reclaimed - the board was left with a 3.1 GB
directory that the console listed but refused to remove. A completed deletion is
now only a replay while nothing remains on disk for that session, and
`incomplete-status` reports the most recent record instead of always preferring
the rescue record.

## Operator documentation

See [DEPLOYMENT_AND_USAGE.zh-CN.md](DEPLOYMENT_AND_USAGE.zh-CN.md) for the
Chinese deployment, configuration, App integration, collection, transfer,
acceptance, rollback and troubleshooting guide. It is written so a new host
can install the immutable package without needing the original development
conversation.
