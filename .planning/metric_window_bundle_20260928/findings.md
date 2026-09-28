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

Actualpartialreplay revealedstrict1e-10cachedendpointidentityfails: max1.933e-7m
infirstfourgroups, accepted/reason/admission/initialendpointsexactunchanged.
Historicalcommand proof oldcensususes/usr/bin/python3 (NumPy2.2.6); current
launch usedtoolchainvenv NumPy1.26.4; SciPy1.14.1/OpenCV4.11.0same. Initial
endpointsremainexact; LS nfev differsin3–5windows/case. This is not a sensor
failure or accuracygain. Realpairedproof10uniformfirstwindows inBOTHruntimes:
originalvsinstrumented centers/landmarks/bias BITequal; system firstwindows
matcholdcached EXACTLY; crossruntimeendpointdelta1e-11..8.415e-9m atfirstwindows.
No cached identitycheck silentlyrelaxed. Addingexplicitoptional numericreplay
proof mode with1e-6m bound (0.001mm), separatefromdefaultstrictidentity andATE.
AFTERfreeze scoreCURRENTcontrols ratherthanreusepreviouscachedreferenceerrors.

Completedcensus590windows,567accepted/23refused,0diagnosticmissing; all567
allfactorconditionalendpoint ranks3. Strictunobservability NOTdemonstrated.
Maxcrossruntimecomponentdelta2.113874232e-7m,Euclidean2.77886473e-7m; tiny
numericreplaynotaccuracygain. Explicitopt-inproof checks exactinputhashmaps,
decodedfirstwindowhashsubsets,allwindowadmission/initialendpointidentity;
defaultstrict1e-10mstillfails. Same-input10casepairedproofBITequal BOTHruntimes.

CURRENTlocalGTscoring537windows,30unscoredexplicit,median/P95/max
1.129441/4.318094/18.577697mm. Frozenallten Spearman sensitivity/error
0.802908(allfactors)/0.809269(pixelrows);within-caseallpositive0.774..0.896.
Trackcount/errorrho-0.535227, fresh4-0.724425. Supportsweaksustainedmetric
supportascontributingmechanism, notuniquecause orcalibrateduncertainty.
fresh1w42 weakaxisprojection17.955mm of18.578mm localerror,10endpointtracks;
fresh4w54 projection6.718of9.224mm,14tracks,indices1060..1080 includepeakarea.
Counterexamplefresh4w53 59tracks/error7.845mm only0.938mmalongweakaxis:
biasedobservations/referenceuncertainty/graphconsensus notexcluded.
No GTthresholdfit/edgechoice,closedfamilysweep,modeltrainingorproductionedit.
Detailedreport reports/metric_window_bundle_20260928/observability_report.md.

Phase8v1 disproves automaticbenefitfrommergingtwoindependentwindowfeatures:
joint78/100 vsindependent91/100 acceptedendpoints; same78local med/P95/max
.767/3.490/7.171mm vsindependent .553/3.160/6.218. No graphpromotion.
Posthocboundarypixelmatching yields0–3sharedtraininglandmarksinseveralpairs.

Fixedseam-bornbidirectionalcontinuousLKv2 improvesactualsharedsupport:
fresh4lastpair1058..1098 commontrain1->9 rank3,19added(14train/5withheld).
Alltenv284/100acceptedvs78v1,independent91. Same78v1/v2local med/P95/max
.767/3.490/7.171 -> .593/3.203/4.520mm;48/78better. Same84independent/v2:
.815/4.342/7.182 -> .743/3.417/6.028mm;42/84better. Coverage/refusalsretained.
Inputs/decodedpixels/independentinitial+optimized endpoints EXACTLYunchanged
alltenverifiedbyfrozencomparison.json. NoGTreads duringestimation,scoreonly
afterfreeze,unchangedcalibration/timelever. ThesearelocaldisplacementsNOTATE.

Still2geometry/2model/1solve/3rawpairfailures; doesnotestablish10mmwholeSLAM.
Reviewerapprovesfullpaired40stride40alltenUMI-only next, NOTgraphpromotion.
Endpointpairs correlated,notcalibratedcovariance; graphcontractneedsseparate
reviewwithpaircounts,endpointcoverageandfailures,neverGT-admissionselection.

