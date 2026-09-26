# Lighthouse world calibration v13 (2026-09-26)

Status: ACCEPTED_FOR_NEW_EXTERNAL_VALIDATION.

Tracker serial: LHR-A2A59C7D. Lighthouse IDs: 2640831677 and 1850292303.
Frozen config: `libsurvive_config_frozen_v13.json`.
SHA-256: `8b2f50ed5bf51ee4366d155dd99983a7a2791b1de4b7995d375d5036b94668d0`.

## Evidence and acceptance

Source: `../lighthouse_recalibration_v13_20260926.MOdFGn/coverage_round2/`.
An explicitly prompted, broader motion capture produced an uncompressed
`lighthouse_raw.rec`, terminated by the outer 65 s SIGTERM timeout (rc 124),
with final solver stats and complete newline-terminated records. Two offline
replays exited 0, each obtained 8 scenes, 276 measurements and zero MPFIT
failures, and produced byte-identical candidates. Online/offline relative
lighthouse geometry differed by 1.110 mm / 0.0385 deg.

Raw record SHA-256:
`d45dfa1440731a850d439b34470a52f9272dfdb5833b0b633a5a4c5492a549f3`.

The first static test (20 s after 8 s warmup) FAILED: position P95/max
2.281/3.026 mm and orientation P95 0.448 deg. The cause of that transient
has not been established; preserve this failed test rather than discarding it.
An independent restart test (15 s after 8 s warmup) PASSED: position P95/max
0.107/0.128 mm, orientation P95 0.0876 deg, endpoint drift 0.087 mm,
129.31 Hz, zero timestamp regressions and zero intervals above 25 ms.
The two static centers differed by approximately 0.194 mm.

Independent dynamic acceptance (20 s, explicitly prompted) PASSED:
2587 samples, 129.29 Hz, max interval 12.239 ms, zero intervals above 25 ms,
zero timestamp regressions, zero detected translation/rotation pose jumps,
zero angular review events and zero invalid quaternions. Runtime lighthouse
configuration audit passed. This checks stream continuity, not absolute
dynamic position accuracy against an independent reference.

## Scope

The frozen relative lighthouse separation is 1759.141 mm. Compared with v12,
estimated relative geometry differs by 9.775 mm / 2.2672 deg. These estimated
geometry differences do not establish the physical movement of either station.
The previous claim of a proven 120 mm physical displacement was too strong:
fixed-point pose differences can also arise from placement, initial estimator
settling, and world-reference differences.

New preflights and recordings use v13. Historic recordings retain their own
world configuration. Tracker-to-UMI rigid extrinsics and time offset are
unchanged. This calibration acceptance is not a 10 mm SLAM precision result.

The earlier, narrow-coverage round (2 scenes, 74 measurements, recovered gzip
tail) is retained for diagnosis and is not the frozen candidate.
