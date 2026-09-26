# Independent hand-eye audit

Goal: identify the cause of two-round Tracker–D405/body calibration instability without SLAM supervision or changing production calibration.

1. Reproduce current calibration and common-offset split failures — complete.
2. Compare board, Tracker and raw UMI gyro increments on exact split windows and multiple horizons — complete.
3. Check translation continuity and rotation-only versus coupled AX=XB fits — complete: round 2 has four position-step candidates, cross-sensor angular corroboration and larger Tracker–gyro axis-mapping inconsistency.
4. Preserve reproducible findings, apply only a confirmed surgical fix if justified, verify and back up any code capability — in progress: calibration entrypoint now rejects known step-contaminated reference before extracting/freezing. Physical/solver trigger still unresolved; do not claim accuracy fixed.

Stop condition: evidence-backed isolation of the failing measurement/model boundary; do not promise sub-10 mm absolute accuracy without independent validation. No new hardware capture without operator readiness. No calibration selected by SLAM ATE.
