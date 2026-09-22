# Jazzy migration and current runtime state (2026-08-16)

## Migration bundle

- Handoff source: `/home/robot/ego_vio_humble/JAZZY_HANDOFF_20260816/`.
- Use `copy_to_ssd.sh <ext4 mount root>`; it refuses non-mount targets, non-POSIX filesystems, active capture, or insufficient capacity.
- Effective source after final classification is about185GiB plus20GiB required headroom. The mounted222GiB ext4 SSD passes the read-only capacity gate. Copy is direct rsync with no `--delete`; final bundle has per-file SHA-256 and Git bundles/dirty patches.
- New machine is ROS2 Jazzy. Humble `build/install/log` and CPython3.10 pyrealsense2 are evidence only; clean rebuild RSUSB librealsense 2.58.2 and ROS packages.
- One private repository keeps two branches: `handoff/jazzy-20260816` for the Jazzy candidate and `release/humble-known-good-20260816` for the frozen Ubuntu22.04/ROS2 Humble fallback. Do not create separate calibration or VINS repositories.

## Current versus legacy

- `CURRENT_ACTIVE`: D405 color+dual-IR 1280x720@30 via RSUSB, KT-EX9-2 400Hz, raw DB3, VINS dual-IR config `d405_stereo_imu_config.yaml`, `estimate_td=0`, `td=-0.0117`, replay shift 0.
- Live IMU runtime calibration is `imu_runtime_accel_calibrated_raw_gyro_20260816.yaml`: six-face accel correction, identity gyro matrix, zero static gyro bias so VINS estimates bias online.
- Failed manual gyro candidate `imu_manual_calibration/intrinsic_20260803_000908/calibration_candidate.yaml` must be rejected (`acceptance: FAIL`, `runtime_applied:false`). Runtime loader now enforces this.
- Failed fixed world-Z `level-candidate` mode is hard-disabled in `run_vins_realtime.sh`.
- `LEGACY_DO_NOT_RUN`: RGB+leftIR pseudo-stereo, 7.36ms/online-td experiment, inline mkv/NVENC capture, failed fixed-Z and manual gyro candidates.

## Recording classification

- Initial inventory: 130 session dirs, 120989 files, 308050589221 bytes.
- Kept in active `recordings/`: 18 current RSUSB PASS sessions (104.620GiB) + 21 critical regression/calibration/safety evidence sessions (62.946GiB).
- 91 superseded or re-recordable sessions (116.336GiB) atomically moved to `/home/robot/ego_vio_recordings_legacy_quarantine_20260816` and excluded from Jazzy transfer. The final 10 moved sessions total54.992GiB; their existing reports remain and they can be restored before final deletion.
- Full frozen classification: `JAZZY_HANDOFF_20260816/LEGACY_QUARANTINE_MANIFEST.tsv`. Do not finally delete quarantine until SSD bundle SHA verification passes.

## Product truth at handoff

- Capture path has old-host zero-drop 30fps/400Hz evidence.
- Auto-loop is still candidate, not customer-ready: historical declared positive loops stable pass only 5/12 runs; complete hidden action/truth matrix is missing.
- Dynamic world-Z is not solved. Fixed rotation fails cross-session generalization; causal depth-plane factor remains safely disabled because inventory lacks a real horizontal-plane positive sample.
- ORB-SLAM3 core path referenced by old memory is absent on old host; only wrapper/backups remain. Do not claim it was transferred.

## Verification evidence

- Targeted runtime/calibration tests: 7 passed.
- Full official test directory with Humble environment: 180 passed, 1 existing Matplotlib Axes3D warning.
- Migration verify script passed an independent small fixture (stats + three git fsck + SHA256).
- Three repos have local branch `backup/pre-jazzy-separation-20260816`; whole dirty worktrees and untracked files remain authoritative and are copied.
