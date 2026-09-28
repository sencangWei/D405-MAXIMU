# Pixel-level stereo/gyro window estimator candidate

User approved 2026-09-28: develop a UMI-only multi-frame metric candidate;
formal production remains unchanged. Goal: all ten frozen regressions max<10mm,
no regression of eight prior passes; fresh independent captures after freeze.

Current 2026-09-28 continuation: fixed3×121 original geometry samples,
same-ray map propagation and independent right-image temporal evidence.
DirectkeyframeLK completed363frames but its own closurebadalsoinPASSheldout1;
adjacentforward+reversechain diagnostic added to test this limitation. No
diagnostic disagreement is an accuracygate or permissiontochange trajectories.

## Constraints

- No SteamVR/robot poses in estimation, edge selection, or confidence.
- No closed14 family sweeps, per-case tuning, data removal, or altered ATE gates.
- Fixed formal td=-0.009109323 once; body/leftIR frame semantics preserved.
- Read recording-specific stereo calibration, 30Hz full trajectory output.
- New information must be raw multi-frame stereo pixel observations / metric
  landmark geometry, not reweighted existing displacement edges.
- Preserve existing dirty work and source reports. No production promotion until
  frozen all-ten evidence. Same-day owned remote backup + restoration evidence.

## Phases

1. Complete: inventory existing BA/tracking/covariance code and avoid duplicate
   implementation. Review minimal mathematical contract and failure policy.
2. Complete (23 tests, review follow-up pending): synthetic contract tests before code: metric scale, rotation/lever,
   gauge fixation, finite/rank/cheirality guards, robust outliers, gyro soft not
   hard fixed; no-MASt3R-shape or GT dependency.
3. Implemented, NOT production-accepted: isolated minimal estimator using shared landmark multi-frame left/
   right pixel reprojection with soft calibrated raw gyro constraints. Use
   existing scipy/OpenCV, no new dependency or frontend/GPU rerun for this prototype.
4. Complete for prototype (v6,46/50): uniform time-stratified window observation controls on ten cached
   recordings before any single-case full trajectory promotion. Raw data and
   optimizer diagnostics first; scoring isolated afterward.
5. Two frozen all-ten graph batches complete, goal NOT met:
   only with supported metric observations, frozen graph integration
  candidate across all ten. Record any derived-confidence/interface effects.
   Add accepted v6 endpoint factors to the previous frozen SIFT-LM/gyro candidate,
   with a separate no-learned-scale factor contract. Keep native graph sigma,
   all production source files, and original factor confidence unchanged.
   Uniform five windows per case, no GT-selected locations; evaluate all ten.
   First graph:9/10PASS, zero loss of priorpasses; fresh2max11.323→8.837mm,
   fresh4max15.333→14.983mm. Next same-estimator non-overlapping20-frame raw
   windows across allten, no confidence/weight/cap/keyframe/model sweeps.
   Fullcoverage567/590factors:9/10PASS, fresh2max8.128mm/fresh4max14.016mm;
   old8passes retained. No per-case candidate selection or production promotion.
6. Complete for prototype and first graph: review/tests171PASS, owned sencang backup commits
   d725776f/8514a442; fresh remote restore83files byte-identical and146testsPASS.
   Graph integration verified on allten; fullcoverage evidence backup18c37c4a
   and clean remote restoration verified178changedfiles/171testsPASS.
   Current bounded integration branch complete; overall accuracy goalNOTmet.

## Stop/decision conditions

## Phase7 — approved endpoint observability diagnostic (complete; not a correction)

Hypothesis to test, not a proved root cause: some stereo endpoints fit pixels
while motion is weakly constrained after landmark/rotation/bias freedom is
marginalized. Implement isolated Jacobian diagnostics, not new trajectory gates.
Success: synthetic rank/unit/nuisance tests; identical solver endpoints and
acceptance with/without instrumentation; frozen all-ten uniform-window census
before any external-reference association. No weights/thresholds/frame deletion.
Use the existing fullcoverage window schedule; capture optimized Jacobians via
an isolated adapter, leaving previously hashed core/production source intact.
Report rank, endpoint weak axes, provisional unit-normalized-residual response,
and endpoint track/depth support. This is not calibrated covariance or a
millimetric confidence certificate; correlations with external error may be
examined only AFTER freezing diagnostics and cannot set estimator selection.

