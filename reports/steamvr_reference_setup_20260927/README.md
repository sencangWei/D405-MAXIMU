# SteamVR official Tracker connection probe — 2026-09-27

PASS for connection only, not position accuracy. SteamVR 2.17.10 (build 25330290),
physical GenericTracker LHR-A2A59C7D, bases LHB-6E49384F / LHB-9D67E4BD.
600/600 queries over 5 seconds: connected, pose-valid, Running_OK (200).
No regressing query timestamps, nonfinite poses, or exact consecutive pose duplicates.
See connection_summary.json and connection_probe.csv. Motion was not controlled;
do not interpret the sample as a static-precision test.

## Startup findings and bounded changes

SteamVR was initially blocked by its GUI superuser request for CAP_SYS_NICE on
vrcompositor-launcher. The operator completed the official prompt locally.
USB 28de:2101 interface 0 and 28de:2300 interfaces 0/1/2 had no kernel driver;
no process held the USB device files. A libusb_attach_kernel_driver call restored
usbhid on those exact four interfaces, with all return codes 0. No USB reset,
firmware update, or base-station command was sent. Driver logs then identified
the correct serial and opened its IMU/optical HID interfaces.

The existing SteamVR config was copied to steamvr.before_headless.vrsettings.
After gracefully stopping vrmonitor and confirming vrserver exited, only
steamvr.requireHmd=false and steamvr.activateMultipleDrivers=true were added to
the user config. No null/synthetic HMD driver or synthetic Tracker was enabled.
The installed default config was not edited. On restart the physical Tracker
and both tracking references were observed through OpenVR.

vrcmd --background --info crashed (signal 11) without a physical HMD; this is
not evidence that the pose service failed. The isolated SDK probe avoids HMD
properties and verified physical Tracker poses instead.

Existing libsurvive world, D405 calibration, UMI SLAM configs and NVIDIA drivers
were not changed. Do not run libsurvive and SteamVR on the same devices together.
Switching backends requires validation of Tracker local-frame semantics and
time alignment; do not silently reuse the libsurvive external transform/td.

## Reproduce

The included openvr.h and LICENSE are from ValveSoftware/openvr commit
0924064316de3effbcd1acf1e309182a2deb1c05. Link to the installed official runtime:

```bash
cd /home/robot/ego_vio_humble/reports/steamvr_reference_setup_20260927
g++ -std=c++17 -Wall -Wextra -Werror -O2 -I sdk_09240643 steamvr_pose_probe.cpp \
  -L /home/robot/.steam/debian-installation/steamapps/common/SteamVR/bin/linux64 \
  -Wl,-rpath,/home/robot/.steam/debian-installation/steamapps/common/SteamVR/bin/linux64 \
  -lopenvr_api -pthread -o steamvr_pose_probe
./steamvr_pose_probe LHR-A2A59C7D 5
```

This reads Standing-universe poses with zero additional future prediction.
The API still returns SteamVR's current estimated state, not raw optical samples.
Timestamps bound the host query; they are not sensor acquisition timestamps.
120 Hz is the requested query rate, NOT an asserted unique sensor update rate.
Success requires >=95% connected+valid+Running_OK samples and successful output.
All queried rows including invalid states are retained. Exit 2 for invalid CLI;
exit 1 for failed init, absent physical target, output error, or failed validity.

## First operator-confirmed 30-second stationary test

CORRECTION: the operator subsequently reported touching the device at the
excursion. Verdict is INVALID_STATIC_TEST_OPERATOR_CONTACT, not a hardware
stability failure. The stationary precondition was interrupted; do not use
this window to accept or reject the reference. Preserve the original metrics:
frozen P95 <=1 mm, max <=3 mm, centroid shift <=3 mm, valid-running fraction
>=99.9% thresholds. 3599/3600 were connected,
valid and Running_OK. After 5 seconds P95 0.8268 mm, maximum 4.8766 mm;
first/last 5-second centroid shift 0.2230 mm. Whole-window P95 0.7215 mm.
The one non-running sample is retained: state 101 (Calibrating_OutOfRange),
sequence 3405 at 28.3751 seconds; its position deviation is 4.9781 mm against
the same steady median. 31 raw rows exceed 3 mm, in intervals 22.800–22.850,
28.192–28.375 and 28.408 seconds. No temporal smoothing or removal was applied.
See static_30s.csv and static_summary.json. No explicit optical cause was
reported in vrserver's contemporaneous log. Do not assert physical base
movement, reflection, or operator movement from these values alone.

## Three untouched, charging stationary retries

All 3 x 60-second windows passed the same frozen static thresholds. Each had
7200/7200 connected+valid+Running_OK rows and no timestamp regressions.
P95 = 0.2515 / 0.2556 / 0.2308 mm; maxima including startup/all states =
0.4993 / 0.4830 / 0.4575 mm. First/last 5s centroid shift =
0.1037 / 0.0490 / 0.0251 mm. Between-take centroid offsets from take 1 =
0 / 0.0377 / 0.0233 mm. All samples retained without smoothing or spike repair.
See static_retry_take1/2/3.csv and static_retries_summary.json.

