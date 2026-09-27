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
  This reproducibility artifact repeats v3; no accuracy policy changed.
- `controls_ten_v5_fullrate_tracking`:all intermediate recorded frames propagate
  LK, unchanged five BA nodes.40/50 accepted; local median0.763→0.661mm,
  max11.408→3.714mm. Only18/37 shared v4windows improve: not a universal gain.
- `controls_ten_v6_joint_geometry`:review found the transplanted per-node20
  inlier gate inappropriate for joint initialization. Fixed EPNP minimum4 plus
  per-node non-collinear geometry, followed by unchanged joint consistency
  gates. No supportthreshold sweep. This is the final observation prototype.

The repair is per-observation geometric admission at the existing2px PnP
threshold. Source stereo remains; a point may remain valid in other views.
No input frames are removed. No parameter/threshold sweep or GT-selected windows.

## AFTER-estimation local external verification

`controls_ten_v6_joint_geometry/local_displacement_evaluation.json` uses the existing accepted
official body reference and fixed body→leftIR extrinsic; no new td/SE3/scale fit.
Only local endpoint differences are measured, after all observations are frozen.

46 evaluated windows:36 improve relative to their image-only PnP initialization.
Local displacement median1.005→0.878mm; maximum41.804→3.714mm.
**These values are NOT the fused whole-trajectory ATE.** Initialization is not
the production baseline.4 rejected windows remain and are not scored as zero.

| Case | Accepted windows /5 | Local comparisons improving |
|---|---:|---:|
| dev1 |5|4|
| dev2 |5|3|
| heldout1 |3|2|
| heldout2 |5|4|
| heldout3 |5|4|
| heldout4 |5|4|
| fresh1 |4|4|
| fresh2 |5|4|
| fresh3 |4|2|
| fresh4 |5|4|

## Original error-block coverage

Source features180/180 in both original blocks. Fresh2w3 persistent177, five
BA valid177/169/156/102/38, PnP inliers129/101/64/18. Fresh4w5 persistent133,
BA valid133/116/89/46/43, PnP inliers75/35/19/16. The20point barrier was ours,
not proof of bad raw data. Joint-geometry repair yields local comparisons:

- fresh2w3 (589→609):initial41.804→optimized2.536mm.
- fresh4w5 (1068→1088):initial6.522→optimized2.881mm.

Existing graph residuals already consume full metric vectors, but weighting/
local-scale machinery depends on learned-unit scale. Next use a distinct metric
window factor interface, not fabricated MASt3Rscale metadata or oldweight sweeps.

## Remaining limitations / next decision

1. The4 rejected windows are not evidence of damaged recordings (heldout1w2/w4,
   fresh1w4, fresh3w4). Low common
   stereo support and training initialization can limit this estimator.
2. SGBM both seeds/gates stereo LK; seeded reverse closure is not independent
   matching evidence. Longitudinal depth noise can reject valid temporal points.
3. Small source-depth bias remains variable and is synthetically recoverable;
   large bias may fail admission. Diagnostic/noise gates are not covariance.
4. Both original blocks have usable local measurements. The later isolated
   all-ten graph result is below; local window error alone was not acceptance.
5. Full-trajectory goal remains NOT met after first integration:9/10PASS,
   fresh4max14.983mm. Do not claim universal10mmSLAM or replace production.

## Verification

146 targeted old/new stereo/fusion tests passed; new-only37 passed. Syntax checks
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

## Phase5 — frozen full-trajectory graph integration (latest)

New wrapper `scripts/fuse_mast3r_metric_windows.py` adds accepted raw metric
endpoint vectors with explicit source `stereo_window_bundle_v1`, no per-edge
learned scale, no rotation factor. Existing stereo sigma4mm is a fixed uncalibrated
model assumption; old confidence/IRLS/priors/scale/caps/parameters unchanged.
Source identity, all timestamps, full factory calibration, fixed pixel/gyro/bias
guards, input/source hashes are verified. Production native source untouched.

Allten `graph_ten_v1` completed, including the same downstream fusion/smoothing
and official scoring. Reference CSV bytes, thresholds, SE3-no-scale alignment,
samplecounts and interpolation rules match previous frozen candidate.

| Case | Previous max mm | New max mm | New mean mm | Result |
| --- | ---: | ---: | ---: | --- |
| fresh1 | 7.809 | 7.772 | 2.490 | PASS |
| dev1 | 6.458 | 6.454 | 2.721 | PASS |
| dev2 | 8.271 | 8.261 | 3.715 | PASS |
| heldout1 | 5.921 | 5.922 | 2.474 | PASS |
| heldout2 | 5.974 | 5.967 | 3.408 | PASS |
| heldout3 | 6.831 | 6.838 | 3.763 | PASS |
| heldout4 | 8.913 | 8.960 | 3.375 | PASS |
| fresh2 | 11.323 | 8.837 | 3.475 | PASS |
| fresh3 | 6.310 | 6.305 | 1.535 | PASS |
| fresh4 | 15.333 | 14.983 | 5.936 | FAIL |

8→9PASS, zero lost prior passes. fresh4P9510.102mm,94.658%≤10mm. Some
already-passing maxima/means increased slightly, so this is not monotonic
improvement at every point. Both failure and passing data retained.

