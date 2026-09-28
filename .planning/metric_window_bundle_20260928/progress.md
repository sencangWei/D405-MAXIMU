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

Final independent read-only review confirms experimental9/10/30over-limit
samples/no lostpriorpasses; no production acceptance. Fullcoverage uses fixed
adapter, not base runner default. LocalP95usesNumPypercentile explicitly.
Freshmain171testsPASS1.50s. Selectedbackup18c37c4a normalpushedownedsencang;
remote-onlyrestore/tmp/ego-metric-graph-restore-0Wz3x7 fetched178changedfiles
byteidentical to main/backup, restored171testsPASS1.35s. No work left running.
Frozen current integration experiment complete, target not achieved. Next
endpointobservability diagnostic is proposed, notimplemented or running.

User approved next endpointobservability diagnostic. Skills/rootcontract/
HANDOFF/memoryindex read; closedfamilies avoided. Main implementing standalone
diagnostic and adapter; independent read-only mathematical reviewer active.
No core mutation, no GT input, no production promotion. Same590uniformwindows
will be surveyed after synthetic and unchanged-endpoint/acceptance tests.

Tests-first diagnostic collectionRED missingmodule. Added standalone SVD
nuisance marginalization + scoped optimizedJacobian capture. First12tests:
11PASS/1fail due unjustified2x short-baseline response expectation (temporal
parallax also constrains depth); repaired direction-only physical contract.
No empirical estimator gate/weight changed. Added diagnostic failure preserving
solver acceptance and fullcoverage adapter restoration/source provenance tests.

New15diagnostic/adaptertestsPASS; extended186PASS1.40s, syntaxPASS. Nativecore
SHA matches prior590controls, no edits. Awaiting independent read-only review
before realcensus; all-ten/frozen fullcoverage schedule unchanged. A failed
appendpatch tofindingsused incorrectheader anchor; rereadtail/repaired, no file
changes from failedpatch. Instrumentation outputs diagnostics only.

Independent read-only review: APPROVE diagnostic-only590windowcensus,0blockers.
TwoLOW recommendations repaired beforelaunch: explicit scriptsimportpath,
provenancecheckexplicitValueErrorinsteadassert. No optimizer equations changed.

Alltenfullcoverage diagnosticcensus running execsession2929; first3groups
complete,198/590windows latestcheck. Interceptiondoesnot change originalsolver.
Core diagnosticbackup28e60086 fetchedcleanremote:7filesbyteidentical,
restored186testsPASS1.42s. Addedpost-freeze-only localreferenceassociation
5testPASS; workersummary correctedexactnames/schedule/nullrank/unavailable/
quantile/provenanceguards14PASS. Freshcombined205PASS1.65s. Newsummary/
associationreview active; no real externalscores read by diagnosticcensus.

Review caughtlocalGTscore schema mismatchinassociationgeneric hashvalidator;
fixedschema-specifictop-level hashes/flags + CLI regressiontests. Worker
summarymetadata malformeddiagnostic guardsadded, producerfieldmismatchfixed.
32summary/associationtestsPASS. Realpartialnumericalidentityfails by~1e-7m:
tracedhistoricalcmd tosystemNumPy2.2.6 vscurrentvenv1.26.4, admissions/init same.
Addeduniform10casefirstwindowpaired proofBOTHruntimes; allwithin-runtime
instrumentationoriginalresults BITequal, originalsystemcachefirstwindowexact.
Explicitnumericreproducibility mode pending; defaultstrictunchanged. NoGT
associationlaunched; currentcensuscontinues, eightgroupscompletedlatestcheck.

Allten590windowobservabilitycensusDONEexit0. NoGTreads duringestimation.
Optionalnumericproofmode strictdefaultpreserved; reviewerMEDIUMinputbinding
fixed withinputmap/decode-subset/admissionregressiontests. Anextendedtestrun
duringworkeredit brieflyfailed2fixtures (differentold/newfakeinputhashmaps);
fixturescorrectedsharedrealinput,notguardrelaxed. Finalexpanded248PASS2.33s.
Realnumericproofmode 567accepted/0missing,strictidentityfalse;maxcomponent
2.113874232e-7m. Allacceptedconditionalranks3,notstrictunobservability.
CurrentcontrolslocalGT537scores/frozenassociationv2 completedexit0;rho0.803
sensitivity/erroralltenpositive,tracklossproblemwindows butcounterexamples
explicit. Earlierdiagnostic/associationv1 retainedsuperseded(helperhashchanged),
sourcecensus/pairedproofunchanged. No newfusiontrajectories,goalstill9/10PASS
fresh4max14.016mm. Phase7diagnosiscomplete; reportsobservability_report.md.

Finalread-onlyreviewAPPROVE/no blockers; v2sourcehashesvalid. Selected49changed
code/test/evidencefiles committed112452548721035561bdbb0d9bfa4c609609c0b3,
normalpushedownedsencangbranch, fetchedcleanremote-onlyrestore49filesbyteequal
main/backup. Restored246PASS2.06s; main248PASS includes2existingimu-scaletests
absentselectedbackup. Firstbackupcheckexited4missingthose2tests(no tests ran),
correctedselectedsuite246PASSbeforecommitandafterrestore. No scope/gatechange.
No computation orlivecaptureleft running. Nextrepairrequires actualpersistent
cross-windowlandmarkobservations, not claimingthisdiagnosticfixedtrajectory.

UserapprovedPhase8. Readskills/AGENTS/HANDOFF/memoryindex/currentcontracts;
plannedtwoadjacent20framewindows withsingleboundarypose/mergedlandmarks/newB
birthstereo. Puremerger/tests delegatedexecutor, mainowns matchedrawcontrols.
Pre-reviewacceptsdirectionwithheldoutinheritance/single-count/frame/gauge/
no-fallback/factor-correlation hardchecks. No productioncore/tracker edits.
Initialwrongobservationfilename corrected; .codegraph/nestedAGENTSentriessabsent.

Testsfirst mainjointcontrolsREDmissingdriver; implementedfixed5pairs schedule,
camera_i endpointconversion,birth-anchoredheldoutprediction/nojointfallback;
4PASS. Executor puremerge8PASSincludesreal9nodeborn-landmarkBA/gauge/depth/
singlecount/holdoutinheritance. Independentreviewactivebeforerealcensus.
Jointpreparationfailurepreservesalreadycomputedindependentcontrols (initial
callerwouldotherwiseoverwrite aspreparationfailed); no acceptancefallback.

Phase8finalreviewAPPROVEfixed5pairs×alltenUMI-onlycensus, NOTgraphpromotion.
Jointsupport/solvefailuresalsopreserveindependentcontrols; withhelddiagnostic
errorsrecordnull/reasonwithoutchangingtrainacceptance. Ambiguitydiagnosticnow
countsArowsandBcolumns; matchingunchanged. Fresh265PASS3.20s includes17new
join/controltests. Frozenalltenrunstartedsession20158,outputjoint_pairs_ten_v1.
Sameoriginaltracker/solver/noise/td; noGTreads, no productionedits ornewATEyet.