Completed590/590windows;567accepted/23refused,0diagnosticmissing. Allaccepted
endpointconditionalranks3: strictunobservabilityhypothesis NOTconfirmed.
Within-runtimerealpairedproofs alltenfirstwindows BITequal; crossNumPyreplay
strictidentityfails,explicitproofmode reportsfalseexactidentity withmaxcomponent
2.113874232e-7m (0.000211mm),no ATEgatechange. Currentfrozencensus537localGT
scores show sensitivity/errorSpearman0.803, positiveallten; persistenttrack
supportweakenedproblemwindows. Counterexamplesremain, notuniquecauseproof.
Fresh248testsPASS; no trajectorychange orproductionpromotion. Repairdirection:
cross-windowpersistent/reseededlandmarkjointgeometry, separatelyboundedand
validatedbeforealltenfrozengraphreplay; notconfidence/weighttuningfromGT.

## Phase8 — approved adjacent-window shared geometry (in progress)

Minimal structure experiment, NOT a closed-family density/weight sweep:
two adjacent20-frame windows use all41raw frames and nine BA states. Existing
source trackers/solver remain frozen. Second-window stereo detection supplies
new birth landmarks; mutual one-to-one boundary stereo pixels link repeated
landmarks across both windows. Boundary pose/pixel observations are single-count.
Joint gauge=A start; B newborn initialxyz/pose transformed by A INITIAL PnP end,
not optimized/learned/GT pose. B heldouts matching A inherit A labels; train-only
PnP before solve. Heldout depths anchored at their birth stereo, never fitted.

1. Pure merger + tests first: nonzero rotation/translation, birth visibility,
   one-to-one ambiguity, single-count, inherited holdout and geometry contracts.
2. Fixed5time-stratified PAIRS×allten raw-observation controls, no GT reads;
   compare same input independent solves against joint solve. Joint failure is
   explicit, no fallback claiming joint success. Shared training landmarks must
   span both sides; native four noncollinear geometry support before solve.
3. Freeze hashes/admission/diagnostics before separate localGT evaluation;
   do not pick windows or cases by scores. Correlated two endpoint factors are
   explicit, not calibrated independent confidence. Same noise/td/solver gates.
4. Only if internal consistency supports the architecture, full all-ten coverage
   and old-parameter graph replay; max<10mm and no lost priorpasses required.
5. Independent review, selected same-day backup and remote-only restoration.

No production promotion or confidence reweighting. Birth points behind A gauge
remain explicit nativeguard rejection, not silently deleted. Full-rate outputs
and unchanged external evaluation contract preserved.

Observation-only improvement is not SLAM acceptance. Reject an estimator that
does not improve independent geometric consistency or is unobservable. Do not
run a large graph batch solely on a successful example. Record blocked data or
mathematical assumptions; ask only for meaningful new authority/input.

### Phase8 v1 decision and v2 continuation

Fixed5pairs×allten v1 completed: joint78/100 endpoints, independent91/100.
After freeze, same78 local-reference displacement comparisons: independent
median/P95/max .553/3.160/6.218mm, joint .767/3.490/7.171mm,33/78 improved.
Not full-trajectory ATE; NO graph or production promotion. Geometry refusals
often0–3 shared train points, including fresh4 lastpair1. Separate corner
detection plus near-pixel merging failed to ensure physical track continuity.

Continue approved shared/replenishment direction with fixed seam-born points:
detect NEW stereo corners at rawseam20 after7px exclusion of ALL existing seam
pixels (trainandheldout), then samepointIDs tracked backwards20/forwards20raw.
Supplementary rawpre+post support, then sampledBApre+seam+postfour-noncollinear
train gate. Original primary rawtracker20points gates/noise/td/core untouched.
Initialize new3D through A INITIAL trainPnP seam pose, never optimized/learned
orGT; combined train-onlyPnP9poses, fixedGFTTidmod5 withheld labels. No oldjoint
successfallback on newfailure. Independent controls remain exactlyv1 inputs.
First fixed5pairs×allten, UMI-only freeze BEFORE localreferenceevaluation.

### V2 all-ten result and full fixed-cost graph control

V2 fixed5pairs×allten freezes84/100 endpoints (42pairs) vs independent91.
Same78 v1/v2 local displacement max7.171→4.520mm,48/78 improved. Same84
independent/v2 max7.182→6.028mm,42/84 improved. This is LOCAL displacement,
not full ATE. Review approves full UMI-only40raw/stride40 census,29pairs/case;
true38/39 tail frames receive no new factor but are NOT removed from SLAM.

