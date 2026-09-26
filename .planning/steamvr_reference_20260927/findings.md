# Evidence and constraints

SteamVR 2.17.10 build 25330290, official OpenVR SDK commit
0924064316de3effbcd1acf1e309182a2deb1c05. Physical GenericTracker LHR-A2A59C7D,
tracking references LHB-6E49384F and LHB-9D67E4BD. Connected and good 600/600
at 120 Hz host queries. No sampling-rate or absolute accuracy claim.

Standalone runtime collector:
reports/steamvr_reference_setup_20260927/steamvr_pose_probe.cpp and SDK header.
Standing origin, zero added future prediction, host monotonic begin/end and
wall timestamp, physical serial/class selection. Wrong serial and invalid
duration negative checks passed. -Wall -Wextra -Werror build passed.

30-second static metrics: P95 0.83 mm after first 5 seconds, valid max 4.88 mm,
all-state max 4.98 mm, mean first/last-5s shift 0.223 mm. State 101 once.
USER SUBSEQUENTLY CONFIRMED TOUCHING THE DEVICE AT THE EXCURSION.
Therefore INVALID_STATIC_TEST_OPERATOR_CONTACT, not evidence of base movement.

No libsurvive world/extrinsic, Docker2 td, UMI SLAM config, GPU driver changes.
Never run libsurvive and SteamVR on these same USB devices concurrently.
SteamVR local pose origin may differ: old libsurvive extrinsic/td cannot be
silently accepted. Static poses cannot identify dynamic timing offset.

Primary documentation:
https://github.com/ValveSoftware/openvr/wiki/IVRSystem::GetDeviceToAbsoluteTrackingPose
https://github.com/ValveSoftware/driver_hydra#using-the-hydra-driver
