# 25-recording bidirectional MASt3R development regression

User request: jointly optimize symmetric independent left/right learned IR
frontends over the 25 dynamic recordings from Sep 27–30; empirically decide
whether correction limits are needed. No Tracker/robot supervision in SLAM.

## Fixed contract

- All 25 are development/regression data, NOT a new blind validation set.
- Keep each recording's factory calibration and the frozen scoring reference.
- Docker2 timing: td=-0.009109323, estimate_td=0, replay IMU shift=0.
- Score body/IMU-origin outputs, SE(3), no fitted scale or error-based trimming.
- Keep original products and production wrapper unchanged until regression passes.
- Failures remain in the denominator; don't exclude a difficult recording merely
  because its ATE is high. Raw/GT invalidity requires independent evidence.

## Baseline evidence

Symmetric v2 cached take2/4/6 requests 11.937/17.199/13.553 mm correction.
Existing graph limit is 1000 mm, zero clipping. Old mono 25 mm clipping cannot
explain the symmetric take6 maximum of 15.352 mm.

## Work

1. Freeze manifest of 25 unique captures and audit all caches/reference bindings.
2. Reuse valid VINS/left/right artifacts; prepare missing right mono frontends
   once, with the same official model/configuration, onboard-only scale.
3. Freeze uncapped symmetric graph; compare post-solve global caps 10/25/40/100 mm
   and none. Test a smaller cap only as a diagnostic, not a proposed default.
4. Isolate both/left/right/stereo-only and residual-aware stereo-row weighting.
   No per-take or per-frame parameter selection using reference error.
5. Publish per-recording mean/P95/max/coverage, all-frame 10 mm success count,
   cap binding and source failure taxonomy. Do not call tuning a generalization test.
6. Back up code + small manifest/docs to sencang without force push; restore
   fetched artifact and compare changed content hashes.

## Fresh checks and pilot (2026-10-02)

- 129 targeted tests passed; independent code review found no blocking issue
  after right-cache provenance and scorer return-code classification fixes.
- Original symmetric maxima take2/4/6: 8.086 / 5.826 / 15.352 mm.
- Stereo-only control, identical graph: 8.296 / 5.973 / 8.398 mm.
- Own-stereo + cross-eye learned-edge consistency at 10 mm:
  8.134 / 6.077 / 14.014 mm. 15/25 mm bounds did not fix take6 either.
  This is NOT an accepted fix. Do not select a bound from these three alone.
- Original 25/40/100 mm correction caps are inactive on all three. Genuine
  CLI cap=10 mm matches projected cap within 1.31e-9 m (binding verified).
- Baseline source manifest remains intact. Separate recovery overlay preserves
  original failures and attaches four same-record onboard-only geometry caches
  with differing producer recipes, NOT four newly proven production passes.
- Recovery-aware complete left caches: 24/25. Sep29 take05 remains unresolved
  low translation excitation; it stays in the 25-record denominator.

## Active full-corpus job

Systemd user unit: `umi-dual-ir-regression-25-20261002.service`

Output: `.planning/dual_ir_regression_25_20261002/batch_v1/summary.json`

Frozen policies: both, left, right, stereo_only, residual_aware,
consistent_10mm, consistent_15mm, consistent_25mm.

No GT in graph inputs; reference scoring only after trajectory freeze.
Code/config hashes are frozen; don't edit solver/helper dependencies during
this job. Missing right frontends are computed once and reused across policies.
Preparation/graph failures continue to the next recording and remain counted.
Summary counts finished recordings separately from accuracy passes.

Status: full 25-record development regression running; optimization incomplete.
Production defaults unchanged; no all-25 or blind-test accuracy claim.