After full census freeze and independent local scoring: separately reviewed
engineering control uses unchanged native0.004m fixed penalty/confidence1,
allten×(baseline,joint,independent), no GT-selected weights or windows. Finish
all30graphs before any GT scoring. New wrapper must verify exact all-ten inputs,
schedule, calibration, all full-trajectory timestamps/rows, and preserve refusals.
Joint two-endpoint groups are correlated; independent controls declare separate
solves. Count endpoint factors and pair groups, never claim a calibrated
covariance or independent information count. This experiment is NOT promotion
authority even if numeric max passes: covariance-correct integration needs its
own design/evidence review. Baseline replay and full official ATE contract are
checked, and all-ten outcomes retained including failures.

Read-only foreground diagnostic did not support a jaw-track root cause for
fresh4 in the uniform last-pair B-half. No input masks or estimator change.

### Phase8 completed full graph outcome; Phase9 bounded shape contract

All30 baseline/joint/independent graphs and downstream scores completed.
Baseline8/10PASS/max15.333417mm, joint9/10/max13.801442, independent9/10/
max13.961496; baseline replaymaxdelta0 inallten. Alloriginaleightpassesretained,
notuniformimprovement, 10mmgoalNOTmet/no promotion. Finalfresh4joint26frame
continuous mostlyZ offset33.198..34.031s. Reportseam_full_graph_report.md.

Review confirmed discardedintermediateinformation: nine local BA centers and
landmarkcoupling reduce to two endpoint displacements. Next approved bound:
standalone single correlated9-center nuisance-projected sensitivity prototype,
syntheticcorrectendpoints/bowedinterior test and exactsolveridentity tests.
No existingcore edits or graphweight/density sweep; no GT inputs or covariance
claim. Expose rank/nullaxes and firstcamera frame/gauge; preservecorrelation.
Zero-centered sensitivity alone is not exact nonlinear profiling (baseline
projectedresidual/gradient absent), so prototype cannot be promoted as exact
Schur measurement. Subsequent all-ten UMI-only diagnostic and graph interface
would need separate review/freeze/evaluation. Back up currentcompletedgraph
evidence before newestimator integration work; overallgoal still active.

Phase9 affine prototype/tests+all-ten50pairadapter implemented. Captured fun/Jac
also retain projectedbaselinegradient via affine_offset+droppedconstant, not
onlyzero-centeredWdelta. Syntheticexplicitnuisance-lstsqprofile/gradienttests
verify LOCALlinearquadratic only, neverexactnonlinearprofile/covariance. Final
independentreviewAPPROVE boundedUMI-onlyten50pairdiagnostic, no graph/GT/selector.
17newtargetedPASS; sameformaltd/gates/nativecore. Contractshape_contract.md.
Back up source beforelaunch; freezeall50 before any externalgeometryevaluation.

Phase9 sampled diagnostic complete and frozen. Aggregation-only optional-field
bug recovered in a new explicitly-provenanced output directory, no solver rerun.
42/50 accepted pairs expose full-rank 24 grouped geometry. Post-freeze evaluation
of all eight non-anchor centers per accepted group is mixed, not a production
accuracy result: local max 9.168 versus graph 12.543 mm, 146/336 points improve.
Do not infer full ATE PASS. Preserve/back up these results and obtain a separate
graph interface review on gauge, linearization, objective scale and gyro reuse
before integration; no GT selection or closed-family weight/density sweep.

Separate design review permits only a pure grouped-row prototype next. Force
all nine indices into graph correction nodes; represent one correlated affine
block in the first-camera frame, including camera-only global scale derivative.
Synthetic bowed-interior/correct-endpoint test, rigid world-frame invariance and
finite-difference derivative checks must pass. Current profile remains explicitly
pixel+gyro-conditioned and diagnostic-only. Do not insert it into the native
endpoint observation list, append duplicate endpoint evidence or run real graphs
without a further integration review.

Pure row algebra now passes synthetic and all42 real-input construction checks;
it does not produce estimator poses. Before a real consumer variant, capture the
same diagnostic on fixed full uniform29pairs×allten (290 total), retaining eight
or more refused groups without fallback. Underlying solver/gates/inputs must
exactly reproduce the prior full census. Both summaries validate before either
is annotated; strict independent optional-field contract, all source/input hash
closure and exact uncropped full pose count retained. Back up/review the new
adapter before launch, finish all290 before any external scoring. This is new
correlated information capture, not a weight or density sweep.

