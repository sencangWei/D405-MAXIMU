# Shared-landmark stereo/gyro window prototype — 2026-09-28

**Status: observation prototype, NOT production SLAM / NOT all-ten ATE PASS.**
Production scripts, calibration, recorded frames and old reports are unchanged.
No SteamVR, robot TCP or learned positions enter this estimator or its admission.

## What changed

New isolated files `scripts/stereo_window_bundle.py` and
`scripts/prepare_stereo_window_observations.py`. Several stereo pairs share
variable XYZ landmarks; camera pose and a constant gyro bias are jointly fitted
to left/right pixels plus soft raw-gyro preintegration. Metric baseline is read
from each recording. Formal td=-0.009109323 is applied once. Raw gyro derivative
is camera-frame/right-tangent and first-order, not exact bias reintegration.

This is new raw observation information, not a sweep of closed14 graph weights
or trajectory smoothing. No MASt3R/VINS frontend, neural model or GPU rerun.
Full recorded30Hz frames/trajectories are retained; these sampled windows only
test the new candidate constraints.

## Frozen controls

Ten recorded sessions, five uniformly preselected windows at10/30/50/70/90%
of each camera timeline. Each window uses five pairs spanning20frame intervals
(roughly0.667s). Every fifth track is withheld before training-only PnP and BA.
Withheld source stereo depths predict pixels in subsequent views; no withheld
pose fitting. This checks geometric consistency, not calibrated accuracy.

- `controls_ten_v1`:36/50 solved;26 heldout-pixel improvements. Initially
  insufficient output consistency gates and all-track extraction PnP; retain as
  diagnosis only. Its supplied-Jacobian `exact=True` label was wrong; corrected
  in later code without changing the bias solver formula.
- `controls_ten_v2`:31/50 accepted after consistency guards and holdout repair;
 25 improvements. Revealed gross temporal correspondences (P95 up to414px).
- `controls_ten_v3`:37/50 accepted,27 withheld-pixel improvements; median
 withheld-pixel RMSE0.552→0.432px. PnP geometric inliers now admitted per view
 rather than discarded during initialization and reinstated in BA.
- `controls_ten_v4_manifest`:same solver/admission, added input/frame and scoring
  source hashes. All37 endpoints repeat v3 bit-for-bit; all13 rejections repeat.
  This is the final reproducibility artifact; no accuracy policy changed.

The repair is per-observation geometric admission at the existing2px PnP
threshold. Source stereo remains; a point may remain valid in other views.
No input frames are removed. No parameter/threshold sweep or GT-selected windows.

## AFTER-estimation local external verification

`controls_ten_v4_manifest/local_displacement_evaluation.json` uses the existing accepted
official body reference and fixed body→leftIR extrinsic; no new td/SE3/scale fit.
Only local endpoint differences are measured, after all observations are frozen.

37 evaluated windows:26 improve relative to their image-only PnP initialization.
Local displacement median0.721→0.392mm; maximum10.425→3.332mm.
**These values are NOT the fused whole-trajectory ATE.** Initialization is not
the production baseline.13 rejected windows remain and are not scored as zero.

| Case | Accepted windows /5 | Local comparisons improving |
|---|---:|---:|
| dev1 |4|1|
| dev2 |5|4|
| heldout1 |3|2|
| heldout2 |4|4|
| heldout3 |5|3|
| heldout4 |4|4|
| fresh1 |2|2|
| fresh2 |3|3|
| fresh3 |3|1|
| fresh4 |4|2|

## Remaining limitations / next decision

1. The13 rejected windows are not evidence of damaged recordings. Low common
   stereo support and training initialization can limit this estimator.
2. SGBM both seeds/gates stereo LK; seeded reverse closure is not independent
   matching evidence. Longitudinal depth noise can reject valid temporal points.
3. Small source-depth bias remains variable and is synthetically recoverable;
   large bias may fail admission. Diagnostic/noise gates are not covariance.
4. Uniform windows do not establish coverage of original fresh2/fresh4 peak
   blocks. Need coverage/consistency analysis before any full graph integration.
5. No all-ten full-trajectory rerun with these constraints yet; prior frozen
   candidate still8/10 with max11.323/15.333mm on the two failures. Do not claim
   a10mmSLAM success or replace production.

## Verification

140 targeted old/new stereo/fusion tests passed; new-only31 passed. Syntax checks
and clean restore verification are recorded separately in the planning folder.
No live hardware, full raw-data replay, or production deployment acceptance.

Commands (output must be a new directory, never overwrite old evidence):

```bash
cd /home/robot/ego_vio_humble
rtk proxy /home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/.venv/bin/python \
  .planning/metric_window_bundle_20260928/run_observation_controls.py \
  --output reports/metric_window_bundle_20260928/new_controls
rtk proxy /home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/.venv/bin/python \
  .planning/metric_window_bundle_20260928/score_window_controls.py \
  --controls reports/metric_window_bundle_20260928/new_controls \
  --output reports/metric_window_bundle_20260928/new_controls/local_evaluation.json
```