Phase8v1alltenDONEexit0. Joint39/50pairs=78endpoints, independent91/100.
Frozenlocalreferenceevalsame78: med/P95/maxindependent .553/3.160/6.218mm,
joint .767/3.490/7.171mm;33/78improved. Reviewrejectsgraphpromotion. Boundary
posthocmatchinghas0–3sharedtrainingpoints; fresh4pair5only1. v1code6changed
filesnormalpushed52de61983363a0657d18c1b1db31732e26ee0307, remote-onlyrestore
byteidenticalall6; restored263PASS3.02s (main265includes2unbackedexistingtests).
Firstbackupguard -d.git failedcleanly becauseisolatedrepo isgitworktree(.git
file); fixedrev-parse validationbeforeanycopy. No overwrite/destructiveaction.

Phase8v2pre-reviewapprovesseam-bornbidirectionalcontinuousLKnewphysicalIDs,
mask7pxexistingALLlabelsbeforeGFTT. Newhelper/main/testswritten;32targetedPASS
includingactual9nodeBArecoverysynthetic. Workerremovedtest-onlyglobalruntime
state/unusedalias; source>=20matchesprimarysourcegate,supplementary>=4only.
Twofixturefailures(missingaccepted,Noneimagelist)fixedtestdata notgate. Failed
planpatchheaderanchors made no edits; correctedexactErrorsanchor. Awaiting
implementationreviewbeforev2alltenrun. v1frozencode/evidenceremainsunchanged.

V2implementationreviewAPPROVEUMI-onlycensus/noHIGHMEDIUMblockers; alltenfactory
rectifiedintrinsicsequalverified. LOWstalemerge-metadata fixedexplicit geometry
outputkeys/test;32targetedPASS1.72s, expanded280PASS3.91s beforethattinytidy.
Frozenv2alltenstartedsession50696, seam_pairs_ten_v2; noGTscore/promotionyet.

V2alltenDONEexit0,84endpoints/42pairsaccepted, matchedindependent91/100.
FreezeTHENlocaleval/comparison,alltenexactinput/decoded/independentcoreidentity
PASS. Same78v1-v2localmax7.171->4.520mm,48improved; same84independent-v2max
7.182->6.028mm,42improved. FullATEUNCHANGED9/10PASSfresh4max14.016mm.
287expandedtestsPASS3.96s (restoredprior278before7comparator tests); comparator
reviewAPPROVE. FullUMIpaired40stride40alltenapproved, adapter/testsdelegated,
no graphpromotion. Fullcase-count/sourcehash/tailcoverage boundariesexplicit.

Fullpairedstride40adapter implemented; firstreviewinspectioncaught tailalways0
whenloaded-prefix mistakenforrecordinglength. FixedBEFORElaunch byscopedactual
countcapture,11testsPASS; expanded287beforeadapter,50newtargetedPASS1.66s
withcomparator/allnewsets. No input/outputtrim. 1199/1200rawframes
leave38/39withoutanother40frameendpointfactor, explicitlyreported(notdropped
fromfuturefullSLAMoutputs). Hash/binding/argv/context restoration testsPASS.
FulladapterimplementationreviewAPPROVE/alltenactualcountsverified; noGT/no
graph/fullSLAMoutputtrim. Launchfrozenfullcensusnext (290pairs/580endpoints).

FullpairedcensusRUNNINGsession35649 outputseam_full_ten_v1. First17/290pairs
completedlatestcheck, onecore solvefailed retained; sourcecalibration/td/gates
unchanged. 298expandedtestsPASS4.09s. Independentread-onlygraphreviewfound
oldgraphinserts eachedge asadditive3D residualwithfixed4mmpenalty/conf1; not
calibratedcovariance, butpairededges arenotindependentinformation. No graph
launch; subsequentcontractmustcountpairgroupsandedgefactorsseparately. Full
postfreeze scoringhelper/tests delegated, no actualGTreadsbeforemanifest.

V2evidence22changedfilesnormalpushed093d44d65f01868a4ecaf7f11ac5f9835bd29085,
remote-onlyrestoreall22byteequalbackup;21currentmainfilesbyteequal,progress
intentionallyappendedaftercommit. Restored285PASS3.73s. Followupdocs/source
commitwillincludecurrentprogress/fulladapter. No claimedfullATEimprovement.

Fulladapter/code commit c4fc7c95db8defc1230b1c87bb9526f86a81f4de normally
pushed same owned sencang branch; clean remote-only restore all4 changed files
byte-identical to main and backup, restored50 new targeted testsPASS1.79s.
Full census now131/290 pairs completed, noGT read/evaluation or graphlaunch.
Postfreeze full-scoring helper implemented/reviewed, six new tests plus all
new adjacent/seam/adapter sets56PASS1.83s. Helper retains local-not-ATE warning,
true tail coverage and explicit estimation/evaluation GT provenance flags.

Read-only self-track diagnostic implemented all ten uniform final fixed-pair
B-halves. Main review found signed ray-intersection denominator bug before
interpreting results; worker fixing winding/near-boundary regressions and
will preserve initial output as superseded. No self-mask or estimator change;
running full census hashed sources remain unchanged.

Postfreeze-score helper/docs commit fe0e98534d92db663bcfc71a8b56ae07088a72e3
normalpushed ownedsencang; remote-onlyrestore4changedfilesbyteequalmain/backup,
restored56targetedtestsPASS2.35s. Signedpolygonbugfixedbeforeinterpretingv1;
supersessionmetadataretainsv1andv2regeneratedallten,6testsPASS. Independent
reviewAPPROVE/hashesvalid. Fresh4sourceinsidepolygon0;otheracceptedcases1–7,
so NO supportedforegroundrootcause/no maskchange. Census stillrunning.

Reviewer permits separate NON-promoted fixed-native-penalty graph experiment
after fullcensus freeze/scoring; cannot claim covariance-correct integration.
Newpairwrapper/all-ten baseline-joint-independent replay/tests delegated, no
existingwrapper/native/core changes. Metadatawillcountendpointfactors and
pair_level_group_count withstatistical_independence_claimedFalse, not falsely
certified effectiveindependentcovariance. All30graphsfinish BEFORE GT scoring.

310expandedmain testsPASS4.07s. Selfdiagnosticv1/supersession/v2/helper/tests/
report backed19b5c4fd088f64f432f146a97aa451b732d86f1e (8exactchangedpaths),
normalpushsencang; cleanremote-onlyrestoreall8byteequalmain/backup,
restored62targetedtestsPASS1.88s. No mask/estimator/datachanges.

Graphprelaunch reviewREQUESTCHANGES: missingall-entry hashpreflight before
processes, wrappernonemptyhash/exactcase/schedule/modechecks, truthfulseparate
independent-solve metadata and full-row/time checks. Workerfixingown4files;
no graphlaunch. Mainanalysis-only30outcome summarizerTDDREDmissingfile→5PASS,
reviewapprovedafterfailureidentitytests tightenednamedlookup/secondnonfirst
variantfailure/reversedstatus order. GT scoring and summary stillNOT run.
Fullcensus223/290pairs atlatestcheck, remains frozen/running.