Full290 complete and endpoints/admission bit-identical tooldfullcensus. All253
acceptedrank24diagnostics retained. ActualfullLOCALgeometry doesNOTsupport
directreplacement: BAmean1.389/max12.181mm vsfused1.301/max9.850,8/10case means
regress; targetfullATE still13.801442. Isolatedrefiner/CLI/runner source reviewed
andtested, sourcebackup/no-run30command preflight ONLY approved, no realbatch
orproductionpromotion. Next discriminator: ALL253 relativeBA/native attitudes
andactualrigid-world invariance, UMI-only nooptimizer/noGT/no thresholds. Gauge
orposeconditioning remains a hypothesis, notprovenrootcause. Separateevidence
review needed before spending another fullgraph batch.

Completed the bounded all253 UMI-only pose audit. The first-camera local gauge
is world-SE3 invariant (max residual change1.972e-12); the implementation is
not disproved by rotation marginalization alone. Relative BA/native rotations
max-pergroup median.170747deg/P95.741991, fresh4pair27max1.155289deg. These are
not ATE or causality. v1/v2 are preserved pre-guard audit artifacts; use v3 with
actualfile checks, formal frame/td/session/rawcounts/timestamps, strict accepted
bools and exact29pair schedule. All10boundcases,257uniqueactualfiles verified.

Latest user asks whether the failure is algorithm rather than video quality.
All10rawcapture PASS/noIRskips/noIMU recorderdrops. Sixpassingcases share failed
fresh4exposure7954us/gain246, so neither recording integrity nor highgain alone
establishes the cause. Keep video/observational quality as an unresolved factor;
do not discard videos because they fail ATE. Read-only report
recording_integrity_vs_visual_information.md separates integrity, visual support
and causal limitations. A paired acquisition/light/exposure intervention would
need user physical participation; no newcapture or exposure change is assumed.

Do not launch a new graph/weight/scale sweep merely because infrastructure now
exists. Next architectural intervention must identify actual new translation
information and be reviewed against existing downstream accel constraints.
No final optimizer/pose changes or 10mm claim in this audit branch.

## Current request: identify why effective features are lost

User explicitly asks to investigate, not merely restate uncertainty. Execute
bounded image/track diagnostics; do not modify estimator behavior. No full
frontend/BA/VINS rerun or GT-based selection. Critically,143→19 belongs to NEW
stereo-window training/PnP support, not MASt3R dense matching count. Trace both
subsystems independently before assigning causality.

1. Read actual tracking/filtering/initialization and current frontend evidence.
2. Build isolated observation-only instrumentation, preserving original return
   arrays/thresholds and finally-restoring hooks. Tests for exact identity,
   overlapping/first-gate reasons, rejection retention and source/input binding.
3. Independently review source, then run fixed pairs26/27/28 (1000..1120)
   in ALL10frozenrecordings, retaining every refusal. No local BA optimizer.
4. Freeze all30 traces; compare loss stages, spatial/disparity support and newly
   detectable points. Report direct facts vs hypotheses and missing metrics.
5. Same-day selected backup with remote restoration. Root cause of full ATE
   is established only by a discriminating intervention, not point-count
   correlation. Any actual algorithm repair requires causal evidence plus
   all-ten full official validation; 14closedfamilies remain closed.

### Feature investigation completion and bounded replay exception

Original frontend logs lacked dense-match metrics. To answer the actual current
recording question, performed three narrow same-config/model/toolchain/data
frontend replays with EXISTING logging hooks: fresh4, fresh1, heldout1. This
overrides only the diagnostic no-frontend-rerun line above; no parameter sweep,
training, closed-family rerun, VINS/BA/graph rerun or GT-based selection. All
three 1199-frame output CSVs byte-identical to originals, maxpose delta0.

Fixed10x3 tracing complete:30/30 unique rows,26pre-BAprepared/4rawtracking
refusals retained, all26counts exactly frozen-summary identical. Root independently
verified all30 coverage/arrays/provenance fields and68uniqueactualfile hashes.
Bad26frames raw1053..1078 min77983/median101231 dense optimized matches; prior
raw1000..1052 min8774. Feature-count-collapse IN the bad block falsified;
preceding weak-state propagation/geometric bias NOT distinguished or repaired.
229regressiontestsPASS; fullmax13.801442 unchanged, accuracygoalNOTcomplete.
Selected same-day source/evidence remote backup and clean restoration next.

## Current request: common-landmark geometry and state-propagation probe