The probe now line-buffers output and gracefully closes on SIGTERM/SIGINT,
for readiness/cleanup of synchronous camera capture; maximum duration 600s.
Existing static recordings were collected before this I/O-only change.

Next: independent fixed-board motion validation and SteamVR-local-frame/UMI
clock calibration. Static success does not prove workspace-wide/dynamic accuracy.
Do not use SLAM residuals to tune the reference and do not claim 10 mm accuracy
from this connection check.

Official API semantics:
https://github.com/ValveSoftware/openvr/wiki/IVRSystem::GetDeviceToAbsoluteTrackingPose
Official no-HMD configuration guidance:
https://github.com/ValveSoftware/driver_hydra#using-the-hydra-driver

## Synchronous D405 + IMU + official Tracker acquisition

New entry point (no libsurvive reader, no SLAM supervision):

```bash
cd /home/robot/ego_vio_humble
rtk proxy python3 scripts/capture_steamvr_with_d405.py \
  --duration 40 --label steamvr_aprilgrid --preview --guided
```

Wait for `正式采集开始：40 秒` before moving. Keep the original 6x6,35.2mm
AprilGrid fixed, UMI/Tracker rigidly attached and both bases visible. Move with
translation and multiple rotation axes as the guided preview requests.
The existing independent board calibration pipeline consumes the adapted
tracker.csv using host_monotonic time. Solve a NEW official-backend tracker_T_body
and independent IMU/Tracker query offset; old libsurvive transforms/td are not
accepted as replacements. A second motion take is needed for held-out dynamic
validation before using the transform as SLAM evaluation ground truth.
No extrinsic or time offset has been estimated from tonight's static data.

The wrapper preserves complete official raw traces and records explicitly
which camera-exposure interval plus bracketing queries is checked. Every
in-window invalid state/gap remains subject to the unchanged adapter gates.
Startup and post-recording DB3 analysis are not calibration acquisition.
Device timestamps are NOT fabricated from host query timestamps.

Live checks:
- 06:15:34 first smoke FAIL: camera frame timeout after ~9s, raw retained.
  The physical/software cause has not been identified; retries do not prove
  that this camera fault is fixed.
- 06:17:03 camera retry PASS with 449 frames. Whole-trace Tracker gate initially
  failed because of startup/post-processing gaps; within exposure interval
  max gap12.95ms, no invalid states.
- 06:19:03 fresh15s complete capture PASS: 449 matched camera sets, zero drops;
  IMU400.044Hz,1793/1793 normal Tracker queries,max gap10.333ms;
  every camera exposure bracketed. Status PASS_CAPTURE_ONLY_NOT_CALIBRATED.
  Evidence reports/steamvr_umi_sessions/20260927_061903_steamvr_static_smoke_verified.
- 06:20:22 40s confirmation FAIL: camera1199sets PASS, IMU399.986Hz;
  one in-acquisition Tracker query gap119.857ms. Every returned pose was normal.
  No threshold relaxation or interpolated repair applied.
- 06:23:23 instrumented40s confirmation PASS:1199sets, IMU400.009Hz;
  4792/4792 normal Tracker queries,max gap9.811ms,100% camera exposure coverage.
  Post-recording stdout writes sometimes blocked20–82ms. This establishes a
  write-path risk, not proof that every earlier gap had that same cause.
- Tracker stdout is now RAM-staged in a private mkstemp file, exclusively
  copied to its durable raw artifact and SHA256-compared before removing the
  exact temporary copy. Interrupted captures also preserve partial raw data.
  This reduces disk-write coupling, but cannot guarantee OS scheduling latency.
- 06:26:33 final RAM-staged40s complete capture PASS:1199camera sets,
  IMU399.985Hz,4793/4793 normal Tracker queries,max gap9.876ms;
  100% exposure coverage, raw copy SHA256-verified. Probe diagnostics had
  no >20ms output stalls in this take. Review found no remaining code blocker.
  Evidence reports/steamvr_umi_sessions/20260927_062633_steamvr_ram_staged_validation.
- Targeted Python regression suite23 PASS; bundled C++ probe compile passes
  -Wall -Wextra -Werror. Old-reader contention guard reviewed/fixed.

After a NEW moving board capture passes, the existing offline solver command is:

```bash
rtk proxy bash scripts/calibrate_lighthouse_aprilgrid_session.sh \
  /home/robot/umi_ego_vio_data_device2_c48df736/recordings/ACTUAL_NEW_D405_SESSION \
  /home/robot/ego_vio_humble/reports/steamvr_umi_sessions/ACTUAL_NEW_CAPTURE/tracker.csv \
  /home/robot/ego_vio_humble/reports/steamvr_umi_sessions/ACTUAL_NEW_CAPTURE/official_board_candidate
```

Use the paths from the NEW capture_manifest.json, not tonight's stationary
smokes. Keep that manifest with the candidate for backend/frame provenance;
the legacy solver's frozen_manifest schema alone does not establish official
backend identity or held-out dynamic accuracy. Do not install this candidate
as evaluation GT until a separate motion take passes independent validation.
