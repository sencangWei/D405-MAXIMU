# Findings

Previous frozen SIFT free-PnP LM + gyro-validation candidate: 8/10 PASS. Fresh2
max16.151→11.323mm, fresh4max16.181→15.333mm; production unchanged.
Corrected fresh2 edge590→610 onboard VINS disagreement13.568→3.152mm, reverse
closure1.290mm, but actual graph confidence stays0.05: measured scale1.566 vs
local reference0.995. Scale conflict alone does not establish stereo error.
Fresh4 does not exhibit the same sampled pattern; cause still unresolved.

Current pair-PnP: source-frame SGBM depth becomes a fixed object point, then
target left-image PnP; optional inlier LM still holds source depth fixed. LK
reverse estimates add another pair result but do not share optimized landmarks
across several views. Current graph consumes summarized displacement edges,
not raw pixel/landmark residuals. A multi-frame stereo landmark estimator would
change the observation model, not merely the displacement weight.

Planning skill/catchup run completed, no unsynced context reported. No CodeGraph.
Dirty production/calibration files were present before this approved work and
will be preserved. Offline cached data only; no ROS/hardware motion needed.

Code inventory found no existing shared-landmark stereo pixel BA. Current
`prepare_mast3r_d405_window_finetune.solve_component_positions` only composes
displacement edges; not the approved observation-model change. Reuse existing
calibration/image/gyro IO, add a separate pixel-level core. Formal config has
gyr_n=1.03e-3; runtime raw gyro supplies zero bias/VINS-online-bias policy, so
hard-fixing gyro is unjustified. Draft design includes shared estimated bias and
explicit provisional noise assumptions for review. No implementation yet.

Update: prototype implemented. Core accepts no GT/learned positions. Raw gyro
right-tangent finite-difference bias derivative and formal td tested. Fullten
uniform observation controls_v2:31/50 accepted after consistency gates,25/31
withheld-pixel improvements, median0.463→0.397px. Not a trajectory ATE claim.

New-candidate failure mechanism (NOT proof of original frozen SLAM failure):
dev1w3/w4 and dev2w4 have gross raw temporal correspondences while initialization
PnP remains gyro-consistent (<0.43deg). PnP discards those inliers, but the BA
currently reinstates them. Training left pixel P95 at dev1w4 rises34–105px and
dev2w4 reaches414px. Raw robust pixel BA can be pulled by coherent outliers;
postfit gates correctly refuse those windows, no production output changes.
Next investigate training-only PnP geometric admission (fixed2px existing
tolerance), never per-case/GT selection and never delete inputframes. Challenge:
initial noisy depths can also reject valid pixels, so admission must be tested
against depth-noise cases and not treated as new information or guaranteed ATE.