User explicitly asks to carry out the next investigation, not another plan.
Systematic-debugging + karpathy + existing file-backed plan apply. Hypotheses:
prior weak observations alter propagated keyframe state, versus current accepted
matches having biased geometry. Neither is yet established. No production edits,
GT supervision/selection, td/calibration/threshold/model/weight change.

1. Read actual calibrated tracking/map-update boundaries and unit/frame semantics.
2. Isolated runpy/monkeypatch diagnostic wrapper: same opt arguments/return objects,
   fixed raw input indices1000..1120, uniformly sampled optimizevalid pixel rows.
   Capture pre/post/final relativeSim3, exact corresponding current/keyframe pixel
   IDs and learned XYZ, both-view independent stereo depths, and post-update
   keyframe XYZ. Record IMU relativeR from existing prepared priors, not GT.
3. Pure diagnostic maths/tests: calibrated pixel/log-depth residuals, relativeR
   consistency, metric common-point 3D residuals and learned-map update. Canonical
   scale diagnostics must not be called global metre drift or absolute accuracy.
   Missing stereo evidence remains UNKNOWN, notzero. No optimizer correction.
4. Review/test/freeze wrapper/source/config/model/dataset hashes; same-config full
   frontend replay first fresh4 then fresh1/heldout1 controls. Require1199full-rate
   trajectory and byte-identical original before treating logged state as original.
   Stop causal attribution if identity/provenance fails. No BA/VINS/fusion rerun.
5. Compare same landmarks pre/post/final and update; retain all121frames/case,
   not only ATEpeaks. Distinguish direct facts from underidentified causal claims.
   No fix/promotion until causal source identified plus fullallten validation.
6. Same-day exact selected code/evidence backup to sencang with remote restore.

Bounded commongeometry diagnostic completedthreecases: fresh4 savedNPZpure
recovery (originalnativeSLAM completed butdiagnostic borderbug retainedFAILED),
fresh1/heldout1 successfulcaptures. Each121samples/full+online1199byteidentity.
Source/math/summaryreviewAPPROVE;53targetedPASS. Pixelresiduals small while
metricgeometry imperfect; controls also have3D/rotation disagreement, so unique
causeNOTidentified and neitherlowcurrentcount noroldkeyframealone explainsFAIL.
Nominaldepth/frameK gridconsistent;NNdepth-edge error notbounded. No production
fix/estimatorchanges/fullATErerun;fresh4max13.801442 and9/10unchanged. Report
frontend_geometry_probe_v1/geometry_findings.md. Selectedbackup76226fe6 normal
pushedowned sencang;remote-onlyrestoration386selectedfiles(includingall363NPZ)
byte-identicaland53testsPASS0.21s. Boundeddiagnosticbranchcomplete;overall10mm
optimization/uniquecause identificationNOTcomplete.

## Current continuation: shared-ray propagation versus independent correspondence

User approved continuing the preceding causal investigation. Reuse saved363
NPZ captures; no GPU/model/SLAM replay or production source changes initially.
Success criterion for this bounded diagnostic: verified sample identity and
same-ray semantics, fixed all121frames on each of threecases, independent checks
that discriminate a map-update mechanism from temporal correspondence problems.
None of these residuals may be relabeled ATE, covariance, or unique cause.

1. Read native before/after point-map and shared-memory update semantics.
2. Quantify before/after geometry on exactly the same finite stereo rays,
   separating fixed-scale displacement and scale-free depth shape. Verify
   successive same-keyframe samples at shared pixel IDs; unsupported intersections
   remain UNKNOWN. No varying-cohort cumulative-error inference.
3. Independent RIGHT-image temporal LK, unseeded by MASt3R, with forward/backward
   closure diagnostics. Stereo depth supplies right pixel coordinates from
   exact native NN sample positions and recording-specific focal/baseline;
   compare predicted current right position to MASt3R-derived temporal link.
   Retain all statuses/unknowns; LK disagreement alone is not proof of wrong SLAM.
4. Synthetic translation/rotation/unit/gauge and source/coverage tests, review,
   fixed363frame diagnostic census before any external ATE association. No GT
   data read, optimized poses, threshold/weight/density sweeps or frame deletion.
5. Only introduce an estimator repair if a discriminating mechanism is supported;
   otherwise report the exact uncertainty/counterexample and next finite test.
   Any repair must then pass the unchanged fullallten evaluation.
6. Selected same-day backup to owned sencang, actual remote restore + tests.