FullcensusDONEexit0 all290pairs/580endpoints;253acceptedpairs/506endpoints,
independent540/580accepted. AFTER freeze fullscoringhelperexit0,476joint/
510independentscored;both30acceptedoutsideexistingGTcoverage;470mutual:
mean1.393→1.348,median.889→.837,P954.281→4.079,max18.578→13.287mm,
261/470improved. Severalcasesregresslocally; thisNOTATE/nopromotion. Report
seam_full_local_report.md;fullsourcehashesvalid. No trajectories trimmed.

Operationalerror: workerreviewfixmessageswere sentvia send_messageafteragent
completed, so fixesqueuedbutnotrunning. Detectedbylist_agents and explicitly
followup_task restarted fixturn. No graph was launched with unreviewed code.
This is orchestration error, not a data/SLAM fault; originalcensus ran normally.

Completedfull-local evidence +analysis summarizer normalpushed49301b32f198e351
915871bd1232e24d21201580,22exactchangedpaths. Cleanremote-onlyrestoreall22
byteequalmain/backup;28targetedtestsPASS. Existingmain315PASS4.02s. Fullcore
nativefusion/metricwrapper/complementarysource SHA256 matchesbackup bytewise.

Finalgraphguardfixesnowlanded11targetedPASS;newcombined33PASS0.63s. Dryreal
all30entryhash/schedulepreflightPASS84frozenpathsclosure, nooutputs/processes.
RealalltenfactorbuildpreflightPASS506joint/540independentendpoints, full1199/
1200timestampsunchanged, independent6partialpairs explicitlyrepresented.
Finalindependentprelaunchreviewpending; no realgraphlaunchedyet.

FinalprelaunchreviewAPPROVE/no blockers forNON-promoted oldfixed4mmpenalty/
conf1 allten30attempts; no covariance correctness/selector/production claim.
Main326testsPASS4.19s. Code4newfiles+2docsnormalpushed4a9d4b0a54dfb7a6bf07
bdd8ca28faf8066f143f (6changedexactpaths);cleanremote-onlyrestoreall6byteequal
main/backup,restored324testsPASS4.11s (existing2imu-scale testsnotselected).
Graphallten×baseline/joint/independent RUNNINGsession50951; output
seam_graph_full_ten_v1. AllgraphscompleteBEFORE downstreamGTscoring; no edits
tohashedsourceswhilebatchruns. New fullATE stillNOTavailable.

Fullgraphsession50951 DONEexit0. All30graph/complementary/quality/smoothstages
rc0; officialscore26rc0/fourrc3 thresholdfailures retained, not executionbugs.
Frozencomparison allbaseline maxreplaydeltasEXACT0.0, allscoringhashesvalid.
Baseline8/10worst15.333417mm; joint9/10worst13.801442; independent9/10worst
13.961496. Priorfullmetric9/10worst14.016499. No lostbaselinepasses but several
per-case maxima regress; targetNOTmet, no promotion. Graph1199/dev21200;
unchangeddownstreamVINSoverlap1142/dev21143, no new crop/drop. Independentreview
APPROVEevidence/no blockers; correctedrc3wording. Summarypending-recordguard
testREDthen6PASS: pending is not finalizedfailure. Reportseam_full_graph_report.md.

Read-onlyofficialresidualfresh4joint26consecutiveover10frames996..1021,
33.197901..34.031315s; maxidx1014vector[6.697527,-1.963217,-11.906666]mm.
Baseline63over10 in3blocks, independent31infinalblock. Not isolatedspikes.
Review confirms nine localBA centers/rotations are solved but ONLYtwoendpoint
displacements reachnativegraph; interiorgeometry/coupling discarded. Hypothesis
notprovedrootcause. GO isolatedsingle-group correlated9-center sensitivity
prototype/tests; NO more independentedges/4mm densityweightfamily. Marginalize
rotation/landmark/bias, expose rank/gauge/frame/IMUreuse/no covariance claims;
no production graph integration or realbatch until separate review.

Fullgraph628exactchangedpathsbackupafc8af6e30e7c8bba0bf697d26e9edfa87d07269
normalpushedownedsencang. Cleanremote-onlyrestoreall628byteidenticalmain/backup;
restored325PASS4.00s, main327PASS4.22s. Firstbroadstagedwhitespacecheckstopped
onoriginalrawCSVCRLF; preservedfrozenbytes, scopedcode/docscheckinstead. Not a
SLAMfailure. All180logs/30scoreplots retainedinowned26.8MBevidencesubtree.

NewshapeprototypeTDD6testsinitial; reviewREQUESTCHANGES2HIGH(rank0/gauge)
and4MEDIUM(SVDallocation/rejectionmetadata/countcoercion/bothendpointtest).
Fixedstrictfirstcameragauge/thinSVDlargepixelrows/rank0emptyresidual/strict
integers/rejectionnotgraphmetadata;9testsPASS. Remainingrank0gradientreturned
scalarandtestbroadcastmaskedshape; fixqueuedbeforefreeze. No realrun/integration.
IndependentreviewGO fixed5pairs×allten UMI-onlydiagnostic afterfix, notgraph;
append9centergeometry+sensitivity/noselector/noGT, retainrefusals/source/input/
decodedhashes/finallyhooks. Adapterimplementationboundednew2files underway.

Shapeaffineprofileextension complete: capturedresult.fun withJacobian,
singlecorrelatedWdelta+a anddroppedconstant; purelinearnuisancelstsqidentity/
gradientfinite-difference tests. NOTnonlinearprofile/covariance/4mmsigma.
Rank0gradientshape fixed andstrictfinite/count/gauge guards. Adapterexactten/
50pairscoped9nodesolve, original5nodesindependent unchanged. Fixeddeletedinput
failopen, duplicatecasecollapse, independentrowidentity, sourcehashconflicts
beforewrites, endpointvsuniquepaircounts. A mid-fix test brieflyfailed2fake
fixtures; realtempfileSHAfixtures now17targetedPASSroot0.46s. Staledocstring
correctedafterworkerREADY. Finalread-onlyprelaunchreviewpending; no realrun.
Contractshape_contract.md; then sourcebackup beforeuniformdiagnosticcensus.

FinalprelaunchreviewAPPROVE0blockers diagnosticALLTEN50pairs ONLY;17testsPASS
review0.37s. No graph/GTduringrun/selector/productionpromotion. Actualsource
backupandfreshremote-restoredtests prerequisites remain beforelaunch.

## Completed nine-center sampled diagnostic

Prototype source was normally pushed in fd55c29db0efe9e417c4d4a9a31793171c1712c2.
Clean remote-only restoration compared all nine changed files byte-for-byte;
restored 342 tests passed, main 344 (two existing unselected IMU-scale tests).

