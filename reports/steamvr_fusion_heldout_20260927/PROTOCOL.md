# Independent heldout recordings after the stage-aware rescue fix

Frozen before new acquisition. Existing two captures were used to diagnose and
validate rescue routing; they are development data, not these heldout takes.

- Record at least two independent 40-second slow-motion takes with the original
  D405 raw DB3 + paired IR/RGB ~30 Hz + external IMU ~400 Hz + official SteamVR
  reference. Preview enabled; do not move until formal start is announced.
- Include translation, vertical motion, and slow multi-axis turns. Tracker/UMI
  mount and bases must remain unchanged; operator confirms readiness separately.
- Use fusion-guarded without per-take tuning. VINS runs only as internal fusion
  input. No separate standalone VINS comparison is requested.
- Same checkpoint, baseline/rescue presets, formal td -0.009109323 s,
  estimate_td=0, replay IMU shift 0, and frozen official reference manifest.
- Tracker only enters scoring after complete internal PASS. SE(3), no scale;
  translation RMSE/P95/max <=10 mm, rotation RMSE <=2 degrees, timestamp
  overlap >=98%. Every valid initialized SLAM output is evaluated. Report
  initialization coverage separately; do not remove high-error frames.
- Preserve failed recordings/runs and separate recording, reference, runtime,
  and algorithm failures. If the target/reference is unavailable, do not record
  an unsupported precision trial or silently change calibration.

## Frozen source/config SHA256

- workflow: 66e592af485398c7e94099dbc254bd0fa19266437c35e5cd6721e7330eb9d60a
- guarded selector: 4f8373ec2d47394c01132a31b31893553bc64c0e64c53c6486b9b56f3d7c4e76
- camera prior builder: 21493571d37797dd2ca70bce47323d1a2019f7e5c5c52b15b0c3d1e027687d18
- baseline preset: ac2577063dce6f017307f2bbe45485cb272f84b152eec86d9cb31962f9d4d84a
- rescue preset: 1c306b8ae0a982ba7a468e2a50b3fe7013dda9421c22782c80f53055c9287c8a
- official reference: b5ac5b8f639f55b9c99e81837fe32224fa3ab806177b8067bf14a2bf43490439

## Initial preflight

Result: BLOCKED_REFERENCE_TARGET_ABSENT. Formal capture NOT started.
SteamVR vrserver PID3550529 is running. No libsurvive reader conflict and no
running Docker containers. USB 28de:2101 Watchman receiver enumerates, but the
official pose probe cannot find physical GenericTracker LHR-A2A59C7D (exit1).
This does not establish whether the Tracker is powered off, asleep, unpaired,
or otherwise disconnected, and does not establish physical base movement.
Connection log/CSV retained in this directory. Available RAM staging ~5.5 GiB
and persistent disk ~1.3 TiB; 40-second dry-run valid (~5.09 GiB camera staging).
No reset, firmware, service restart, new extrinsic/time fit, or algorithm edit.

Next required input: operator powers/awakens the Tracker, confirms green and
unchanged rigid mount/bases; repeat identity/validity preflight, then confirm
readiness for explicitly announced 40-second acquisition.
