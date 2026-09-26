# 2026-09-27 progress

- Steam installed by user; SteamVR installation verified by app manifest/files.
- User completed official first-start privileged GUI prompt locally.
- Read-only diagnosis isolated no headless mode and detached USB HID drivers.
- Four interfaces reattached (all rc=0), user settings backed up, headless
  settings applied, runtime gracefully restarted. Correct serial/two bases seen.
- Official SDK collector implemented in report directory, compiled and measured.
- Connection 600/600 and wrong-target/invalid-duration checks passed.
- Backup branch codex/steamvr-reference-probe-20260927 on sencang; commit
  221a6ad2 fetched/restored, 7 artifacts byte-identical and clean-tree compiled
  probe ran 120/120 good. Later commit 806be13e holds initial static evidence;
  corrected contact verdict pending update/verification.
- Operator then confirmed device was touched; corrected current JSON/README.
- New operator confirmation: placed stably, charging, visible to both bases.
- Three 60-second stationary retries PASS, 21600/21600 normal queries, maximum
  position deviation 0.4994 mm, between-take centroid spread under 0.04 mm.
- Official adapter and camera-only wrapper implemented; 20 targeted tests PASS.
- Review fixed old-reader detection (ps executable basenames instead of pgrep comm).
- First synchronous smoke FAIL: D405 stopped after ~9s; raw retained. Root cause
  not identified; do not claim a repaired camera fault based on later retries.
- Second camera capture PASS; initial whole-trace Tracker gap gate FAIL due
  startup/post-processing scheduling gaps, in-camera maximum 12.95ms.
- Explicit camera-exposure interval plus bracketing query selection added;
  full raw retained, invalid/gap rows inside the interval preserved. Thresholds
  unchanged. Fresh 15s end-to-end PASS: 449 camera sets, 400.044 Hz IMU,
  1793/1793 normal Tracker rows, max query gap 10.333 ms, exposure coverage100%.
- First40s confirmation: cameraPASS1199sets/399.986HzIMU but one119.857ms
  Tracker query gap => FAIL, correctly no usable reference export.
- Second40s diagnostic confirmation: PASS1199sets/400.009HzIMU,
  4792normalTracker queries,maxgap9.811ms,100%exposurecoverage.
- Probe instrumentation measured20–82ms stdout-write stalls after DB3capture.
  Private tmpfs stdout staging now copies/hash-checks after childcleanup;
  exact temporary copy removed only after matching durable bytes.23testsPASS.
  Final40s live RAM-staged confirmation PASS:1199sets,399.985HzIMU,
  4793normalTracker rows,maxgap9.876ms,100%exposurecoverage, no output-stall
  diagnostics. SHA256-verified durable raw copy; own children/containers exited,
  officialvrserver remains running. Code review no remaining blocker, LSP unavailable.
- Remaining: archive exact evidence and restore-verify backup.
  Motion-based reference/clock verification remains pending and cannot run
  while operator is asleep without already-recorded compatible dynamic data.