Full290paircensus weakens small-v2 extrapolation: same470endpoint localmaximum
18.578->13.287mm; mean1.393->1.348,median.889->.837,P954.281->4.079mm,
261/470improve. Fullgraphs (sameofficialSE3/time/reference, all30beforeGT):
baseline8/10max15.333417, joint9/10max13.801442, independent9/10max13.961496.
BaselinereplaymaxdifferenceEXACT0allten. Previous9/10max14.016499. No claimed
10mmSLAM, covariancecorrectness, generalization or promotion. Allfailureoutputs
retained; jointfresh4remaining26consecutiveframesmostlyZoffset, notsinglespikes.

Read-only code review confirmed structuralcompression: nine localBA states are
solved in a sharedpixel+gyro model, but downstream receives onlytwo20-frame
endpointvectors. Interiorbow can satisfybothendpointconstraints. This proves
informationloss, NOTcauseoffresh4error. Bounded nextdiagnostic retainsone
correlated9-center factor, notmore independentlyweightededges/densitysweep.

Affine local-profile algebra reviewapproved: original transformedresidualr,
centerJacobianJc,nuisance(rotation/landmark/bias)Jn. NormalizednuisanceSVD spanQ:
C=(I-QQT)Jc, b=(I-QQT)r. C=UsVt; W=sVt, a=UTb. Groupedresidual a+Wdelta
preserveslinearizedprofilecost gradient/Hessian; leftover ||b-Ua||² isconstant.
Keepconstantifreportingprofilecost. ThinSVDavoidslargepixel-row MxM matrices.
Zero-centeredWdelta alone losesbaselineprojectedgradient; capturingresult.fun
now avoids anothercensuslater. This is NOTexact nonlinearprofiling, rawpixel
covariance or 4mm meter-sigma: original_residuals manuallysoftrobustifypixels,
least_squaresuseslinear loss, and result.fun/result.jac describe THATtransformed
objective. Ranknullaxes/gauge/IMUreuse explicit, no statisticindependenceclaim.
Syntheticexplicitnuisance-lstsqprofileidentity/rankdeficiency/gradient tests
requiredbeforealltenfixedfivepairdiagnostic. No graphintegrationauthorityyet.
## Nine-center diagnostic: local information is mixed, not ATE completion

All ten cases/fifty fixed pairs computed, 42 accepted. The capture preserves
original estimator acceptance/reasons and endpoints exactly against cached v2;
every accepted grouped diagnostic has numerical rank 24, but this is not a
calibrated uncertainty or correctness claim. Post-freeze local relative geometry
max is 9.167750 mm versus existing joint graph 12.542609, but only 146/336 centers
improve and six case means regress. Final fresh4 pair also has mixed interior
improvements; local endpoint maximum differs from full-ATE peak location.
Full-trajectory worst ATE remains 13.801442 mm. No promotion, no GT correction.

Operational fault: mandatory joint_pair on independent normal rows was a new
adapter aggregation assumption, not a data/trajectory fault. Preserved raw
producer outputs and pre-fix source snapshot; new finalized clone explicitly
names producer and finalizer hashes and computation_rerun=False. No repeat
estimator computation was needed to recover annotations.

The full-shape adapter must distinguish recording raw count from pair count:
1181–1198 frames also yield29 complete stride40 pairs, so merely checking29
would falsely label them1199. This was caught before launch. Allten actual
timestamp counts and the literal29×9 schedule are now checked before any solver;
unknown/extra/cropped/nonmonotonic input and shifted-but-still29 schedules fail.
No accepted trajectory is cropped to make its graph/window contract fit.

## Full290 actual local score: do not extrapolate sampled maximum improvement

Completed253accepted/37refused groups. Fullpostfreeze1864nonanchorcenters from
233groups: BAmean1.389377/median.789917/P954.512902/max12.180882mm; current
officialFUSED relativegeometry1.300730/.734268/4.534526/9.849770. Eightcase BA
meansworse,743/1864pointsgain.20groupsoutsideexistingofficialoverlap retained.
Fresh4pair27 raw1070 BA12.180882vscurrent9.849770; supportsnodedrop143→19,
heldoutpixelRMSE1.727452→1.642413, trainingRMSE.573653. No unique rootcause
proved, no GT-selectedweights/productionpromotion. Shapegraphsourceprototype
mustnotbeclaimednewATE; currentfullmax13.801442unchanged. Report
reports/metric_window_bundle_20260928/full_shape_diagnostic_report.md.

Rootrecheckedrobustresidual/Jacobian consistency suspicion. OfficialSciPy toy
indeedmismatches Jmodified.T@rawfun whenlosssoft_l1, butOURbundle explicitly
manuallytransforms residual andusesdefaultLINEARloss. This NEGATES thehypothesis
forourmodel, agreeingwiththeearlieraffinealgebranoteabove. No frozenfileedit.

