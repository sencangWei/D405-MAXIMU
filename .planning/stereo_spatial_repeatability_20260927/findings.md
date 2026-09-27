# Findings

Previous SIFT reverse candidate allten8PASS/2FAIL; rejected, production unchanged.
Raw-gyro fixed-PnP diagnostic allten/166edges reduced reverse closure but worsened
reprojection medians in allten and paired VINS disagreement in8/10. Hard fixing
rotation is not a supported universal repair. Factory IR distortion coefficients
allzero, prepared crop0/maskfalse; one take4 dual-IR PNG-vs-DB3 sample is exact.

Current graph uses stereo_sigma_m=0.004 isotropically for all three displacement
axes, times scalar confidence and IRLS weight. It does not consume a directional
PnP/triangulation uncertainty. This is an assumption, not yet a proven bug.
SIFT/LK combined result preserves forward displacement/PnP rotation (only scalar
scale is averaged), so its actual forward RANSAC point set can be matched by
the -R^-1*t displacement. Plan freezes all166edge sampling rules from prior probe.

Spatial delete-one-tile refit measures feature-support sensitivity, including
coherent depth/image errors; it is not an absolute error bar. LM-all-inliers
control separates ordinary refinement shift from spatial omission sensitivity.

Completed spatial10cases/166edges: failurefresh2max1.270mm versus passingfresh1
max6.166mm; failurefresh4max4.334mm. Reject a universal spatial-sensitivity
weight repair. Remove newsidecar and all prior free/fixed replay fields equal
raw_gyro_pnp_v1 exactly; original report SHA256 unchanged.

Completed held-out target depth10cases/166edges: forwardPnP source3D is fixed,
targetleftUV is fitted, targetstereo disparity is unused. Recompute targetSGBM
on exactforwardPnPinliers; LRtolerance1px, originalrange0.07..1.5m, R*XYZ+t.
Check availability/LR/range separately. Edgewise depth-absolute-median aggregate:
fresh2 3.174mm / disparity0.267px; fresh4 4.981mm / disparity0.259px;
passingfresh1 5.293mm / disparity0.331px. Median predicted/measured depth ratios
near1.0. Not a common failing-case discriminator. Shared stereo observation
model is not independent truth; these numbers are not SLAM trajectory error.
No productionweights, acceptance rules, outputs or calibration changed.

Diagnostics do not establish that a joint two-time stereo/soft-gyro solve would
improve absolute SLAM. Request independent architecture critique before another
candidate; a new observation model requires one frozen all-case falsification,
not scalarweight sweeps or GT-guided frame selection.

One isolated attitude-world-alignment candidate actually scored on allten:
8PASS2FAIL, no newpasses. fresh2max16.1514227→16.1527936mm;
fresh4max16.1810366→16.2559919mm. Candidate REJECTED, production unchanged.
Allscore samplecounts/SE3alignment unchanged; auto visualsigma0.020m unchanged;
allcachedinputhashes unchanged. Do not mistake projectionbasis difference14.7mm
for a predicted14.7mm improvement in the actual graph; observed effect<0.075mm.

Originalcachedmeasurement scoring (GT evaluation only, not optimization) all166:
fresh2worstedge590→610 error15.684mm, originallySIFT78/162inliers and reproj
median1.995px/P953.641px, visualrotationdifference4.488deg; newfree replay rejects
SIFTrotationdisagrees after LK insufficientdepthpoints. fresh4worstedge1035→1075
10.767mm, also acceptedreplay; passingheldout4worstedge12.729mm. These arelocal
motionerror evaluations, not wholetrajectoryATE. Initialv1onlynewacceptedfree
subset incorrectly excludes3fresh2productionedges; supersede bycachedv2all166.

Four fresh2IRimages atfirst590/source620 andsecond610/source640 exactlymatch
preparedPNG vsoriginalDB3, zero pixelchanges/skew. Excludesimagecontentmismatch
for thispair only. CandidateRANSACseed/input-order solution sensitivity is an
unprovenhypothesis; evaluate all166uniformly, fixedfullinputs, tenfixedseeds.

