# Progress

2026-09-28: user approved proposed window-metric candidate. Read repository
AGENTS, memory index, karpathy-guidelines, systematic-debugging, and complete
planning-with-files instructions. Started bounded read-only existing-code lookup
with installed executor fallback after explorer's unsupported-model failure.
Created scoped plan before implementation. No production source edits, no new
trajectory run, no GPU/model training, no claimed accuracy gain.

Inventory complete: no shared-landmark pixel-level stereo BA exists; pair-PnP
and displacement-graph scaffolds reusable but not substitutes. Drafted minimal
contract with estimated bias and fixed formal noise inputs for independent
review. One malformed apply_patch failed verification with no edits; corrected.

Implemented isolated shared-landmark BA and image-only observation extraction.
Synthetic extraction initially failed (1/6): forward stereo was exactly -18px,
but unseeded reverse LK jumped a 25px texture period, rejecting all60 tracks.
Inverse initialization on the stereo backward pass gives60 valid tracks and
~0.000085px median closure; no gate relaxation. This seeded closure is NOT an
independent matcher. SGBM LR/depth/epipolar checks remain. Finite disparity seed
guard prevents NaNs passed to OpenCV. New core/control/extractor tests23PASS.

First frozen observation batch controls_ten_v1 completed allten, five fixed
windows each.36/50 solver-accepted; heldout reprojection improves26/36. Median
withheld-pixel RMSE0.546→0.452px, mean2.792→2.648px. These are NOT ATE errors.
14 rejections:6 insufficienttracks,3 visualinit,3 training-onlyPnP,2 max-iteration.
Several accepted windows remain bad: dev1window4 withheld0.326→1.876px,
endpoint changes21.09mm, biasnorm~0.072rad/s; dev2window4 withheld49.77→51.87px.
Therefore do NOT integrate this prototype into formal graph or claim10mm.
Diagnose raw correspondences/visual initialization and review first. Known
diagnostic bug: caller-supplied bias Jacobian was mislabeled exact (solver
unchanged); correcting separately after batch to preserve sourcehash provenance.

Review fixed: bias derivative always first-order/not exact; fixed2px stereo
P95/support,5deg gyro,0.01rad/s percomponent bias guards with diagnostic states
retained on rejection. Holdout extraction no longer performs all-track PnP.
Short-window bounds test caught count96 lastindex96: reject96, accept97;
no wrapping negative/out-of-range NumPyindices. Targeted27testsPASS; extended
existing stereo/fusion+new tests136PASS. controls_ten_v2 complete:31accepted,
25heldoutimprove, median0.463→0.397px. No graph replay/production promotion.

Second review identified exact boundary error: training-only PnP rejected gross
pixel observations but BA reinstated them. Fixed per-observation admission,
source stereo retained, no blanket track blacklist or frame deletion. Fixed
existing2px threshold (no sweep). Tests added holdout sentinel,10% grossoutliers,
per-view preservation and support collapse.30newtestsPASS before next depth-
bias test. controls_ten_v3 completed fixedallten/50windows:37accepted,27heldout
improvements; withheld median0.552→0.432px. Separate evaluation-only scorer,
run AFTER estimation with unchanged official reference/time/extrinsics:37 local
endpoint comparisons,26improve, median0.721→0.392mm,max10.425→3.332mm. NOT full
trajectory ATE;13windows refused, no productionpromotion or claimed10mmSLAM.

Final independent review:0HIGH, safe as observation-only prototype. Repaired
scoring script selfhash and input provenance. controls_ten_v4_manifest records
graph/stereo/calibration/session/IMU hashes and selected decoded grayscale
frame hashes.37 accepted endpoints equalv3 bit-for-bit, same13 rejections.
New-only31tests and extended140testsPASS; syntaxPASS. Algorithm code remains
isolated; plan phase5 (full graph integration/ten-case ATE) still pending.

Ownedremote initialprototype commitd725776f restored into new empty repo:
58filesbyte-identical,140testsPASS. Known error-block coverage check: fresh2
window3=[589..609] fails trainingPnP, fresh4window5=[1068..1088] fails shared
support; successful3.332mmlocalmaximumdoesNOT establish repair atthoseblocks.
Next bounded consistency branch uses all21 recordedframes for LK propagation
but samefiveBAnodes/calibration/gates.32newtestsPASS. Frozenallten_v5running;
not a MASt3Rkeyframedensity sweep, no frontend rerun or trajectory filtering.

