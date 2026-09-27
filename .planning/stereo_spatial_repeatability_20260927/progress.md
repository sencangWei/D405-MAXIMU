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

Direction-basis survey firstattempt failed at helperAPI because fusion.load_trajectory
returns Rotation, not quaternionarray; emptyv1directory retained, correctedread
contract then completed direction_basis_ten_v2 all10/166. Undo finalconstant
graphattitude correction before comparing basis. Both worldgauge choices explicit:
positionfit existingmatrix; all-overlap attitude mean, never perframe/peredge.
fresh4 positionfit projectionmax14.697mm versus attitudefit2.291mm; constant2.764deg.
PASSfresh1has3.544degconstantoffset, hence not sufficient causal diagnosis.

Independentreview supports a singleonboard framealignment candidate, confirms
bodyoriginsmatch and camera extrinsic rotations cancel in meanworldtransform.
Do not replace stereoattitudes. Implement opt-in proxy under planning only;
existing production untouched. Fresh102testsPASS. Launch alltenstage7→9score
session44101 output attitude_alignment_candidate_v1. Casesorderedfresh1first,
thenprior6thenfresh2..4. Need allscores/samplecounts, cachedhashes and identical
autovisual-sigma policy before accepting/rejecting. Do not edit frozen candidate
sourcewhilebatchruns. Sourceoriginal alignment stats retained in cached reports.

Session44101completed allten graph→fusion→score. Candidate8PASS2FAIL,
fresh2max16.1527936mm and fresh4max16.2559919mm. No gain; rejectcandidate.
summarize_attitude_candidate.py checks samples/SE3alignment unchanged,
sourceinputhashes and selected visualsigma0.020m unchanged for eachcase;
storesboth oldpositionfit and newattitudefit stats. NoGToptimizerinputs.

Firstdiagnosticbackup34ce6faa pushednormally to sencang branch
codex/stereo-spatial-repeatability-20260927; freshfullrestore
/tmp/ego-stereo-spatial-restore-paR9kF matchesall37committedcomponents bytewise,
99restoredtestsPASS. Fullfetch took~9min; optionalfilteredfreshrestore also
completed /tmp/ego-stereo-spatial-partial-2iHmjv. Nootherprocesskilled.
Need secondbackup for basishelper/candidate/tests/results beforehandoff.

Localmeasurement evaluation firstv1scored newlyrecomputedfreePnP subset,
notproductioncachededges: do NOT use fresh2v1max4.044mm asproductionfactorerror.
Corrected explicitmeasurement-source to originalcachedproductionreports and
ran local_stereo_scoring_cached_ten_v2: all166selectedoriginalacceptededges
scored againstexistingofficialbodyreference converted toleftIR withfixedlever.
No new timeoffset/SE3/scale fits, no GT input to optimization, no estimateschanged.
fresh2edge590→610 SIFT error15.684mm; originalSIFTaccepted, replayfree rejects
(LK insufficientconsistentpoints; SIFTrotationdisagrees). fresh4edge1035→1075
SIFTerror10.767mm, alsoacceptedreplay. PASSheldout4has12.729mm edgeerror too;
do not equate anysingleedgeerror with wholetrajectorymax or universalcause.

IndependentrawDB3-vs-preparedPNG check fresh2sourceframes620/640 bothIRs:
allfour1280x720 imagesexact, pixel difference0, stereo skew0ms. Sourcegraphfinal
attitude correction is downstream, not a cachedPnP input. RNGstate/order
hypothesis remainsunproven. Launch same166pair ten-seed repeatability diagnostic
session89387, fixedfullcorrespondences/solverkwargs perfirstforwardfreePnP,
never rewritesacceptedmeasurement or trajectory. Extra solvercalls occurafter
originalfullestimate and before nextseed-resetcontrol to preserveoldresults.
103relatedtestsPASS; compileallPASS. No productionchange.
