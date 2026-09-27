# Progress

User requested continuation. Read karpathy-guidelines, systematic-debugging,
planning-with-files and root-cause-tracing fully; session catchup empty.
Existing dirty fuse_mast3r_stereo_imu.py belongs to prior joint-scale progress;
do not reset/rewrite it. No production edits. Read graph factor construction and
forward/reverse displacement contract. Prepare failing-first tests for a small
spatial sensitivity diagnostic, reusing prior all-ten sampling harness.

Failing-first: new testmodule initially fails collection because spatial_pnp.py
does not exist. Implemented diagnostic-only helper, all3synthetic testsPASS:
exact geometry stable, coherent regional error creates measurable variation,
one-tile support returns unobservable/null rather than fakezero uncertainty.
All44related tests freshlyPASS. Independent critique approves read-only scope,
warns against calibrated covariance claims and stochastic per-fold RANSAC.

Launched ten-case_v1 with frozen prior166edge sampling and free/fixed controls.
Capture actual forward RANSAC point/inlier set, assert displacement+rotation match
accepted measurement, all-inlier LM control then delete-one4x4tile LM fits. Require
at least20 and half original inliers remaining, full six-parameter Jacobian rank;
record unobservable folds. Existing algorithm/defaults/output trajectories untouched.

Completed all10/166edges. Spatial variation cannot distinguish failures: fresh2
max1.270mm, fresh4max4.334mm, passingfresh1max6.166mm. Reject universal uncertainty
weight repair; no production change. Preserve v1helper/wrapper snapshots beside
v1results before hardening. Reviewer reproduced nonfinite fold rvec crash; add
failing synthetic test then finite guard before Rotation construction. Also
record Jacobian condition and original displacement, restore load hook finally.
Fresh99 related testsPASS including3new target-depth synthetic tests.

Start target_depth_ten_v1: same166time-stratified pairs, exact original freePnP
inliers/pose verified. Compute unused target stereo disparity with LRcheck1px
and original source-depth range; compare in target-camera frame R*XYZ+t. No
trajectory edit, GT access, model training or candidate selection. Independent
review confirms conventions and no blocker, warns this is shared-model internal
consistency, not absolute accuracy. Capture availability/LR/range separately.

Target-depth session36872 completed exit0,10cases/166edges. summarize.py asserts
every priorfree/fixed field equal originalrawgyroprobe after removing sidecar,
allinputhashesunchanged. No robustfailure separation; reject rootcause/weight
claims. Both diagnostics yield counterevidence, not a10mm solution. Requested
boundedarchitecturecritique before more observationmodel candidates.
Two documentation apply_patch context failures made no filechanges; corrected
exactcontext and reapplied. No production filechanges thisturn. New diagnostic
guards and helpers freshly99relatedtestsPASS, compileall exit0.
