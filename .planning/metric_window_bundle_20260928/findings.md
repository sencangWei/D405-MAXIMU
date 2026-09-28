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
# Phase5 latest full-graph result and index correction

First frozen allten graph completed9/10PASS, zero loss ofprevious8passes.
fresh2max11.323→8.837mm; fresh4max15.333→14.983mm. No productionpromotion.
fresh4wrapper integrated5newvectors; no capclips; neww5 residual9.387→8.901mm
andgraphdelta.711mm. Small influence doesNOTprove damagedrecording.

Independent review initiallymistook evaluationrowindices forfullgraphindices.
Corrected by exacttimestampmapping: fused1142rows vsgraph1199,sourceoffset57.
Current>10mm eval759–772→source816–829(maxsource820,11.173mm),
eval780–786→source837–843(max840,10.309mm),
eval988–1027→source1045–1084(max1071,14.983mm).
Window4[829..849] andwindow5[1068..1088] DOoverlaperrors; latterincludespeak1071.
Do notsay sparsewindows missedworstblock. Relativefactorlowleverage / constant-
offsetblindness is a hypothesis, notprovedsinglecause or GTadmissionjustification.
Next samealgorithmuniform590rawwindows measurescoverage/connectedmotion
information withoutchangingweights/caps/keyframes or pickingGTpositions.

Separate AFTER-estimation evaluation `graph_ten_v1/window_basis_evaluation.json`
compares rawmetricendpoint andoptimizedgraphdisplacement with unchangedbodyGT
convertedtoleftIR. All46 metriclocalreferenceerrors exactly matchpriorfrozen
localdisplacementscorer (<1e-9mm). Camera-graph SE3-no-scale alignment used ONLY
for orientation/worlddisplacement diagnosis, not officialbody ATE or selection.
fresh4w5 metriclocalerror2.881mm vsgraphlocalerror8.347mm; graphcameraorientation
vsreference1.109deg; metricworlderror1.864mm vsgraphworlddeltaerror7.195mm;
GTmotionlength186.461mm. Thusfor thiswindow, orientationbasis alone doesnot
accountforremainingdisplacementgap; acceptedrawmetricmeasurement has better
relativeGTagreement thanoptimizedgraph. This is evidence ofgraphconsensusgap,
notproofwhich individual oldedge ordataiswrong. Fresh2w3 basis2.849deg makes
metriclocal2.536mm→world4.243mm; orientationuncertaintystillmatterselsewhere.

# Phase7 diagnostic contract (not a correction)

Endpoint columns of the fixed-gauge BA state are21..23 for fiveposes and3..5
for twoposes. Every other pose/landmark/bias column is nuisance; use SVD span
projection rather than normal-equation inversion. Column normalization removes
parameter-unit scaling without changing nuisance span. Rank tolerance uses
pre-projection endpoint scale, preventing fully cancelled columns from being
misreported as observable because their numerical residue is nonzero.
Allfactor vs pixelrow-only sensitivity uses the SAME optimized robust Jacobian,
no gyro refit or pixel model swap. Units are mm/unitNORMALIZEDrobustresidual,
not calibrated mm/px covariance. Null axes have responseNone/rankdeficient,
not finite overconfidence. Provisional noise and local-linear assumptions noted.
Scoped least_squares interception returns the identical original result before
posthoc diagnostics; tests show endpoints/landmarks/bias byteidentical. Prior
solver source hash still matches frozen590controls.186testsPASS; independent
review requested before all-ten diagnostic census. No GT factor/gate selection.