All 50 local pair computations finished. The postflight adapter exited 1 because
normal independent rows omit optional joint_pair. Preserved producer snapshots
and raw outputs; fixed only aggregation with a real-schema regression. Finalized
a NEW clone with explicit recovery provenance and no estimator rerun. Raw case
files byte-identical; 100 acceptance/reason entries and all accepted endpoints
exactly match prior v2 (maximum delta 0.0 m). 42 accepted groups, eight refused;
all accepted conditional ranks 24, zero unavailable/missing accepted diagnostics.

Independent review approved bounded post-freeze geometry scoring only, not
graph integration/promotion. Score completed: 336 non-anchor local centers,
bundle mean/median/P95/max 1.182284/0.622824/3.544572/9.167750 mm versus current
joint graph 1.214525/0.547247/3.797679/12.542609 mm; only 146/336 improved and six
case means regress. 141 evaluation hash bindings verified. Full ATE remains
13.801442 mm and target NOT met. Report shape_geometry_report.md; grouped-factor
graph design review underway, no native production changes or graph launch.

Diagnostic/evaluation backup 271622a3e7957f7a6a7514a84f81e398003969b1 normally
pushed to owned sencang. All 37 changed files restored from remote byte-identical
to main and backup; restored 352 tests passed, main 354 (two existing unselected
IMU-scale tests). Independent graph design review requests a separate grouped
consumer, exact interior correction nodes, removal of duplicate same-source
endpoint factors, explicit pixel+gyro-conditioned semantics, and linearization
displacement diagnostics. No direct native graph insertion authorized.

Bounded worker slice is now implementing a pure sparse grouped-row prototype
and synthetic tests in two NEW files only. No native fusion edits, real graph
run, GT selection, extra gyro residual, covariance claim or production promotion.

Pure grouped-row prototype complete after fail-closed index/column/scale guards,
strict frame/gauge flags, physical-oracle dense/affine derivative tests and
local-relative final diagnostics. Independent review APPROVE bounded read-only
all42 construction only. Root verified all42/10cases, max affine/physical delta
1.167955e-12 and common translation residual2.807906e-14;111 hash bindings valid.
No graph solve, GT read or pose write. Expanded main suite361PASS4.59s.

Next bounded worker slice owns only NEW full-shape adapter and test. Reuse the
already fixed uniform29pairs/case full schedule with the unchanged solver and
diagnostic capture. No real290census launch until exact-schema review, hash closure,
source backup/clean restoration and fresh tests. No native integration yet.

Pure-row source/tests/real-input preflight backed in 4ff4514241c4d41174dc7435da3ca91bf4f92547.
Normal push; eight exact changed files byte-identical after clean remote-only
restoration, restored359testsPASS4.52s. Still no native graph solve or new ATE.
Read-only prior case completion spans: old full290census1925.7s between first
and last case completion; sample50census344.9s. Full diagnostic capture is a
bounded tens-of-minutes CPU task, not another learned model training run.

Full-shape adapter built in NEW source/test files only. Review/root caught and
fixed postflight-only raw-count assumption, len29-only schedule guard and stale
five-pair label. Actual all-ten raw count/timestamps, exact literal29×9 schedule,
62 input hashes and14source hashes now verify BEFORE any solver. Known independent
optional-field contract and full29 cached schema have tests. Final17adapter tests,
36relatedPASS; final independent launch review pending. No full batch launched.

Final independent review APPROVE full290 UMI-only diagnostic capture ONLY after
backup/remote-restoration. Root actual preflight14sources/62inputs/exact10counts
PASS, expanded main378testsPASS4.65s. Prelaunch prooffull_shape_prelaunch_v1.json
frozen; no realcensus/GT scoring/graphintegration launched yet.

Sourcebackupc0e9c41b7636d1562c7c58e2e5abcbbce43730bb normally pushed. Clean remote
restoreall6changedfiles byte-identicalmain/backup;376testsPASS4.54s. Full290
UMI-only diagnostic census now RUNNING unifiedexecsession16309. Output
reports/metric_window_bundle_20260928/shape_full_ten_v1, log sibling .log.
Keep14hashedsources immutable until process completes; no native trajectory
change/no newATE/no realGT evaluation yet. Purepostfreeze fullscorer source/test
slice underway in TWO NEW files only; cannot score until fullcensus freezes.

Continuation review: full-census process remains live (at least63/290 completed),
not stalled. NEW full scorer initially passed49related tests but identity-lever
fixtures hid a BODY-versus-camera comparison bug: officialtrajectory_fused.csv
is BODY, while localtruth is leftIRcamera. Requested rotating-lever/nonidentity
extrinsic red-green regression and exactly-one body-to-camera conversion on
both sides. No realGT was opened/scored by the new evaluator, so this is a
pre-execution evaluation-code defect, NOT a demonstratedSLAM error or change
to old13.801442mm ATE. Existing sampled scorer reads camera graphposes and is
unmodified. Also tightening persistedcounts, name-keyed inputmaps and unchanged
formaltd/referenceinterpolation contract in the twoNEWfiles only.

Consumer review GO for sourceprototype design AFTER full290 freezes, NO-GO
for realgraph yet. Additional question: removing seam observations upstream
also changes native scale-derived priors. Prefer an exact-row replacement
contract if it can preserve all ancillary priors, rather than silently confound
correlation information with prior-weight changes. No new weight/cap/schedule
sweep, no production integration and no GT-based candidate choice authorized.

NEW full-scorer red-green regression reproduced68.404mm synthetic mismatch
with a rotating100mm lever under oldbody/camera mix; corrected estimate and
reference bothcamera exactlyonce nowagree nearzero. Six newtestsPASS, full
expandedmain384PASS9.32s. Independent reviewAPPROVE evaluation-only AFTER full
freeze. Reordered independent arrays intentionally validate by keyed case
identity (notzip); output nowdeclares this explicitprovenance semantic. All14
producer/62inputbindings reverified zero mismatch while live. ActualglobalATE
unchanged. Newnative_shape_consumer_contract.md records row-level replacement,
matchednodes, unchangedacceptedlist/ancillarypriors and descriptive(noGTtuned
threshold) local-linearization diagnostics. No consumer source orgraph yet.

Fullscorer source/test/design normally pushed as2aeaebaea85436d2a2f0558588879807d1d051f3.
Remote-only restored5files byte-identicalmain/backup;fresh382testsPASS8.62s,
restoreclean. Producer14/input62unchanged. Live290process still running,
threecasescomplete/heldout2inprogress; no realGT score ornewglobaltrajectory.

Independent review clarifies fullfreeze is NOT a dependency for pure synthetic
row-splice scaffolding. Approved NEW helper/test ONLY while capture continues:
nativeaccepted ordering/dimensions/groups guarded, replace only endpoint
translationrows, append correlatedrankrows, otherpriors exact, noopidentity.
Worker owns scripts/stereo_window_shape_system.py and its newtest only. No
realcontrols/GT/graph/read orproducer changes. Realconsumer/runtime remains
blocked on fullfreeze and separate review; this is not a graph-launch shortcut.

