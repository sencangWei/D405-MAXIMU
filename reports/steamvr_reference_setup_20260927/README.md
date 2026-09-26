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

Next: operator-confirmed 30-second no-motion stability test; then independent
fixed-board motion validation and SteamVR-local-frame/UMI clock calibration.
Do not use SLAM residuals to tune the reference and do not claim 10 mm accuracy
from this connection check.

Official API semantics:
https://github.com/ValveSoftware/openvr/wiki/IVRSystem::GetDeviceToAbsoluteTrackingPose
Official no-HMD configuration guidance:
https://github.com/ValveSoftware/driver_hydra#using-the-hydra-driver