Evidence: `graph_ten_v1/comparison.json`, per-case `official_score/precision.json`,
frozen command/hash manifests and `window_residuals.json`. Allten rotatable
`metric_windows_3d.html` plots contain officialreference(white), frozenbaseline
(gray), newcandidate(green); their plotted maxima exactly matchprecision reports.

Remaining rawmetric graph residual: fresh2w3 9.956→5.220mm, graphdelta change
4.751mm; fresh4w5 9.387→8.901mm, graphdelta change.711mm. fresh4 no correction
cap clips, only.514mm maximum graph position change. These are estimator-only
factor residuals, not GT ATE. Five windows cover only3.3s of40s; coverage/constraint
conflict diagnosis continues with590 uniform20-frame raw windows onallten.
Sameestimator/gates/5BAnodes/fullratepixeltracking; not a weight/cap/keyframe/model
sweep and noGT-driven windowselection. Fullcoverage results follow below.

Independent review: noHIGH/CRITICAL, approved only isolatedbatch.168old/new
testsPASS and freshremote restore168PASS; coverageadapter additional3PASS.

## Phase6 — full uniform raw-window coverage (2026-09-28)

`controls_full_ten_v1` completed590 windows (59 per recording), with567
accepted and23 refused by unchanged internal guards. All21 raw frames are
tracked per window; five poses share landmarks within each independent window.
No external trajectory is used for estimation, rejection, or candidate selection.
The unchanged graph/downstream/scoring pipeline completed allten cases in
`graph_full_ten_v1`, via the fullcoverage adapter `run_full_coverage_graph.py`
(the base runner defaults to five-probe controls). No production source or
weight/cap was changed.

| Case | Frozen previous max mm | Full-window max mm | Mean mm | Result |
| --- | ---: | ---: | ---: | --- |
| fresh1 | 7.809 | 8.592 | 2.866 | PASS |
| dev1 | 6.458 | 6.356 | 2.690 | PASS |
| dev2 | 8.271 | 7.940 | 3.616 | PASS |
| heldout1 | 5.921 | 6.032 | 2.550 | PASS |
| heldout2 | 5.974 | 6.016 | 3.388 | PASS |
| heldout3 | 6.831 | 6.650 | 3.776 | PASS |
| heldout4 | 8.913 | 8.938 | 3.270 | PASS |
| fresh2 | 11.323 | 8.128 | 3.354 | PASS |
| fresh3 | 6.310 | 6.284 | 1.422 | PASS |
| fresh4 | 15.333 | 14.016 | 5.670 | FAIL |

**9/10 PASS, no lost previous passes; the all-recording10mm goal is NOT met.**
fresh4 P95 is9.060mm and97.373% of samples are within10mm, but30 of1142
samples exceed10mm. Its only remaining score failure is maximum ATE.
Relative to the first five-probe batch, fresh2 max improves8.837→8.128mm and
fresh4 improves14.983→14.016mm, while fresh1 worsens7.772→8.592mm.
Do not pick one candidate per recording using external scores.

The evaluation contract, official reference bytes, timestamps, sample counts,
and SE3-no-scale alignment match the frozen baseline. These are time-associated
ATE metrics, NOT nearest-curve geometric distances. The same1142–1143 scored
output samples are used; fullrawcapture1199 samples are not all evaluated.
Per-case precision reports, frozen command/hash manifests, and rotatable
`metric_windows_3d.html` are retained. White=official reference, gray=frozen
previous fusion, green=experimental full-window fusion. Allten plotted maxima
were checked against official precision reports to1e-6mm.

### What the raw observations establish (and what they do not)

After freezing all window estimates, the separate local-displacement evaluation
scored537 windows;30 accepted early windows are outside existing GT coverage.
The median/P95/max local displacement errors are1.129/4.318/18.578mm. These
are NOT full-trajectory ATE. All567 internally accepted factors were used;
none were removed based on their external error.

Low reprojection error does not guarantee millimetric motion accuracy:
fresh1 window42 has pixelP951.722px and97.2% inliers, yet18.578mm external
local displacement error; admitted tracks decline55→54→27→16→10.
fresh4 window54 has pixelP951.199px and99.3% inliers, yet9.224mm local error;
tracks decline97→97→64→32→14. Held-out pixel error also improved in both.
This supports investigating weak metric observability/depth-motion ambiguity
and endpoint support, rather than assuming a failed pixel fit. It does not
prove a unique root cause, a moved Lighthouse, or deficient model training.

Next bounded diagnostic: measure endpoint-motion Jacobian weak axes and
track-resampling stability from UMI observations only, plus adjacent-window
metric consistency. Freeze diagnostics before external scoring. A cross-window
shared-landmark estimator would be a separate architectural step, not a weight
sweep or interpolation replacing genuine motion.
No live hardware acceptance, new independent recording validation, or
production promotion has been performed. The experimental candidate is kept
separate so the existing production pipeline is not silently replaced.

Verification: independent read-only evidence review agrees with the9/10 result;
171 targeted old/new tests passed in both main workspace and clean remote-only
restore. Selected fullcoverage outputs were backed in commit
`18c37c4a76685a71798ba60a5d9eeecf68e3e9ee` on owned remote
`sencang/codex/stereo-window-bundle-20260928`. All178 changed files were fetched
from remote and compared byte-for-byte with main and backup. Normal push only;
no unrelated dirty production work was staged. Later documentation changes do
not alter that frozen algorithm/evidence commit.
