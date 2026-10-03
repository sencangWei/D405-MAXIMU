# Retain the effective component; isolate the missing interval-frame constraint

## Decision and current limits

The user's correction is binding: missing the final 10 mm requirement does not
invalidate an incremental improvement. Retain source-bound development candidates;
test the fixed failing five first, then the passing five with unchanged coverage.
Do not promote before that comparison, and do not roll back an entire direction
because one independent defect remains. Tracker is evaluation-only.

The retained metric-keyframe candidate changes neither the model nor the existing
local tracking objective. Its fresh ind2 LEFT raw-stereo primary passes at scale
dispersion 0.4093186696; the same-code OFF control fails near 0.571. These are
internal geometry results, not ATE. The RIGHT continuity defect below prevents
fresh ATE and remains in the cohort denominator.

## New failure-locus evidence: ind2 RIGHT frame 577

Both ON and OFF already contain the jump in their online trajectory, before the
full-trajectory export recomposes relative poses with the final keyframes.
For the input transition 576 -> 577 (19.2000008 -> 19.2333336 seconds):

| Native-coordinate step | Metric-keyframe ON | Same-code OFF |
| --- | ---: | ---: |
| Online | 0.111872592 | 0.110480196 |
| Final full export | 0.104127683 | 0.104686763 |

Native lengths above are not calibrated physical metres. The independently
recorded shared-geometry diagnostics in ind2_retained_stage_comparison_v1.json
report physical final steps 40.514 / 40.783 mm. RIGHT geometry there is explicitly
LEFT-derived and selection-biased, not an independent RIGHT stereo-PnP estimate.

A fresh query of all 111 ON solves.jsonl entries confirms neither frame 576 nor
577 is in any metric-keyframe graph. The first four graphs are [0,546],
[0,546,556], [0,546,556,568], [0,546,556,568,602]. Thus frame 577 is an interval
tracking pose between keyframes 568 and 602; improving only graph keyframes does
not directly constrain that pose. This rules out export-only creation of this
jump. It does not yet establish whether its local match/depth/scale estimate is
the principal cause or which repair will improve ATE.

Source inspection at toolchain revision 58c9f619d930566e79b42b16fe4ef3ebe3a79bfd:
FrameTracker.track optimizes the current-to-reference Sim3, optionally constrains
rotation, then stores the translation without a raw-stereo metric pose factor.
save_online_traj emits the accepted pose; save_full_traj transports its stored
relative pose by the final reference keyframe. Existing pose-scale/PnP overwrite
hooks are OFF and are not silently enabled.

## Reproducibility and next bounded test

Paths are relative to this directory. SHA256:

- ON online: full_metric_joint_frontend_v2/20260927_ind2/right/mast3r_logs/dataset_online.txt
  000a9c66f22f85fc3957410e768caa78d2a10e2c5986519fdf5d1902f109806d
- ON full: full_metric_joint_frontend_v2/20260927_ind2/right/mast3r_logs/dataset_full.txt
  acfe9e0dc84f9b1ad508470543e0020d92b11590c92be54a8a1883f0a7f681ac
- ON solve log: full_metric_joint_frontend_v2/20260927_ind2/right/solves.jsonl
  e2d7b41a931b66d9d271ebdd7bed646a0d8c0b857ad5d567b471249403d8fb22
- OFF online: full_metric_joint_frontend_control_v2/20260927_ind2/right/mast3r_logs/dataset_online.txt
  effd6dedd713b1835de73b414876cbf561712ee5f759d63077de25cbe8e09944
- OFF full: full_metric_joint_frontend_control_v2/20260927_ind2/right/mast3r_logs/dataset_full.txt
  e3f232c6eb1210df25a73c3771b6f4232a4341fa1ce127ffd4fdfd2539f8ae21

Next test: capture the actual local tracking inputs/iterate trace for this
transition, check transform direction, native-scale gauge and uncertainty, then
test a raw-stereo metric relative factor jointly with the unchanged learned
residual. Retain the existing keyframe improvement. No hard translation cap,
interpolation, frame removal, reference supervision, or quality-gate relaxation.
Do not mutate the frozen fixed10 queue's CODE_PATHS during its run. Independent
read-only review confirms a surgical per-frame factor is feasible: its state is
T_CkCf = T_WCk^-1 * T_WCf, measurement translation t_metric / ratio_k, and scale
ratio_f / ratio_k. It must be linearized inside local GN, not used as a hard
post-solve pose replacement. The proposal still needs actual frame-577 inputs,
flag-OFF equivalence, and a passing-control replay. No interval-frame repair or
full precision PASS is claimed.

## Independent second-record check: take04 LEFT frame 807

The new queue completed take04 RIGHT's 1199-frame frontend in 593.318 seconds,
then the reused LEFT trajectory failed primary continuity at transition 806 ->
807: 34.503213 mm. This is not caused by the new RIGHT frontend. LEFT's online
native-coordinate step is 0.122704657; its final full export is 0.085933909,
while the configured historical LEFT export already has 0.084588885 at that
same frame. Final re-anchoring reduces the outlier but does not create it.

Frame 807 is again absent from the metric graph. The first graphs are [0,783],
[0,783,799], [0,783,799,801], [0,783,799,801,814]. The existing keyframes bracket
807 with 801 and 814. No solve contains either 806 or 807. Current and historical
full poses are not interchangeable; the history is used only to locate the
inherited defect, not as a same-code OFF accuracy score.

SHA256 (relative paths under this directory unless stated otherwise):

- LEFT online: full_metric_joint_fast9_v1/20260929_take04/left/mast3r_logs/dataset_online.txt
  0c29f953f57eb6bcfa19b6422aa0f8793a58b8362a356dd4d8bc9a53ebf9b91d
- LEFT full: full_metric_joint_fast9_v1/20260929_take04/left/mast3r_logs/dataset_full.txt
  65e0614bff3a4d63bd975f091e032db05ba584b1a055f23800a5e7a60d37e194
- LEFT solves: full_metric_joint_fast9_v1/20260929_take04/left/solves.jsonl
  d4dd148bfa00ce57a352c50c3e94705412d6195d6281d8e9c7692a6099e372a2
- New primary report: full_metric_joint_fast10_v2/20260929_take04/eval/left_cache/stereo_scale_bidirectional_report.json
  bca6d62dbfcc00a6e77b54939f394215f3ebf9f707dd3db9e5a5b1c859cdb26b
- Historical LEFT: ROOT/reports/steamvr_umi_sessions/20260929_195345_slam_validation_take04/evaluation_20260929_v2/fusion_adaptive/sparse/mast3r/trajectory_frames.csv
  e0c53eb94af0b63b7a522d20aaffbc168a5fa62ffbd1869cfcf551f7cbb54fa4

Both independently checked loci bypass keyframe-only metric optimization. This
strengthens the interval-factor hypothesis but still is not evidence that every
failing record shares the cause, or that this proposed repair meets 10 mm. The
fixed10 queue continues automatically to take07 and then the passing controls;
the ind2/take04 evaluation failures and take02 coverage failure remain recorded.