## Current continuation: raw keyframe update proposal (approved)

Capture missing native update boundary, not an estimator intervention. Use
fresh4/fresh1/heldout1 frozen producers, each full1199frames but diagnostic
inputscope1000..1120 anduniform2048 optimizevalid pixelIDs. Log raw oldXYZ/C,
native matched/working decoderXkf/Ckf, actual transformed proposalXYZ/C,
raw newXYZ/C and updateN. Verify native weighted_pointmap formula and preserve
full+online trajectories byte-for-byte. No production/model/td/config/weights
changes, no external poses or GT-selected interpolation. Firstcase then both
controls before attributing rootcause. If logging changes produceroutput or
coverage, reject the diagnostic rather than presenting a fix. Freeze/hashes,
synthetic tests/review, separate saved-sample statistics, same-day ownedremote
backup+actualrestoration. Overall allten10mm criterion remains unmet.

Completed first three-case native replay and uniform 363-sample comparison:
all traces/noerrors/source and prior-array identities/full+online byteidentity
PASS; 0 weighted-formula or boundary-transform failures. Passing fresh1 has
larger actual map update and worse depth-shape error than fresh4's failed block.
Reject simple keyframe weighted-update corruption as a discriminating cause;
no estimator fix or accuracy gain. Read-only online/final anchored comparison
instead raises backend re-anchoring as an underidentified next boundary;
prior cases show some online errors too. Findings: keyframe_update_probe_v1/
findings.md. Owned backup/remote-only restore remains to finish this branch.

Post-freeze evaluation-only same-reference raw-MASt3R check: all three final
Sim(3) shape P95 values are better than online; fresh4 bad-block median final
4.32 versus online 5.97 mm after separate global Sim(3) fits. MASt3R itself
has nonmetric scale, so none of these is fused ATE. Reject direct backend
disable/online switch. Next diagnostic must distinguish local MASt3R motion
from independent raw stereo/IMU metric constraints before estimator changes;
the current product is still 9/10 PASS with fresh4 max 13.801442 mm.

Backup done: selected code/evidence normal-pushed to owned `sencang` in
commits 80bb0339/dc1b7281 and independently restored from the remote.
All 388 changed files match main/backup/restored bytes; 41 focused tests pass
in the clean restored checkout. Current observation-only branch closed;
overall precision objective continues at the next metric-motion boundary.

## Current continuation: timestamp-correct metric-stage boundary

Matched raw D405 exposure times, not product row indices (which start 56–57
frames later), across fresh4/fresh1/heldout1. Using the official fixed-body,
SE(3)-no-scale post-freeze scoring, fresh4's 26-frame interval is 20.794 mm
stereo metric, 7.829 mm IMU metric, 14.796 mm joint graph, 13.801 mm final.
Graph improves the interval's *local displacement* despite the larger absolute
position ATE, so neither deleting the graph nor switching to IMU is justified.
Passing controls and full-trajectory scores confirm that. Reviewer approved
the timestamp/metric contract; no estimator changed, 9/10 gate unchanged.
Evidence: keyframe_update_probe_v1/metric_boundary_findings.md and JSON.
Next bounded test is unchanged-output graph per-node/per-family residual tracing
on all ten frozen cases, followed by UMI-only cross-sensor discrimination;
no GT-driven admission or per-case weights. Back up this diagnostic to owned
remote and verify clean restoration before extending it.

Completed that bounded test: an external profile hook captured native solver
state in ten full frozen graph replays, with byte-identical graph trajectories.
The fixed all-ten factor census and 5-frame-stride rolling scan show fresh4's
relative-motion residual grows ~5x in the failed interval, but eight passing
cases exhibit larger same-schedule ratios somewhere; fresh4 is not worst on
IMU/stereo residual or correction magnitude either. No reliable UMI-only gate
from these tested magnitudes; do not promote a threshold/interpolation fix.
Evidence: native_solver_trace_v2/README.md, factor census and scan JSON.
Next separate branch must observe MASt3R upstream correspondence/loop constraint
influence directly, with controls and unchanged all-ten official gate after a
source-supported structural fix. Precision goal remains 9/10, not complete.

## Errors

- Explorer role unavailable (`gpt-5.3-codex-spark` account unsupported). Used
  installed executor role for the same bounded read-only lookup; no model sweep.
- Phase8 initial lookup used nonexistent stereo_window_observations.py; correct
  source is prepare_stereo_window_observations.py, found from existing runner.
