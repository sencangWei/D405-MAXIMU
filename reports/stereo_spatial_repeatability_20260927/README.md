# Ten-case stereo measurement diagnostics (2026-09-27)

Goal remains all-frame SLAM max translation error below10mm without GT input.
Current frozen baseline remains8/10PASS; fresh2max16.151mm, fresh4max16.181mm.
No new trajectory, production weights or calibration changes in this experiment.

## Results

**Superseding correction:** the old rawgyro/spatial/target-depth diagnostic used
maximum depth1.5m, while the formal workflow uses0.6m. These results are NOT a
production-equivalent replay. Withdraw their production-level conclusions about
which observation mechanisms can be ruled out. Original data remain for audit.
Exact0.6m reproduction matches cachedfresh2edge590→610 (162points/78inliers,
acceptedpose), independent of OpenCVthreads1/2/4/8/24. The earlier163-point
rejectedreplay is not evidence of runtimeinstability. Correctedten-case replay
is being written separately toproduction_depth_raw_ten_v2. Baseline trajectory
precision, rejectedactualattitudecandidate, and cachedlocalGTscoring unaffected.

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

## Additional world-basis contract candidate: REJECTED

Survey `direction_basis_ten_v2` explicitly removes the graph's final constant
attitude correction and brings VINS body attitude to the same leftIR camera
basis. It compares position-fit worldalignment with a single all-overlap attitude
worldalignment. Fresh4constantdifference2.764deg gives per-edge projection
difference max14.697mm versus2.291mm after attitude gauge alignment. Passing
fresh1alsohas3.544degconstantdifference; this is NOT a sufficient cause.

Opt-in `attitude_alignment_candidate_v1` changes only VINSbody relative-motion
worldalignment; stereo attitude, weights, sigmas, caps, frontend and calibration
remain unchanged. Actual all-ten stage7→9 + GTscore:8PASS2FAIL, no improvement.
Fresh2max16.151→16.153mm; fresh4max16.181→16.256mm. Reject this candidate.
Samplecounts, SE3scoring and selectedvisualsigma0.020m unchanged. Both original
positionalignment and candidateattitudealignment stats in comparisonJSON.
102relatedtestsPASS; production unchanged. This is not a delivered10mm repair.

## Original stereo edges evaluated against existing external reference

`local_stereo_scoring_cached_ten_v2` scores all166frozen selected original
productionobservations. Externalreference is used AFTER estimation only. Camera
origin recovered from bodyreference with fixed rotating body-to-leftIR lever.
No newextrinsic/timeoffset/worldalignment/scalefit; no observation/outputchanged.
These arelocalmotion residuals, not global trajectoryATE.

Fresh2edge590→610 SIFT localmotionerror15.684mm; the samepair is rejected by
newfreePnPreplay. BothIRs atbothtimes exactlymatch DB3 and preparedPNG (four
imageschecked, sourceframes620/640, skew0ms). Thusimagecontentmismatch is ruled
out for thispair. PnPsolver randomstate/order is an UNPROVEN followup hypothesis.
Fresh4edge1035→1075 error10.767mm remains accepted on replay. Passingheldout4
alsohasanedge12.729mm: single-edgeerror is not sufficient toexplain finalATE.

`local_stereo_scoring_ten_v1` is superseded: it onlyscored newlyacceptedfreePnP
subset and omits3fresh2originalacceptededges. Its4.044mmmax must NOT be cited as
theproductionstereo measurementmax. Use originalcachedv2all166 above instead.

### Scope correction

Spatial and target-depth sidecars likewise only cover newlyacceptedfreePnP;
fresh2 has13of16selectededges, missing3originalproductionacceptededges. Their
aggregate lackoffailure separation does NOT rule out depth/featuregeometry
problems on omittedbadcachededges. They justify no threshold deployment, not
declaring those observation mechanisms irrelevant.

Ten-seed RANSACdiagnostic coversall166firstforwardinputs, including newrejected
edges: eachfixedinput givesidenticalR/t for alltenseeds (diameter0mm/0deg). Thus
globalRNGseedalone is not an explanation for these fixed-inputPnPresults.
The badfresh2pair currentinputhas163points vs originalcached162; the subsequent
depth-contract correction above explains thisdifference. No solverrandomness
rootcauseclaim.

## Corrected formal-depth diagnostics

`production_depth_raw_ten_v2/cached_motion_audit.json`:166/166sampled freePnP
motions matchoriginalcachedproduction, with0.6mmaximumdepth. Allsourcehashes
unchanged. `production_depth_spatial_ten_v2` and`production_depth_target_ten_v2`
coverall166acceptedfreeedges and auditedcontrolfields equalcorrectedrawreplay.
These supersede the old1.5m diagnostics forproductioninterpretation.

Fresh2edge590→610: same78inliers LMrefit shifts14.422mm and lowersmedianpixel
residual1.995→0.857px, while delete-tilevariationmax1.022mm. ExistingfreePnP
refinement is implementedbutnotenabledbyproductioncallers. A uniform allten
free-refinement observationprobe isrunning; this is NOT yet an accuracyrepair.

Hard-gyrofixedlocalmotion evaluation: medianerrorworse9/10, fresh1max18.516mm,
fresh4max16.054mm. Fresh2improvesbutrejects1/16edge. Do not deployhardgyrofix.
GTusedonlyafterestimationforevaluation; noGTselectionoroptimization.

EarlierSIFTreversecandidate andobservabilityprobe likewiseused1.5m. Retainthose
historicaloutputs,butwithdrawgeneralizationtoformal0.6m observationrepairs.

## Free LM + gyro validation, observation diagnostic and full candidate

`production_depth_lm_gyro_gate_probe_ten_v1/raw_gyro_gate_audit.json`:same166
sourcepairs/RANSACinputs,allaccepted. Fresh2localmotionmaximum15.684→5.331mm
withoutremovingbadpair. Dev2/fresh3localmaxslightlyworse;fresh1medianworse.
These aremeasurementdiagnostics,NOT finalSLAMprecision or universal10mmproof.

`sift_lm_gyro_candidate_ten_v1` isrunninga fullten-casegraph→fusion→scorecandidate.
Re-estimatealloriginalacceptedSIFT factors only, notGT-selectedwindows; keepLK,
globalscales/frontends/VINS/graphsettings unchanged. PnP freelyestimatesrotation,
existing5degconsistencygate usescalibratedrawgyro, formaltdonce. Productionnot
changed. NoGToptimizerinputs, no per-case parameter tuning, noframe deletion.
Alltenresultsrequired; do not extrapolate fromfirstcase. Alreadyseenrecordings
aredevelopment/regressioncases; finalindependentvalidationneedsnewrecordings.
