# Lighthouse partial-observation repair candidate (2026-09-27)

Status: IMPLEMENTED AND REPLAY-TESTED, NOT ACCEPTED FOR PRODUCTION.
This is reference-tracker repair, not SLAM tuning. No camera/AprilGrid/robot
trajectory is consumed by the tracking algorithm. Board poses only evaluate it.

## Change

Opt-in `mpfit-partial-observation-cov-scale=16` preserves full correlated
MPFIT covariance and inflates it for weak observations. Default 0 unchanged.
Requires `mpfit-use-cov=1` and nonadaptive observation covariance (verified in
these records); disabling covariance will reject the partial solve, not
silently substitute a fabricated covariance.
Partial fallback requires initialized tracker (>=16 observations), two known
stations, >=3 axis groups each supported by >=3 measurements. Single-station
updates remain rejected. It only bridges 20 ms to 1 s since a full observation.
Calibration-intent solves cannot take the exception. Admission, covariance
inflation and last-full timestamp share the same classification.

The covariance multiplier and bridge bounds are experimental heuristics,
not a proven sensor-noise model or a guarantee of 10 mm accuracy. They have
not been automatically optimized/selected against the board.

## Reviewed bridge result (same baseline SE3, unchanged external/time/scale)

| Capture | Baseline mean/P95/max mm | Candidate mean/P95/max mm |
|---|---|---|
| Previous independent board | 5.046 / 12.090 / 34.634 | 5.193 / 13.265 / 26.723 |
| New orientation-control board | 4.104 / 9.960 / 55.532 | 3.781 / 9.249 / 13.778 |

Both reuse all baseline valid samples (876 and1181); no precision-based
sample removal. New-board original55.532mm peak now6.875mm; remaining worst
occurs25.159s after camera start (baseline8.204mm, candidate13.778mm).
Old-board12s block remains (candidatepeak26.723mm). This is real gap mitigation
but NOT <=10mm acceptance, and old-board mean/P95 worsened. Do not deploy it.

Earlier eager partial updates (`board_precision.json`) and debounce-only
(`starvation_only/`) are experimental comparisons, not the final reviewed
implementation (`reviewed_bridge/`). No per-record algorithm parameters.

## Verification

- C helper tests: opt-out, initialization, one-station, insufficient axes,
  no-gate/full-support and nonfinite covariance scale all reject fallback.
- Isolated CMake build succeeds; installed libsurvive hash remains
  e09e6fb27b30dfc1b15a4a61c0eb4ae69ffb8e691dff222f534afcff7be89882.
- Default-off new-board pose values bitwise identical to original baseline.
- Three further raw records plus both board records: zero invalid values;
  filtered jump flags0/5. Raw jump counts are NOT mm-accuracy claims.
- Review fixed calibration-path leakage and inconsistent support predicates.
  No live acceptance done; production world/external/time/library untouched.

## Reproduction and remaining boundary

`validate_partial.py` runs isolated replay with frozen joint-world candidate,
audits startup/config/support guards and reports board comparison. Set
`PARTIAL_VALIDATION_OUT` to a new directory when rerunning; existing runs are
not overwritten. Requires recorded inputs stored on this machine.

Next: trace weak optical geometry/bias on the old-board12s block and the new
post-update bias at25.159s. Avoid another blind covariance/time/weight sweep:
partial covariance already reaches Kalman, so increasing its multiplier
alone cannot make a biased solve unbiased. A reference-world/sensor model
or subspace measurement correction needs new internal evidence first.

Code actually lives in /home/robot/.local/src/libsurvive (upstream-owned
remote). `libsurvive_partial_update.patch` includes the earlier four-way gate
plus this candidate/header; the existing optical-timing diagnostic is separately
backed up in reports/lighthouse_prediction_audit_20260927/. Never add unrelated
survive_process_gen2.c edits or install the candidate to production implicitly.