## Recording integrity is not a millimetric image-quality certificate

User asks whether a day without accuracy improvement really establishes an
algorithm fault rather than video quality. It does NOT establish that dichotomy.
Root freshly read fresh4 raw session `d405_720p_rgb_stereo_ir_20260927_182007`:
acceptance.json PASS, authoritative DB3 counters no skipped/repeated/reset/
regressed frames; IMU16001samples/399.993998Hz/zero recorder drops. This is
recording transport integrity, not a visual-information guarantee.

IR metadata: exposure min/median/P95/max7954us; gain median/P95/max246, min237,
configured limit8000us/248. Close to those operational limits is descriptive,
not proof of blur or unusability. Root viewed existing dataset raw1040/1070/1080
leftIR PNGs: near-object/patterned mat view changes into wider dark desk/wall
view with repeated mat texture and largely blank surfaces. Visual assessment
only; no quantified blur/noise-depth causal claim. All frames remain retained.

Actual samegroup27 support143/113/107/101/199/122/75/34/19, heldout pixelRMSE
1.727452->1.642413, but local BA does not solve its metric geometry discrepancy.
Thus lost persistent metric support/observation-model mismatch remains plausible;
no basis to certify video perfect or solely blame the trained model/algorithm.
Earlier sensitivity/error association and fewer tracks support investigation,
not causation or a GT-selected frame-removal rule. Do not discard recordings
merely because full ATE fails. Need a discriminating physical/internal test or
paired acquisition intervention before attributing the unique root cause.

Source audit confirms local BA includes stereo pixels+gyro/bias ONLY; no
accel-preintegration/velocity/gravity/accelbias state. Native downstream graph
already includes those translation equations. Missing local accel coupling is
an architecture hypothesis, not proof of the current error cause and not an
excuse to repeat closed graph-weight/scale-mode sweeps.

Rootfurtherchecked all10rawacceptance reports: allPASS, left/rightskipped0,
IMU recorderdrops0, exposuremedian7954usALL. heldout1/2/3+fresh1/2/3 have
samegainmedian246asfailingfresh4 yetpasscurrentjoint10mmmaxgate. Therefore
exposure/highgain alone NOTsufficientcause. Needtemporal/spatialsupport and
algorithmphysicalmodel distinctions; do not simplycall darkimagecorruptdata.

Newfeature-loss investigation: sourceproves143→19 are supplementalwindow
`admitted_train` counts after train-only PnP, not MASt3R frontend dense matches.
Windowtracks use cumulative temporal validity (one temporal failure persists),
per-frame stereo/depth/epipolar gates, train/heldout split and PnP-inlier admission.
Counts alone cannot separate these stages. Frozenmemory also includes a DIFFERENT
09-20 recording where frontendmatchcollapse hypothesis was falsified; do not
re-use either that older negative or currentwindowcounts as proof about latest
fresh4actualMASt3Rmatches. Investigate actualcurrentfrontend logs independently.

Final30stage-trace completed:26pre-BAaccepted/4raw-windowrefusals, all26count
identity;8input/14source/82decodedframe hashes perrow verified; root68uniqueactual
filehashes and30exactcoverage checksPASS. fresh4pair27 secondhalf180initial
points atlastnode:58cumulative temporal/32LR/2stereoLK/8epipolar/19depth
exclusivefirstgate failures,61pass. Joinedlastnode56trainvalid->19PnPadmitted.
Middle199=60A+106B+33seam; final19=0A+16B+3seam, notsamecohortsurvival.
Shadow originalarrays identity preserved, noBA/graphthreshold change.

Threelogging-only frontend replays all1199CSVbyte-identical originals, elapsed
306/265/286s. Actualfresh4 bad26raw1053..1078 noptmin77983/median101231
of147456, i.e.min52.89%/median68.65%. Prior1000..1052 min8774 (5.95%);
passedfresh1 lastfixedwindowmin8.18%. Counts alone NOTcausal/failedblockcollapse.
Do not confuse diagnosticSim3relative_scale or canonicalpointmapdepthscale
withglobalmetretrajectory drift. Needcommonlandmark geometric consistency and
keyframe-state propagation investigation, notblindmodeltraining/re-recordrequest.
Finalreport feature_loss_findings.md distinguishes knownlossmechanism from
unproveduniqueATEcause; fullmax13.801442 unchanged.229testsPASSnotaccuracyPASS.

