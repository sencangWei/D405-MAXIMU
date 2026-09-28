# Pixel-level stereo/gyro window estimator candidate

User approved 2026-09-28: develop a UMI-only multi-frame metric candidate;
formal production remains unchanged. Goal: all ten frozen regressions max<10mm,
no regression of eight prior passes; fresh independent captures after freeze.

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
   existing scipy/OpenCV, no new dependency or frontend/GPU rerun.
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

## Errors

- Explorer role unavailable (`gpt-5.3-codex-spark` account unsupported). Used
  installed executor role for the same bounded read-only lookup; no model sweep.
- Phase8 initial lookup used nonexistent stereo_window_observations.py; correct
  source is prepare_stereo_window_observations.py, found from existing runner.
