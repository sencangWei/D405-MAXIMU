# Lighthouse world repair (offline, candidate only)

## Goal
Identify/fix conflicting two-station optical constraints, without SLAM supervision, changing Tracker–UMI rigid mounting, or overwriting production calibration.

## Phases
1. Reconstruct exact libsurvive flags/configuration and calibration evidence — complete.
2. Recompute one candidate from saved raw observations; require full optical coverage and genuine calibration — complete as diagnostic: four scenes/124 measurements, limited coverage, NOT accepted.
3. Replay baseline/candidate on diagnostic capture and independent saved captures — complete available A/B: current improved, earlier worsened; same-layout premise unproven. Only two raw records available, earlier hand-eye rounds cannot replay. Two independent same-layout validations unavailable.
4. Preserve outcome and verified backup — in progress. Candidate inactive. Need operator-ready broad-coverage capture + two independent validations before activation.

## Success criteria
Both station optical P95 residuals decrease toward individual-station fits; reject large position/rotation jumps rather than interpolate them away. Two independent captures required before recommending activation. Continuity is not absolute accuracy; no claimed 10 mm accuracy without independent validation. Only use same-station-layout records.

## Constraints
Frozen v13 master read-only; private configs for every libsurvive process. No USB/live stream, production writes, hand-eye/time changes or SLAM processing. Do not use camera/robot trajectories to fit Tracker world poses. Never bulk-stage reports, never force-push.

## Errors
- Narrow explore agent could not start: configured spark model unavailable for current account. Continue lookup locally, do not change model settings.
- Earlier lookup globs found no .sh files in coverage directory; use README/source evidence.
- Looked for obsolete survive_cal.c filename; calibration actually lives in driver_global_scene_solver.c and poser_mpfit.c.
- Unfiltered session-log lookup produced excessive text; use direct source/README instead.