Pure row-splice helper now implemented with strict nativecolumn/rowlayout,
frozenacceptedorder/same-sourcepair mapping, allothermatrix/target rows EXACT,
no-opobjectidentity, nuisance-column nonzero rejection and noinputmutation.
Rootcaught nominal stored-zero test droppedzeros duringdense→CSR conversion;
fixture nowexplicitly storeszeroandprovesstorageunchanged. Actualnine-state
producer interoperability usesdensecoupledW/nonidentityR/affine/scale.14helper
tests/31relatedPASS; expandedmain398PASS8.44s beforelastfixture strengthening.
Independent reviewapprovedsourceonly; finalfix followup pending. Still no
realnativeconsumer/graphlaunch. Livecensus7casescomplete/fresh2inprogress.

Finalsource-onlyreviewAPPROVE; main398PASS6.86s. Exact5changedfiles normally
pushed cc9d6c708199bd8186aaf92d8ba4ed41ae244205. Remote-only cleanrestore
byte-identicalall5/main/backup;396testsPASS6.75s. Full290capture stilllive,
atleast222completed, no realGT geometry score/newATE/nativeconsumer yet.

Actualdisk pre-score check found NEWfullscorer rawcount assumption wrong:
dev1 rawcamera1199poses butofficialfusedBODY1143poses (unchanged VINS overlap).
The full1199 syntheticfixture hid this. Existingoldsample scorer read fullcamera
graph and is unaffected; newfullscorer has NOT readrealGT orproducedscores.
Requested explicitraw→fused timestamp-subsetmapping, unchangedofficialcount
binding, rawwindowelapsed checks and unavailablecoverage retention. No new
cropping/padding/tdshift; all290rawgroups remainreported. TwoNEWscorerfiles
only maychange. Full290census9casescomplete/fresh4running, producer14 immutable.

FULL290 completed/unifiedsession16309 EXIT0. Exact10cases/290pairs/580joint
endpointrows;253accepted/37refused; all253available conditionalrank24, zero
acceptedmissing/unavailable. Source14/input62 frozenbindings zero mismatch.
No newnativegraph/GTscore. Fullold/new solveridentity proof started, then
separatecorrectedfullscorer prereview/backup requiredbeforelocalGT evaluation.

Completed identity proof:290pair admissions/reasons/indices and1160joint-plus-
independent rows unchanged;1046accepted endpoint vectors BITIDENTICAL, maximum
component delta0.0m. Old/new input/decoded-image maps identical;80proofbindings
verified. All253accepted groups retain complete rank24 shape diagnostics;37
refusals retained. This proves instrumentation identity, NOT improvedATE.

NEWfullscorer now handles unchangedraw→official BODY timestamp-subset coverage
and requires actualfusion_report.json bodyorigin/count/camera/calibration
metadata (nativegraph_report is camera metadata, cannot declareBODY). Official
1142/1143/1144counts remainunchanged versusraw1199/1200. Worker11targeted and
56relatedtestsPASS;actualcorrectjointroot noGTpreflight121hashesPASS. Rootfresh
suite andindependent final review underway; no realGTscore/newgraph yet.

Rootfresh403testsPASS11.21s;actualcorrectjointroot noGTpreflight121bindings
PASS andidentity80bindings zero mismatch. Newnativewrapper firstprototype uses
fake-native tests only; rootrequested actualfrozennative syntheticcall coverage,
nonidentitylever andactualcap diagnostics beforeapproval. No actualgraph launch.

Finalfullscorer reviewAPPROVE evaluation-only afterbackupchecks;13standalone
testsPASS6.62s, actualnoGTpreflight131hashes nowalsoincludesfullCAMERAsource
trajectory count/exactrawtimestamps andstrictFalse supervisionflagswhenpresent.
Source/censusselectedbackup underway. Newnativewrapper actualfrozennative
syntheticcalls added, standalone27PASS/adjacent48PASS afterimport/order/four-
iterationguardfixes; independentrecheckpending. No realconsumergraph yet.

Complete290/scorer selected18paths backed b8506e0d7c329c827722d804939584e68deadec3;
clean remote-only restore18byteidentical, fresh403PASS9.80s. Evaluation-only
APPROVE andbackupchecks satisfied; actualfull LOCALgeometry evaluation launched
afterfreeze. This is not a newfullATE. Newmatched-node research batchSOURCE
prototype keepsall30graph+complementary+smooth estimatoroutputs frozen BEFORE
anyGTscore (stronger thanoldgraph-onlyordering);7syntheticrunner testsPASS0.42s.
CLI/wrapper review/backup required beforeanyactualbatch; no production change.

ActualfullLOCALgeometryevaluation EXIT0,141hashes reverified.233scoredgroups/
1864nonanchors;37refused+20missingexistingofficialoverlap retained. BAmean
1.389377/max12.180882 versuscurrentFUSED1.300730/max9.849770;8/10caseBAmeans
worse,743/1864pointsimprove. Fresh4pair27 raw1070 BA12.18vsfused9.85. Current
ATE13.801442unchanged. Findings doNOTsupportdirectproductionreplacement; keep
sourceprototype researchonly whileauditingposeconditioning/observationmodel.
Newfullshape batch hasNOTlaunched. RootrobustJac/fun mismatch hypothesis was
FALSIFIED forourmanualtransform+linearloss model; no unnecessaryfactorfix.

FinalindependentreviewAPPROVE sourcebackup/no-runpreflightONLY forrefiner/CLI/
runner (6ownedsource/testfiles); no realgraphlaunchauthorityclaimed. Freshmain
expanded446PASS11.06s. Actualall30commandpreflight10cases110bindings/entry,
unchangedformaltd/capsPASS, noGTopened/nooutputscreated. PureUMI-onlyall253
relativeattitude+worldrigidinvarianceaudit next; avoid mistakingthe gauge or
mathematicalconditional limitations for a demonstratedimplementationbug.

Researchconsumer/fullLOCALevidence13pathbackup a327e4d45fea0fb61a3732acdb3d749a8bba9032
normallypushed ownedsencang; remote-onlycleanrestore all13byte-identicalmain/
backup, fresh444PASS12.71s (backup444PASS10.95s). InitialTLS handshakefailure
resolvedbynormalHTTP/1.1retry, notforceorcertificateweakening. Source/no-run
approvalONLY; fullATE13.801442unchanged.

FirstUMIposeaudit preserved253accepted/37refused andpassed worldrigidinvariance
maxdelta1.972e-12. Relativeattitudemax-pergroupmedian.170747deg/P95.741991,
fresh4pair27max1.155289deg; theseareconsistencyNOTATEorrootcauseproof.
Independentreviewcaught actualinputhashverification andfrozengraphbinding
gaps, plusbool/scheduleguards; v1retained, provenancefix+v2rerununderway.

v3 now fixes realinputhash/framebinding/literalbool/scheduleandunboundcount
guards; all290rows retained,253accepted/37refused;10boundcases/257uniqueactual
files verifiedbeforeafter. WorldSE3 residualmaxdelta1.972e-12unchanged. Fresh4
pair27 relativeRmax1.155289deg/BA-native localdelta6.933210mm, notATE/Cov/causal
proof. Rootcmp30actualoldnativeCSV+graphreports+fusionreports byte-identical
cleanremotea327e4d. No solver/GT/threshold/graph/productionchange.

