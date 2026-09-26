# Independent hand-eye audit

Goal: identify the cause of two-round Tracker–D405/body calibration instability without SLAM supervision or changing production calibration.

1. Reproduce current calibration and common-offset split failures — complete.
2. Compare board, Tracker and raw UMI gyro increments on exact split windows and multiple horizons — complete.
3. Check translation continuity and rotation-only versus coupled AX=XB fits — complete: round 2 has four position-step candidates, cross-sensor angular corroboration and larger Tracker–gyro axis-mapping inconsistency.
4. Preserve reproducible findings, apply only a confirmed surgical fix if justified, verify and back up any code capability — complete: calibration entrypoint now rejects known step-contaminated reference before extracting/freezing. Commit 4ff408be pushed to sencang and all 17 changed files restored from fetched remote artifact with byte comparison. Physical/solver trigger still unresolved; do not claim accuracy fixed.

Stop condition: evidence-backed isolation of the failing measurement/model boundary; do not promise sub-10 mm absolute accuracy without independent validation. No new hardware capture without operator readiness. No calibration selected by SLAM ATE.

2026-09-26 17:05 operator-authorized raw-optical diagnostic completed. Camera/UMI PASS; Tracker FAIL (125.555 mm online step). Board 1199/1199, independent gyro/board corroboration rejects real-motion explanation for several jumps. Raw-optics recomputation reproduces large jumps without SLAM. Per-LH ablation removes large steps but yields incompatible single-station pose solutions (~174-degree disagreement), so no single-LH production fallback applied. Next boundary is dual-station measurement consistency / pose ambiguity, not a new hand-eye fit. Evidence: reports/lighthouse_umi_sessions/20260926_170502_raw_optical_diagnostic_v13/DIAGNOSTIC.md.
