# Tail-support diagnostic, not a production optimization

The development cohort remains the fixed five failure plus five passing cases
from September 27–30. Small cause experiments precede that regression; full25
and fresh independent recordings remain final acceptance checks. No failed
recording is discarded, no trajectory is cropped, and no precision gate changes.

## Current evidence and correction

The overlap40 experiment regressed Sep29 take02 from the seven-pair pilot's
16.820 mm maximum to 21.213 mm. This disproves density-only recovery; it does
not prove a covariance/count-normalization bug. Stereo factors already receive
IRLS downweighting (8 mm residual threshold). `downweighted_queries=0` describes
local-scale queries, not the absence of stereo robust weighting.

In the fixed ten-record cohort, saved frontends end early only for failing
Sep29 take02 (left 587/right 588 of 1199 frames) and passing Sep30 take03
(807/789 of 1199). Thus missing learned tail coverage cannot explain all
failures. Raw D405 inputs are complete; both frontends actually enter failed
relocalization, rather than encountering an exporter cap. Previously tested
previous-keyframe rescue restores coverage but can fail scale/rotation checks;
coverage is not evidence of an accurate recovered trajectory.

## Single controlled diagnostic

Reuse the exact saved overlap40 source reports, native candidates, calibration,
learned factors and solver settings. Optionally select new tail observation
pairs by their existing internal confidence, rejecting temporally overlapping
raw support intervals (including shared endpoints). Retain both eyes of each
selected pair and all original factors. Preserve all source observations and
selection reasons in diagnostics. Default `all` behavior remains unchanged.
No Tracker measurements participate in selection or estimation.

Independent critic approved the four-file implementation with no blocking
issues. Grouping can conservatively overestimate a pair's support when multiple
raw supports bind to it; this can reject extra candidates but cannot establish
false statistical independence. Fresh targeted suite: 43 PASS.

Actual comparison: Sep29 take02 and passing Sep30 take03, with fresh native
controls and complete 1143-sample timelines. Success requires improvement over
the earlier seven-pair failure pilot (mean 7.532/P95 14.143/max 16.820 mm),
not just the regressed overlap40 result, without damaging the passing control.
If unsuccessful, do not promote or launch fast10/full25 for this policy, and
close support-sampling as a proposed fix. Actual outcomes will be appended.

## Actual outcomes: reject this policy

| Record | Arm | New pairs | Mean mm | P95 mm | Maximum mm | Result |
|---|---|---:|---:|---:|---:|---|
| Sep29 take02 | fresh native control | 0 | 8.917 | 18.318 | 18.748 | FAIL |
| Sep29 take02 | nonoverlap | 7 | 10.460 | 23.223 | 23.685 | FAIL |
| Sep30 take03 | fresh native control | 0 | 3.005 | 5.956 | 9.869 | PASS |
| Sep30 take03 | nonoverlap | 8 | 4.128 | 10.475 | 12.974 | FAIL |

Both actual trials completed, retaining 1143 samples and timestamp overlap 1.0.
Controls reproduce preceding trials exactly. Estimate CSV hashes match saved
summary hashes. Take02 retained 13 eye observations / seven new pairs (752
total stereo rows); take03 retained 16 eye observations / eight new pairs (504
rows). Code guards passed; trial completion is not precision acceptance.
No crop, GT-supervised selection, threshold or solver parameter change occurred.

Actual two-arm runtimes were 275.462 and 273.336 seconds, run concurrently.
Each Python process had 47 threads; this scheduling context differs from prior
short runs, so their latency cannot be attributed to the sampling policy alone.
No new frontends or images were extracted. Avoid assuming parallel native
linear algebra is faster without bounding and measuring thread usage.

Summary SHA256:
- `timeline_gap_nonoverlap_trial_take02_v1/summary.json`:
  `bd9ef8d45a115ec1100bf97201e00535a9ca9e9b46003dbfab1b26f8d2aeb410`.
- `timeline_gap_nonoverlap_trial_take03_v1/summary.json`:
  `458d124b0df733534ad985946a9874375a3b4766a65d123888e2864a28df65f5`.

This policy is worse than both the fresh failure control and the previous
seven-pair pilot, and turns a passing control into a failure. Reject promotion;
do not run fast10/full25 for it. Neither temporal overlap nor density has been
demonstrated to explain/fix the main error. Close this geometry-only policy
route and return to the exact learned frontend failure frontier. Keep the
optional diagnostic code and evidence as a reproducible rejected experiment;
production remains unchanged. The maximum-ATE <=10 mm goal is still unmet.