Fullrate_v5 finished40/50;30 local comparisons improve. Medianlocal0.763→
0.661mm,max11.408→3.714mm. On37 shared v4windows only18 improve; therefore
fullrate alone is NOT a general accuracy improvement (sharedmedian0.392→0.529).
Both original issueblocks remain refused. Read-only exactstage diagnosis:
fresh2source180features,177persistenttracks, BAvalid177/169/156/102/38;
trainingPnPinliers129/101/64/18. fresh4source180,133persistent, BAvalid
133/116/89/46/43; PnPinliers75/35/19/16. Enoughsource depth pixels, notdamaged
recording. Current blocker is newly introduced pairwise20-point initgate.

Independent reviewer approved fixed joint-geometry boundary repair, NOT a
supportthreshold sweep: EPNP mathematicalminimum4 plus per-node non-collinear
XYZ rank, source retained, temporal/connectivity/cheirality/corepostfitguards.
Window authority is joint BA, not independent20-point initialization tests.
Tests4/16/18/19 support and collinear sparse-node rejection added;37newtestsPASS.
Frozenallten controls_ten_v6_joint_geometry running; no productionpromotion.

v6 finishedallten/50:46accepted,4persistent-track refusals. AFTER estimation,
officialreference localdisplacement verification:36/46improve,median1.005→
0.878mm,max41.804→3.714mm. Criticalfresh2w3initial41.804→2.536mm;
fresh4w5initial6.522→2.881mm. These are initialization-vs-BA localcomparisons,
NOT production full-trajectory ATE. Extended146testsPASS, new37PASS, syntaxPASS.
Existing graph consumes fullmetric3D vector; confidence/local-scale is still
learnedscale based. Need distinct rawmetricfactor interface, not fakescale or
oldweightfamily sweeps. Phase5pending. One documentation patch context mismatch
failed atomically, corrected after exacttext inspection; no partial edits.

Phase5 user approved. Nativegraph interface verified: fullvector constraints,
but oldconfidence default would be0.1 for plain no-scale factor. Explicit source
dispatch is required; no fakePnP/scales. Local scalar state excludes no-scale
observations. Wrapper implementation delegated as bounded two-new-file slice;
read-onlyreview independent. Native production and priorcachedreports untouched.
Frozen ten-case replay runner uses previous validated SIFT-LM/gyro graph command,
not originalunrefinedstereo command frommanifest. Runner tests2RED(no module)
then2PASS; pathremap guards siblings and preserves td/referencecommands.

Runner independentreview foundfullinput/sourcepostflightgap; repaired and added
stub-subprocess mutationregression,4testsPASS. Wrapper initial8testsPASS but
realrefinedSIFTreport pathnotinoriginalcontrolshashmap; caughtbeforebatch.
Independentreview agreed; executorrepairingoriginalsourceidentitylookup,
NaNguard/strictinteger/fullcalibration/frame/versionchecks. No rawbatch started
with knownbadinterface. Broadtests during tests-firstversionupdate156PASS/2RED
(expected oldtag vsnewtests); fresh fullrun requiredafteragentready.

Actual all-tenpreflight exposedcontrols inputJSON `inputs` can be a string;
sourceidentityscan repaired withdictguards +regression. Finalwrapperstrictfixed
pixel/gyro/biasguards,frame/fullcalibration/integer/timestampguards, versioned
typeconfidence dispatch, sourcehash rechecks;18wrappertestsPASS. Runner4PASS.
Fresh combined168testsPASS in1.31s. Allten actualcaseidentity/timestamp/hash
preflightPASS46factors. Frozengraph_ten_v1 started: firstfresh1 completedall
stages(score rc0, max7.772mm); automaticallycontinuingnineothers. This is not
fullbatchPASS or productionpromotion. Existingnativeproduction hashesunchanged.

