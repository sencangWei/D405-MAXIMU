# SteamVR reference acceptance

## Goal

Provide official SteamVR physical Tracker poses and verified reference stability,
without changing UMI SLAM, supervising SLAM with GT, or inventing calibration.

## Phases

1. Install official SteamVR and fix startup/device discovery — complete.
2. Validate serial, class, connected/valid/Running_OK state — complete, 600/600.
3. Stationary acceptance — complete: 3 x 60 seconds after new operator
   placement/charging confirmation. Previous 30-second test invalidated by
   operator-disclosed contact, not a hardware failure.
4. Synchronous recording — 15-second stationary camera/IMU/Tracker smoke PASS;
   final RAM-staged40-second confirmation PASS. Frame semantics, time alignment and independent board dynamic precision —
   pending; needs observable motion with operator present, not static inference.
5. Backup exact code/evidence to sencang and restore verification — complete
   for probe (221a6ad2), in progress for current corrected static evidence.

## Stop / report conditions

If no-motion prerequisites hold, require >=99.9% good samples, steady position
P95 <=1 mm, maximum <=3 mm, first/last centroid shift <=3 mm. Report all states,
including out-of-range rows; do not smooth spikes or hide failed takes.
Query timestamps are not hardware timestamps; query Hz is not sensor Hz.
Do not declare complete infrared precision repair before dynamic checks.

## Errors and corrections

- steam_latest.deb root URL 404: corrected to official stable archive URL.
- Initial vrserver no HID devices: four exact Valve USB interfaces unbound;
  libusb kernel reattach succeeded without USB reset or firmware changes.
- HmdNotFound: only requireHmd=false / activateMultipleDrivers=true added to
  backed-up user config, no synthetic HMD or Tracker.
- vrcmd --info segfault: avoid HMD properties; isolated SDK pose probe works.
- Original static FAIL retracted after operator confirmed contact.