All10rawrecordings freshlychecked PASS, noIRskips/noIMU recorderdrops. Exposure
median7954usALL, gainmedian246infailedfresh4andsixpassedcases. Rootviewed3
failedblockframes; dark/wide/repetitivetextureandloss143→19visualtracks plausible,
butbrightnessalone notcauseproof. Userexplicitquestionansweredwiththese
limitations, notcertifyingperfectvideosoruniquelyblamingalgorithm. v3final
independentreviewAPPROVEsource/evidencediagnosticonly,11targetedPASS; selected
source/evidencebackup stillpending, no accuracygainclaim.

Completedv3source/evidence11pathbackup23526d54dd691d2fa18aaad9e6a5b940dcfdd331
normalpushownedsencang. Cleanremote-onlyrestoreall11byte-identicalmain/backup;
restored455PASS11.13s, main455PASS11.38s, backup455PASS11.12s. Noactiveoptimizer
orgraphbatch. Algorithmvsvideoquestion remainsunresolvedcausally; provenfacts
andcounterexamples writtenwithnofalsequality/10mmclaim. Existingestimatorand
allfrozenposesunchanged. Nextdiscriminatingphysical/inputtest requiresdefined
observationcriteria orpairedcapture, notanotherclosedfamilysweep.

Userasksactualinvestigationoffeatureloss. Startedbounded observational tracing
ofrawstereo stages/PnP and independentcurrentMASt3Rloglookup. Not changing
production/gates/td/frames/weights. Sourcewindowcounts vsfrontenddensecounts
distinction explicitly corrected to user; fixedall10pairs26/27/28 planned.

Root independently censused actual native bidirectional stereo reports on all
10 cases x fixed pairs26/27/28, checked all10 frozen hashes. Fresh4 pair27 has
28/30 generated edges accepted,8/8 short edges,medianPnPinliers104.5; passing
fresh3 same fixed indices has22/30 andmedian38. This does not prove same actions
or factor independence, but rejects claiming143->19 equals native collapse.
New stage tracer scaffold needed actual prep runner; worker continuing it.
One prior shell inspection exited1 because rg found no nestedAGENTS, not a
source/test failure; branch separately verified unchanged.

Opened EXISTING frontend log hooks on unchanged frozen producer/data, first
fresh4 thenfresh1/heldout1. Allthree1199poseCSVs byte-identicaloriginal;
elapsed306/265/286secs(total857; instrumentationstereodiagnosticsaddsCPUcost).
Actualexistingofficialfailure26raw1053..1078(maxraw1071=13.801442mm) has
noptmin77983/median101231; earlier1000..1052min8774. Thisrulesout current
badblocklowcount, NOTprecedingweakstatepropagationorwronggeometry.
All10nativebidirectional census remainsstagegeneration,notallusedindependent
graphinformation. Reviewer approvedbounded30stage trace after failclosedhash
andshadowidentityfixes; rootlaunchedit. Targeted23PASS, broader229PASS4.73s,
backupworktree229PASS4.26s. No pose/model/gate/weight change.

Old /tmp backup and clean-restore directories no longer existed (worktree
records prunable), but previous commit a627772 remains locally and remotely.
Created separate persistent /home/robot/ego_vio_feature_loss_backup_20260928
branch codex/feature-loss-diagnostic-20260928 fromthatcommit; no old records
pruned, no reset/forcepush/unrelateduseredits overwritten. Current new sources
copied narrowly, evidence backup pending completion of30trace.

Completed30trace:26pre-BAprepared/4expectedraw-windowrefusals,26frozen count
identities; source/input/82decodedframe hashes perrow checked beforetrace.
Root independentexact30coverage/literalindices/allrowprovenance/identity and
68uniqueactualfile hashes PASS. Initialrootchecker used nonexistent `verified`
key, corrected to actual per-source/input/decoded counts; no tracefailure.
Report updatedwith stage/cohort explanations and3byte-identicalfrontendreplays.
Explicitly correct prior143->19attribution: NEWwindowPnPadmission, notcurrent
MASt3Rdensematches. Bad26frame matching high; precedingweakphase vsbiased
geometry remain unresolved. No estimator correction/fullATEchange claimed.

Independent finalreportreviewAPPROVE: countstages/cohorts/threefrontend
identities andknownvsunprovedrootcause claims verified.27selectedpaths committed
2473057d275738c8d1772b9d45a275dff08aaebc in persistentisolatedbranch
codex/feature-loss-diagnostic-20260928, normalpushedownedsencang. Newremote-only
fetch/checkout /home/robot/ego-feature-loss-restore-J9l9eE: all27selectedfiles
byte-identicalmain/backup/remote,229testsPASS3.83s (backup229PASS3.45s).
First gitdiffcheck flagged originalCSVCRLF as trailingwhitespace; kept evidence
bytes unchanged, reran with core.whitespace=cr-at-eol PASS. No globalgit setting.
Added explicitinputindex-vs-hardwareframe numbering note to report; raw means
zero-based1199fullrateinput, source_frame_number starts30inthisrecording.

Userrequestsactualnextstageinvestigation. Rootread calibratedopt/mapupdates:
tracking changeslastkeyframe weightedcanonicalpointmap aftereachsuccess; current
pointmapandkeyframepointmaps canonicalnotmetres. Existinglogs counts/median
scale only, insufficientcommonpoint/statepropagation evidence. Bounded wrapper
andpuremath slice planned withpre/post/finalrelativeSim3 andsamepixelIDs/stereo
depths. Read-onlysourcehelperdone, puremathworkerownsseparate2files; rootwrapper
ownership. No estimator/model/td/calibration/gateschanged. Initiallookupof
toolchain/run_frontend.sh failed: correctrunner is mainworkspace
scripts/mast3r_slam_precision_workflow.sh; no furthernonexistentpath retries.

Isolatedwrapper+puremath+tests implemented and reviewed APPROVE boundedGPU
diagnostic only. Roottargeted30PASS0.19s beforeworkerprojectionfollowup;
reviewertargeted18PASS0.17s afterfollowup. Originalproduceridentityallfive
fields exact;1199priorcumulationverified. Addedpreflight2398PNGcoverage and
formaltd/estimate_td binding, and explicitfull-vs-sampledoptvalidcounts.
Anapply_patch multi-file call rejected incorrectprogresscontext; confirmedno
partialchanges, then reappliedwithcorrectcontext. No originaltrajectorychange.

Geometry firstGPUreplay completed with exactfull+online1199byteidentity. Exit1
solely becausepuremath rejected legitimate negativeborder;121samples retained,
121knownfinisherrors/0rows. Worker correctednegativeborder/native strictbounds
afterGPUfinished; recoveryhelper/tests added, separatelyreconstructed121rows
withoutGPUpose rerun. Originalfailedtrace remains unchanged. ReviewerAPPROVE
boundedrecovery+puremathfix, notaccuracy/productionrepair. Root43PASS0.22s.