graph_ten_v1 completedALLten. Officialcontract/GT-reference bytes/samplecounts
identical topreviousfrozenSIFTcandidate.8→9PASS, zero lostpriorpasses. Newmaxmm:
fresh1 7.772, dev1 6.454, dev2 8.261, heldout1 5.922, heldout2 5.967,
heldout3 6.838, heldout4 8.960, fresh2 8.837, fresh3 6.305, fresh4 14.983.
fresh4 mean5.936/P9510.102/94.658%≤10; targetNOTmet, no promotion.
UMI-onlyfactor diagnostic: fresh2w3 residual9.956→5.220mm,graphdelta4.751mm;
fresh4w5 residual9.387→8.901mm, graphdelta.711mm,maxgraphpositionchange.514mm.
fresh4graphcapclips0; native local scalar state counts1650oldedges(no-scale
newedges excluded). This doesnotprove a singlecause; fivewindowsspanonly3.3s
of40s. Next testeduniformnon-overlapping20frame rawcoverage, sameestimator/
gates/5BAnodes/fullratepixels, notweight/keyframedensitysweep or GT-pickedframes.
Codebackup c5953e24 normalpushedsencang; fresh remote-onlyemptyrestore
/tmp/ego-metric-graph-restore-0Wz3x7,9newchangedfilesbyteidentical,168testsPASS.

Fullcoverage590uniformwindowcontrols underway, firstfourcasescompleted. Old
native disparity helper emitsinvalid-castRuntimeWarning fornonfinitepixels;
downstreamvalidity/accepted-modelguards remainactive, nativefileunchanged.
ThreecoverageadaptertestsPASS; finalcombined171PASS. Adapterhashnowincludedin
controls sourcehashmap; fixturemissingthatrealfield repaired (oneRED→3PASS).
AFTERestimation graph-basisdiagnostic46metriclocalerrors matchexistingfrozen
scorer; fresh4w5 localmetric2.881 vsgraph8.347mm (worldmetric1.864 vsgraph7.195).
Nativeorientationbasis1.109deg atthiswindow isnot sufficientexplanation alone.
Evidencebackup aead1d99 restored170changedfilesbyteidentical/171testsPASS.

Latestremote diagnosticcommitc4095a63 fetchedintocleanrestore:5changedfiles
byte-identical. Currentactiveofflineprocess execsession34290:
run_full_coverage_controls.py --output reports/metric_window_bundle_20260928/controls_full_ten_v1
(OPENBLAS/OMP threads1). Lastcensus279/590, first4casesfinished, fifthunderway.
Afterallten summaryexistsandprocesssuccess: run_full_coverage_graph.py --output
reports/metric_window_bundle_20260928/graph_full_ten_v1; then unchanged
summarize_graph_regression.py --candidate sameoutput. Fresh main and restored
extendedsuite171PASS; coreunchanged. No formalpromotion.
Current9/10precisionappliesto samefrozen1142–1143outputsampleevaluationrange as
baseline, not all1199rawcapture frames or independentfuturecapturegeneralization.

Fullcoveragecontrolsfinished rc0:590windows/10cases,567accepted/23refused.
Accepted percase(dev1,dev2,heldout1..4,fresh1..4):57,55,57,59,58,56,57,56,54,58.
AFTERfrozenestimation localdisplacementscorer537scored (30firstwindowsoutside
existingreferencecoverage). Neverdropthese inputframes orpickconstraintsbyGT.
Fullgraph replay started execsession80806, outputgraph_full_ten_v1; SAMEfrozen
alltencommands/oldparameters, onlyfullcoveragecontrolssummaryreplacesv6probes.
Preflightalltencontrolssourcehashincludesadapter; no productionpromotion.

Full graph execsession80806 completed exit0, allten downstream scores present.
Full-window maxmm: fresh1 8.592,dev1 6.356,dev2 7.940,heldout1 6.032,
heldout2 6.016,heldout3 6.650,heldout4 8.938,fresh2 8.128,fresh3 6.284,
fresh4 14.016.9/10PASS, previous8passes retained; targetNOTmet.
fresh4 mean5.670/P959.060/97.373%within10mm;30/1142over10mm.
Allten evaluation contracts and external-reference bytes unchanged. Generated
ten rotatable plots; maxima equal official scoreswithin1e-6mm. All raw outputs,
failedcases and23refusedwindows retained. Main production not promoted.
Fullcoverage local537scoredwindowerrors median1.129/P954.318/max18.578mm;
pixel/internalguardspassing cannot guarantee metrictruth. Next scientifically
bounded lane is endpoint observability/resampling/adjacent-window consistency,
not GT-picked factors, per-recording weight tuning, or model-training guesses.
