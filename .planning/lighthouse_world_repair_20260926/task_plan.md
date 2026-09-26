# Lighthouse world repair (offline, candidate only)

## Goal
Identify/fix conflicting two-station optical constraints, without SLAM supervision, changing Tracker–UMI rigid mounting, or overwriting production calibration.

## Phases
1. Reconstruct exact libsurvive flags/configuration and calibration evidence — complete.
2. Recompute one candidate from saved raw observations; require full optical coverage and genuine calibration — complete as diagnostic: four scenes/124 measurements, limited coverage, NOT accepted.
3. Replay baseline/candidate on diagnostic capture and independent saved captures — complete available A/B: current improved, earlier worsened; same-layout premise unproven. Only two raw records available, earlier hand-eye rounds cannot replay. Two independent same-layout validations unavailable.
4. Operator-ready fresh capture — complete: bounded65s, six scenes, limited effective coverage. Two repeated offline candidates byte-identical. Own capture improves, independent diagnostic still fails.
5. Private publish-threshold A/B — complete: control reproduced canonical baseline; smaller threshold improves own optical residuals but not independent validation. Experiment rejected for deployment. Installed artifacts unchanged.
6. Preserve outcome and verified backup — local commit5d822f90; clean-base patch restoration verified. Remote push blocked: TLS EOF via configured proxy(normal and HTTP/1.1), bounded direct fallback timed out, proxy curl independently confirms SSL EOF. Remote backup NOT completed. Candidate inactive. Next work is multi-record optical/coverage/filter diagnosis, not immediate repeat live capture.

## Success criteria
Both station optical P95 residuals decrease toward individual-station fits; reject large position/rotation jumps rather than interpolate them away. Two independent captures required before recommending activation. Continuity is not absolute accuracy; no claimed 10 mm accuracy without independent validation. Only use same-station-layout records.

## Approved next diagnostic slice
User approved four-step remedy; current slice is offline conflict localization, not deployment. Audit station ID/channel mapping and coordinate/quaternion boundaries from raw records and current source. Compare diagnostic/fresh residuals by station, axis, sensor and time, and align against raw optical counts using record time (not asynchronous CSV host time). Require reproducible identity/geometry/branch evidence before a production code change. Preserve complete raw observations; no deletion or GT interpolation.

## Constraints
Frozen v13 master read-only; private configs for every libsurvive process. No further USB/live stream without a fresh operator-ready cue; authorized capture completed. No production writes, hand-eye/time changes or SLAM processing. Do not use camera/robot trajectories to fit Tracker world poses. Never bulk-stage reports, never force-push.

## Errors
- Narrow explore agent could not start: configured spark model unavailable for current account. Continue lookup locally, do not change model settings.
- Earlier lookup globs found no .sh files in coverage directory; use README/source evidence.
- Looked for obsolete survive_cal.c filename; calibration actually lives in driver_global_scene_solver.c and poser_mpfit.c.
- Unfiltered session-log lookup produced excessive text; use direct source/README instead.
- Private libsurvive replay exited0 without plugin modules and generated no WM0 poses: explicitly invalid/excluded. Corrected with explicit SURVIVE_PLUGINS and matching installed modules in both A/B arms.
- Remote HTTPS push failed gnutls_handshake via configured proxy twice; direct fallback timed out and proxy curl confirms SSL EOF. No remote-backup claim; local experiment/report commit is preserved.