Important scope correction after originalcachedscoring: spatial/target-depth
diagnostics only enriched newlyacceptedfreePnP (fresh2 13/16), not the3original
productionacceptededges nowrejected. Thus their aggregate lackoffailure
separation cannot rule out instability on the missingbadcachededges, especially
590→610. They justify no weight/filter deployment, NOT declaringdepth/geometry
irrelevant. Do not cite their fresh2median as a complete productionedge audit.

RANSACseed diagnostic completed10cases/166firstforwardinputs (including rejected
replay). All ten-seed R/t results identical perinput: translationdiameter0mm,
rotationdiameter0deg. Reject seed-alone-as-PnP-cause hypothesis. Badfresh2pair
current163PnPpoints vscached162; inputgeneration/implementation/runtime
contract remains to investigate, not solverrandomsampling onfixedinputs.

### Superseding correction: diagnostic depth range was wrong

Bounded child reproduction with exact formal0.6m maximum source depth restores
fresh2edge590→610 original162points/78inliers/acceptedpose exactly, independently
of cv2threads1/2/4/8/24. Root probe instead hardcoded1.5m in forward and reverse.
Therefore prior rawgyro/spatial/target-depth controls are not production-equivalent
and cannot justify ruling out their respective mechanisms on productionedges.
The fixed-input RANSAC seed result remains a measured property of1.5m inputs,
not an explanation of production. Four-image identity still valid. Localcached
GTscoring and actual attitudecandidate/baseline precision are unaffected.
Corrected diagnostic parser default0.6, explicit1.5 override for legacyreruns;
record actualdepth/disparityparameters. Two parser contracttestsPASS. No production
change. Launch correctedrawallten166pairs, outputproduction_depth_raw_ten_v2.

Correctedalltencompleted: cached_motion_audit.json verifies166/166freeposes,
methods/inliercounts/acceptedstates matchactualcachedproduction; sourcehashes
unchanged. Correctedspatial/targetsidecars both all166freeaccepted andequalraw
controlafterremovingtheirsidecar. Fresh2bad590→610 is nowincluded. Its LM-all
inlierrefit shifts14.4217mm, reducesreprojectionmedian1.995→0.857px; tiledelete
max1.022mm. This differs materiallyfromtheearlieromission-basednegativeclaim.
Sourceestimate_motion_from_correspondences supportsrefine_pnp butdefaultsFalse;
no productioncaller passesTrue. TestoneuniformexistingfreePnPLM onall166,
without changingRANSACinliers/depth/gates or usingGT; outputlm_probe_ten_v1.

Correctedrawgyrofix evaluated AFTERestimation onexistingGT allten: medianlocal
errorworse9/10; fresh1max8.986→18.516mm; fresh4max10.767→16.054mm; fresh2
max15.684→5.046mm butoneedge rejected (15/16 scored). No universalhardgyrofix
justified. These arelocaledgeerrors, notcandidateSLAMmax. No GToptimizerinput.

UniformLM + rawgyro-as-rotation-gate (notfixedrotation) diagnostic complete:
all166motionsaccepted, matchedsame-RANSACinputs. Fresh2localmaximum15.684→5.331mm
withoutomittingbad590→610. Pairedmedian improved8cases,heldout3unchanged,
fresh1slightlyworse1.390→1.471mm; dev2localmaxworsens4.726→5.524,fresh3
7.224→7.435. These arelocalmeasurementresults, nottrajectoryprecision orfinal
independentacceptance. Casesalreadyexaminedfordevelopment requirefreshrecordings
afteralgorithmfreeze for independentfinalvalidation.

Preparedcandidate differsfromsampleprobe: onlyalloriginalacceptedSIFT factors
are re-estimated (LK frozen to isolate SIFTfallback), global scales unchanged.
No per-case tuning or GT-driven edge selection. Existing5deg gatechecksrawgyro,
PnProtationremainsfree. Reviewer confirmsgyroframe/td-onceconvention andscope;
addedIR/calibrationsameassertions andexplicitrefinement_estimate to distinguish
staleinheritedfields onrejectedobservations. Actualalltenstarted5720,
output sift_lm_gyro_candidate_ten_v1, firstfresh1thenprior6thenfresh2..4.
