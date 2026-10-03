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
