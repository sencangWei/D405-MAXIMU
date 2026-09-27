# Pixel-level stereo/gyro window estimator candidate

User approved 2026-09-28: develop a UMI-only multi-frame metric candidate;
formal production remains unchanged. Goal: all ten frozen regressions max<10mm,
no regression of eight prior passes; fresh independent captures after freeze.

## Constraints

- No SteamVR/robot poses in estimation, edge selection, or confidence.
- No closed14 family sweeps, per-case tuning, data removal, or altered ATE gates.
- Fixed formal td=-0.009109323 once; body/leftIR frame semantics preserved.
- Read recording-specific stereo calibration, 30Hz full trajectory output.
- New information must be raw multi-frame stereo pixel observations / metric
  landmark geometry, not reweighted existing displacement edges.
- Preserve existing dirty work and source reports. No production promotion until
  frozen all-ten evidence. Same-day owned remote backup + restoration evidence.

## Phases

1. Complete: inventory existing BA/tracking/covariance code and avoid duplicate
   implementation. Review minimal mathematical contract and failure policy.
2. Complete (23 tests, review follow-up pending): synthetic contract tests before code: metric scale, rotation/lever,
   gauge fixation, finite/rank/cheirality guards, robust outliers, gyro soft not
   hard fixed; no-MASt3R-shape or GT dependency.
3. Implemented, NOT production-accepted: isolated minimal estimator using shared landmark multi-frame left/
   right pixel reprojection with soft calibrated raw gyro constraints. Use
   existing scipy/OpenCV, no new dependency or frontend/GPU rerun.
4. First controls complete; diagnosis in progress: uniform time-stratified window observation controls on ten cached
   recordings before any single-case full trajectory promotion. Raw data and
   optimizer diagnostics first; scoring isolated afterward.
5. Pending: only with supported metric observations, frozen graph integration
   candidate across all ten. Record any derived-confidence/interface effects.
6. Pending: review/tests, explicit evidence backup to sencang; verify restore.

## Stop/decision conditions

Observation-only improvement is not SLAM acceptance. Reject an estimator that
does not improve independent geometric consistency or is unobservable. Do not
run a large graph batch solely on a successful example. Record blocked data or
mathematical assumptions; ask only for meaningful new authority/input.

## Errors

- Explorer role unavailable (`gpt-5.3-codex-spark` account unsupported). Used
  installed executor role for the same bounded read-only lookup; no model sweep.
