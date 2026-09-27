# Ten-case stereo measurement diagnostics (2026-09-27)

Goal remains all-frame SLAM max translation error below10mm without GT input.
Current frozen baseline remains8/10PASS; fresh2max16.151mm, fresh4max16.181mm.
No new trajectory, production weights or calibration changes in this experiment.

## Results

Same166uniformly time-stratified measurement pairs across ten cached cases.
No Lighthouse/Tracker input, no per-case tuning, no frame deletion or retraining.

1. Spatial delete-one-tile PnP sensitivity does not identify the failed cases.
   Failedfresh2 maxvariation1.270mm; failedfresh4 4.334mm; passingfresh1 6.166mm.
   These are refit sensitivity, NOT SLAM error or calibrated covariance.
2. Target-frame stereo-depth holdout also does not separate failures reliably.
   Median over edgewise median absolute depth discrepancies: failedfresh2
   3.174mm, failedfresh4 4.981mm, passingfresh1 5.293mm. Respective disparity
   discrepancies0.267px,0.259px,0.331px. Shared stereo models are NOT truth.

Thus neither result justifies a universal rejection/weighting repair, and neither
proves the cause of16mm trajectory maxima. Do not deploy either sidecar as a
quality threshold. Further joint stereo/IMU measurement-model work remains a
separate, unproven hypothesis, not a delivered accuracy repair.

## Reproducibility

Scripts: `.planning/stereo_spatial_repeatability_20260927/`.
Results: `ten_case_v1/`, `target_depth_ten_v1/`; each includes ten caseJSONs,
summaryJSON and audited aggregate. SourceSHA256 verified unchanged and all prior
free/fixed replay fields exactly equal the preceding rawgyroprobe after removing
new diagnostic fields. Uniform sampling rules and source paths in each caseJSON.

Spatialv1 source snapshots are included because subsequent hardening adds a
finite-rvec guard, originaldisplacement and conditionnumbermetadata; original
v1data was generated before those changes. Targetdepthsource unchanged duringrun.

Validation:99relatedtestsPASS; compileallPASS; independent read-only review finds
no frame/inlier/convention blocker. Actual precision target still NOT met.