Commongeometrysourceaudit: calibratedget_points_poses returnsXf[idx_f2k],Xk;
rowkey iskeyframepixelID, idx_f2k selects currentpixelID. optcalib solves
CURRENT->KEYFRAME Sim3 andmeas_k=[griduv,logXkz]. ThenIMUblendupdatesrotation,
followedby keyframepointmapupdate(Xkk,Ckf). Existingcanonicalpoints notmetres.
Rootwrapper snapshots same2048uniform optimizevalidrows/pre/post/finalposes and
postupdatekeyframeZ; nooriginalargs/results mutated. Puremathworkeradds
stereo backprojection/relativeR/residuals, notoptimizerorGT.

IMUpriorCSV rows arePER-FRAMEincrement notabsoluteorientation; main rightmultiplies
T_WC*delta. Rootcumulation testusesnoncommutingX/Zrotations torejectwrongorder.
Existingconfig/model/toolchain/dirtydiff/lietorch exactfrozenproducer verified
beforeGPUlaunch; correctlietorchsourceis thirdparty/lietorch (notroot/lietorch).
Full/online trajectoryidentity required, notonlyfinalpostgraphoutputs.

Geometry probe runtime: firstfresh4 fullfrontend completed1199frames, bothfull/
online originalCSV byte/arrayidentical,maxdelta0. All121NPZ saved but pure
diagnostic mistakenly rejected native合法pixel_border=-10; originaltrace has
121finish errors/0rows and is preserved asFAILED. Fixed pureprojection only:
anyfiniteborder accepted, bounds exactnative >border and <width-1-border.
Recovery binds originalfailedtrace+121NPZ+frontendlog+correctedmath, checks all
originalhashes except math unchanged; separate recovered_v1 has121rows/0errors.
NoGPU/pose rerun. Samplehashes firstfrozen at recoveryentry, not atGPUcapture.

Fresh4 bad26frames showpost pixelmedian(framewise)0.663668px, visual-vs-integrated
gyro relativeR median0.306343/max1.587843deg, commonstereo3D translation-centered
residual median11.472458mm. Currentcorrespondences numerous; somegeometric
inconsistency exists but doesNOTidentify uniqueATEcause. Learnedtranslation
vsstereo robustmedian max2.791431mm inbad26frames, not13.801442ATE. Gauge-dependent
comparison mustNOT be substituted forglobaltrajectoryprecision. ActualSim3
scale2.084at1053 is compensated bypointmap-to-metricdepthscale; metricconsistency
1.000965, so rawSim3scale isNOT proof ofworldtrajectoryscalejump.

Fresh1control frontend finished1199frames, full+onlinebyteidentical/0delta,
121capture rows/0errors. Samefixed1000..1120 relativeRmax1.088712deg andindividual
common3Dresidualframe medianmax12.769365mm despitewholetrajectoryPASSmax8.582967.
Thiscounterexample means isolatedcount/rotation/3D residual peaks notunique
failure detectors. Heldout1control running; no estimator changes ornewATEclaim.

Heldout1controlcompleted1199frames,121diagnosticrows/0errors,full+onlinebyte
identicaloriginal. Fixed121frames relativeRmedian0.618878deg (higherthanfailed
fresh4.198264deg) butfullATEPASSmax6.026661mm. Keyframeagemedian36vfailed4,
anothercounterexample tooldkeyframealonecause. Three-case frozencomparison
created;53targetedPASS. Reviewerrequested sourceclosure/recoverysemantics/
unique frame-boundNPZpathguards; allrepairedandreviewAPPROVE. Thisisdiagnostic
notoptimizerchange, no10mmgain. Currentalltenstill9/10,fresh4max13.801442mm.

Pixel-grid audit: native1280x720->512x288no crop, Kscale2.5 consistent;actual
K[[259.6828,0,255.376],[0,259.6828,141.8192],[0,0,1]]. StereoNNnominalsampling
diff<=.5nativepx/axis; depth-edge/occlusionerror notbounded. No grossK/gridbug
found;NNfootprintdiff alone NOTATErootcauseproof. Currentbadblockcommondepth
median.399m vsfresh1sameindex.301/heldout1.309; actualmotiondiffers, no direct
crosscasecausalityclaim. Nextindependent correspondence/stereodepth consistency
testmust separate depthnoise frompriorstate propagation beforeestimatorrepair.