Fourexactsource/testfixfiles committedf37b0673 andnormalpushedownedremote
sencang codex/feature-loss-diagnostic-20260928. Existingpersistentremote-only
restoration advancedbyfetch+detachedcheckout;all4byte-identicalmain,43PASS0.26s.
Fresh1 diagnosticreplay completed1199frames:121rows/0errors,bothfull/online
byteidentitytrue/0delta. Heldout1 samefrozenproducer replayinprogress. Pure
comparisonhelper+2tests added;45targetedPASS0.24s. NoCLI/productionmodel,
estimator, formaltd orcalibration changes;fullmax13.801442unchanged.

BothcontrolGPUreplaysfinished exit0. Eachfull+online1199byte/arrayidentity,
121rows/0errors. Purecomparisoncreated geometry_comparison_v1.json afterexact
NPZhash/framebindingguards;reviewer3MEDIUMgapsfixedbeforeoutput (sourceclosure,
explicitrecoveryvsnormalcase,121uniqueframe-boundpaths),added8guardtests.
Final53targetedPASS0.20s, reviewerAPPROVE. Sourcegridlookup no grossK/cropbug,
NNdepth-boundarylimits retained. Findingsreport writtenwithcontrolcounterexamples
andexplicitunderidentifiedcause;no newaccuracyclaim. Finalselectedbackup next.

Finalbackup whitespacecheck stopped on preservedoriginalCSVCRLF (notcodebug).
Noevidence rewritten; per-command core.whitespace=cr-at-eol check used next,
withoutchanging globalconfiguration. Independentroot363NPZhashes/12pixel
formularecomputations PASS;finalreportreviewAPPROVEfactsandcauseboundaries.

Selectedsource/evidencebackup76226fe6114fc16f0ea36a6a57b84a974dd0118d normal
pushed owned sencang branchcodex/feature-loss-diagnostic-20260928. Remote-only
fetch+detachedrestore /home/robot/ego-feature-loss-restore-J9l9eE verifiedall386
changedfiles byte-identicalmain (all363sampleNPZ included),53testsPASS0.21s;
backup53PASS0.22s. Rawrecordings/checkpoint NOTcopied. No activeGPUreplay remains.
Boundedcommongeometry investigationcomplete, uniqueATEcauseand10mmgoalnot.

User approved next investigation. Re-read fullhandoff/activeAGENTS/RTK/memory
index and skills; dirtyexistinguser changes preserved. Reusing363NPZ for
same-ray mapupdate/continuity and independentright-temporal LK closure. No
newGPU/SLAM/model/capture or estimatorchange. Read-only native continuity audit
delegated; rootowns new census/integration, no duplicate old14family sweeps.

Read-only map/right direct census completed363/363frames, source/input hash
closure intact, same-keyframe commonZexact0 all3cases. Bothcontrol andfailure
direct-LK closurebad atlongerkeyframegaps: fresh4bad26median100.105px,
PASSheldout1all121median74.040px. Cannot attribute disagreementtonativeMASt3R
when diagnosticLK itselffails. Bad26centereddepthshape improves. Boundednext
diagnosticunseeded adjacentimagechain followedbyreversechain, same363fixed
scope/all3, no thresholds/GT/trajectorychange. Sixsources/tests backed985db16a
toowned sencang, remote-onlyrestore6byte-identical/22testsPASS0.15s.

Adjacentforward+reversechain363/363complete, allconsumedinputhashes closed.
Bad26fresh4 directclosure100.105→chain2.396px, endpointdifference3.582px.
PASScontrols sameindices have chainclosure3.888/4.975px andendpoint6.248/
7.172px. Diagnosticcounterexamplesremain; nativelyincorrectcorrespondence
orfeaturecollapse NOTuniquecause. Independentroot726rawNPZhasheschecked,
chain15frame scalarrecomputations exact. ReviewerAPPROVEboundedchaincensus;
83targetedtestsPASS0.53s. Sourceaudit identifies rawdecoderproposalXkf/Ckf,
oldC andrawpostXYZ missing; weightedupdateinnovationcannotbedecomposedfrom
rayconstrainedXk/Xk_after alone. Findingsreport added; no estimatorrepair,
productionpromotion/newfullATErun;9/10PASS/fresh4max13.801442 unchanged.

Finalboundeddiagnostic/evidencebackup a83043801fe3a66c4a6dc3e363be6cbc3d76ab9f
normalpushedowned sencang. Remote-onlyrestore738changedfiles byte-identical,
83testsPASS0.52s; olderdirectsourcehashes matchbacked985db16a. NoGPUorSLAM
jobremains. Boundeddiagnosticcomplete; rawupdateproposal/confidencecapture
notyetexecuted andoverall10mmgoal/causalrepairstillincomplete.

User asked continue. Re-readhandoff/activeAGENTS/RTK/memoryindex anddebugging/
karpathy/planning skills; priorbranchfacts preserved. Next boundednative
pointmap-update proposal/weight logging replay, original3cases1199fullrate,
fixed121samples; zero production change. No VINSreplay/hardware/pkill needed.

Rawupdatepuremath14syntheticchecks added: weightedformula/nativeN/confidence,
actualproposal-versusderivedboundarySim3, metricgauge, scale-free old/proposal/
afterdepthshape, rawXYZprojectedraydisagreement (optimizerreconstraintmustnot
hideit), missingdepthUNKNOWN/malformedcapturefailclosed. Adapterimplementation
delegatednewinstalledexecutor due priorfollowup returnedoldtaskresult; explicit
newfileownership no nativeproductionedits. Capture/failurecontrolsnotyetrun.

Preflightfound realchildyaml inherits tracking.filtering_mode from native
config/base.yaml; adapterinitial guard read childonly and would falseFAIL.
Worker nowfixing effective config resolution/test. Root combined37test run hit
concurrent mid-edit mismatch(1 test old signature), not producer issue;
will rerun after workerDONE. One apply_patch onprogress usedwrongcontext and
rejected atomically; reapplied correctcontext. No replay launched yet.

Firstreal fresh4 adapter launch aborted PRE-GPU/preoutput at preflight:
RecursionError in adapter.frozen_inputs calling base.frozen_inputs after
adapter.main monkeypatched that symbol to itself. This is instrumentation
wrapper bug, not dataset/SLAM failure. Worker owns fix + active-monkeypatch
regression; no trace/output directory was created, no source/model run.

Raw-update adapter recursion fixed with captured original preflight and real
active-monkeypatch regression. Reviewer approved bounded replay. Fresh4,
fresh1, heldout1 native 1199-frame replays finished; each121 captured frames,
errors[], full+online1199 CSVs byte-identical to frozen originals. New vs
previous geometry arrays, input/producer/keyframe/hash identities pass all363;
363 weighted-formula and derived-boundary proposal checks pass, 0 unknown
stereo frames. Targeted41 tests PASS. Report and source-linked statistics:
reports/metric_window_bundle_20260928/keyframe_update_probe_v1/findings.md
and comparison.json. Fresh4 bad26 actual update median2.101mm vs passing fresh1
5.588mm; depth shape fresh4 improved proposal relative old, while fresh1
passes despite larger shape discrepancy. Weighted-map corruption hypothesis
NOTsupported; no estimator modification and frozen9/10/max13.801442 unchanged.
Read-only online-vs-final alignment shows fresh4 midblock15.051mm difference
vs controls3.929/0.421mm. This is notGT error nor backend culpability proof;
next finite boundary is exact backend re-anchoring/accepted-constraint tracing.
Selected owned-remote backup and remote-only restore pending this continuation.

Post-freeze evaluation-only official scorer on raw MASt3R full/online and same
SteamVR body reference/fixed body_T_cam0 across three cases: no-scale SE3
ATE grossly nonmetric (GT/estimate Sim3 scales .27-.47), so it is NOT fused
precision. Shape-only Sim3 P95 final/online fresh4 18.37/18.87mm, fresh1
26.57/29.39, heldout1 11.93/13.63; fresh4 bad26 median4.32/5.97mm.
Final slightly better in allthree. Direct disable-backend hypothesis rejected;
isolated local final-online difference doesnot prove backend-caused ATE.
Evaluation files saved under backend_boundary_eval, report updated. Next
boundary compares frozen MASt3R local motion with independent stereo/IMU
metric evidence, without GT-selected admission or production changes.

Selected evidence/source backup commits80bb0339 anddc1b7281 normally pushed
to owned remote sencang branchcodex/feature-loss-diagnostic-20260928. Fresh
remote-only detached restoration in /home/robot/ego-feature-loss-restore-J9l9eE
verified388 changedfiles byte-identical main/backup/remote and41 focused tests
PASS0.20s; restore status clean. Dataset symlinks/rawrecordings/modelcheckpoint
were NOT copied. This diagnostic branch is complete; all-ten accuracy goal
remains 9/10 PASS, fresh4 max13.801442mm, no production change.

Timestamp-correct metric boundary complete for fresh4/fresh1/heldout1:
product starts56–57 raw frames late; raw exposure time matching yields53/26/42
poses per three windows. Post-freeze official SE3/no-scale body ATE in bad26:
fresh4 stereo20.794/IMU7.829/graph14.796/fused13.801mm; passing fresh1
14.652/18.758/5.329/6.004 and heldout1 4.418/5.161/1.139/1.237.
Fresh4 graph/fused interval endpoint local displacement errors6.145/6.006mm
vs IMU10.265mm. Smooth position hump begins before 26-frame gate slice;
factor culprit still unknown. Same-window stereo quality and passing control
counterexample reject simple scale/confidence clamp. No estimator edit or new
10mm claim. Reviewer APPROVE with window-local/provenance caveats; three new
tests pass. Report metric_boundary_findings.md, comparison JSON plus nine
official stage-eval JSONs. Selected same-day backup/remote restore pending.

Metric boundary diagnostic was selected-backup committed00be3b6c normally
pushed to owned sencang, restored remote-only;15changedfiles byte-identical
main/backup/remote and48testsPASS in clean restore. Next read-only all-ten
saved-stage census found failed fresh4 middle-band graph correction17.54mm,
passing fresh3 40.70mm; fresh4 dense-stereo before→after3.62→1.06mm vs
passing fresh3 10.40→3.08mm. So neither correction nor stereo residual
magnitude discriminates. Native solver profile tracer then replayed all10
frozen joint graph commands with only output destinations changed; all10 graph
trajectory CSVs byte-identical, 1704-edge fresh4 count matched report.
Fixed signed-factor census fresh4 middle IMU position0.66mm, VINS-relative
2.47mm, stereo1.54mm median; passing fresh3 IMU1.02mm/stereo2.66mm.
Fresh4 relative residual rises0.50→2.47mm (~5x) within recording, but fixed
whole-recording rolling scan has eight passing cases with still larger ratios
somewhere. No GT or estimator changes, no universal internal gate; exact
upstream MASt3R correspondence/loop influence remains unknown. Evidence in
native_solver_trace_v2/README.md and two JSONs. New source/tests/evidence
backup/remote restore pending before close.

Factor trace package committed98931853 normally pushed owned sencang and
remote-only restored;56files byte-identical main/backup/remote,56testsPASS,
clean restore. New upstream MASt3R backend hook via spawned-process
sitecustomize (no toolchain edit) traced accepted edges and pre/post keyframe
poses in fresh4/fresh1/heldout1. All three full1199 final+online CSVs remain
byte-identical to frozen sources; geometry capture errors0. Event counts
fresh4 223/299accepted, fresh1 207/273, heldout1 95/131. Fresh4 anchored
tracking relative scale rises1.294(frame1043)→2.720(1057), gaps1042→1057
and1057→1074. Passing controls have scale>2 elsewhere and long gaps, so
no causal false-loop/clamp claim. Metric-loop gate absent/false in frozen
config. Report backend_constraint_probe_v1/README.md and event census.
Selected source/evidence backup and clean remote restore pending.

Read-only accepted-edge stereo probe complete. Five fresh/frozen 1199-frame
replays with two observation-only hooks preserved final+online CSV bytes and
reported zero capture errors. D405 dual-IR geometry on long accepted edges
targeting raw900..1120: fresh4 14/17 PASS, fresh1 8/12 PASS, heldout1 11/12
PASS, so failed long edges alone do not discriminate. On short edges targeting
raw1000..1120: failed fresh4 73/77 pass, passing fresh1 142/142 pass.
Fresh4 all70 short edges before1057 pass; 1057→1074 reverse metric PnP
779/1933=40.3% inliers vs existing 50% criterion, and three later short
edges also fail. This is an onboard-only local-geometry candidate, not proven
ATE cause. Existing native metric-loop gate skips consecutive edges and has no
depth in frozen config; enabling flag alone is invalid. Evidence README and
five stereo-check JSONs under backend_match_probe_v1. No production change;
frozen goal remains9/10. Selected backup/remote restore then causal test.

Selected accepted-edge diagnostic was committed b2c3da00 and normally pushed
to owned sencang; clean remote-only restore verified all468 changed files
byte-identical and11 relevant tests passed. Two isolated UMI-only causal
candidates then ran on fresh4 current-chain commands while holding frozen
seam stereo/VINS inputs fixed and re-estimating IMU metric scale from the
candidate frontend. Dropping four stereo-rejected short edges produced
max/P95/mean ATE13.942/10.237/6.223mm vs frozen current
13.801/8.715/5.554; rotation RMSE2.018 vs1.529deg. It worsened.
Passing fresh1 drop-hook control dropped0 and reproduced frozen frontend
CSV byte-for-byte. Keeping edges but masking stereo-depth-supported PnP
reprojection outliers (>4px) yielded13.817/8.699/5.558mm and1.540deg:
essentially unchanged. Neither candidate promoted or meets10mm. Internal
IMU-stereo scale agreement improved under drop but GT worsened, so not an
accuracy gate. Report causal_drop_v1/README.md; next inspect actual
keyframe pose/pointmap state around1042→1057→1074, not more edge deletion.
