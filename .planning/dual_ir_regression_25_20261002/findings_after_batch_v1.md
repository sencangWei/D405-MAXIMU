# Evidence-driven continuation (2026-10-02)

User authorization: automatically continue optimization after all 25 finish;
do not wait for another permission handoff. Existing v1 dependencies stay frozen
until its systemd job finishes. No sensor recording or service manipulation is
needed for the following offline work.

## Verified reference contamination: Sep29 take03

- All eight candidates peak near 222–224 mm; stereo-only also fails.
- Historical VINS-only score already peaked at 216.86 mm.
- Peak reference index 715, camera time 1790682778.9639378.
- Body reference consecutive steps at 715/716/717: 232.22/173.48/106.09 mm.
- Raw Tracker at host_monotonic ~94706.7575–94706.8157 already has steps
  210.50/97.32/109.45/102.07 mm, BEFORE the body extrinsic or clock mapping.
- Camera intervals remain ~33.338 ms; estimate steps remain ~3–6 mm.
- Source Tracker hash and frozen reference provenance match historical scoring.

Conclusion: the huge common peak is contaminated external reference, not a
learned-motion error to optimize against. Preserve the recording, original
precision report and 25-source denominator. Do NOT substitute estimate-derived
GT, trim the peak to make it pass, or report all-25 success. Separate the remaining
ordinary centimetric residual from this invalid-reference spike.

## Isolated cache/adapter failures (not raw-video rejection)

1. Sep27 ind2: right long-hop scale 0.413151 vs primary 0.389777 differs 5.82%;
   each report is PASS, but merge rejects the whole eye at the 5% agreement check.
   Left four scales differ only ~1.16%. Candidate fix: keep primary, reject only
   inconsistent optional reports, retain explicit rejected-report diagnostics.
2. Sep29 take02: right dataset 1199 frames, tracked/dense manifest 588. Left
   metric cache also partial (~587 rows). All four left stereo reports PASS.
   Partial raw tracking is not an input-session mismatch. Candidate fix: permit
   raw tracked coverage <= dataset count with explicit coverage; no interpolated
   artificial observations. Evaluate the full common VINS camera timeline.
3. Sep29 take04: right primary/long/dense PASS, 328/84/399 accepted observations;
   multisecond report FAIL right_stereo_scale_unobservable, relative_p90_p10=.681.
   The derive rc=2 aborts the whole eye. Candidate fix: retain failed optional
   artifact but don't consume its factors; keep the three valid reports.
4. Sep29 take05: all 1190 candidate pairs rejected as translation_excitation_low,
   zero metric-scale observations. Missing downstream reports are consequences.
   Don't fabricate scale. Explicitly label scale unobservable; consider a
   validated VINS-only degraded output, never claim a full learned-fusion solve.

## Accuracy observations—not a promoted parameter choice

- 10 mm correction cap decreases passing-record count, not a safe default.
- Per-edge 10/15/25 mm learned consistency gates have not improved total passes.
- Large learned self-residual alone does NOT mean SLAM failure: Sep27 heldout2
  own-residual P95 ~71.98 mm still yields both max 6.28 mm.
- Conversely heldout4 own-residual P95 ~8.54 mm gives both max 10.46 mm.
  Do not extrapolate a single absolute residual threshold from take6.

## Next safe branch

After v1 finishes: targeted regression tests first, opt-in optional-report
degradation and raw-coverage correction, reuse already computed right frontends,
rerun every affected record and several prior passes, then run the full corpus.
Keep source failure labels, same timing/extrinsics, and no GT graph inputs.
Only then pursue accuracy changes with multi-record internal evidence.

Goal workflow: `.omx/goals/performance/dual-ir-25-10mm-20261002/`.
No goal completion or all-25/generalization claim is currently justified.

## Frozen baseline closed at 18:48 local

Systemd exited with code 3 as expected for retained preparation failures.
All 25 sources processed, 19 both-policy scores, 15 precision PASS, four scored
failures, six unscored. The 13 frozen dependencies had zero hash changes at close.
`baseline_acceptance_v1.json` preserves the fresh audit performed BEFORE edits.

Next experiment repairs adapters only (not graph equations or cap settings).
`config/dual_ir_regression_25_20261002_adapters.json` retains all 25 identities
and frozen scoring references and links the unabridged baseline by content hash.
It reuses 21 complete right caches and three raw frontends chosen only by file
completeness; one primary-scale-unobservable item remains explicit failure.
No source recordings or historical reports are overwritten.

Independent case comparison separated three precision failures:
- Sep30 take06: both 15.352 mm vs stereo-only 8.398 mm; internally weighted
  learned-vs-stereo residual remains high when both eyes are simultaneously bad.
- Sep29 take07: both 13.038 mm vs stereo-only 13.105 mm; not learned override.
- Sep27 heldout4: both 10.463 mm vs stereo-only 11.273 mm; learned helps here.
A future temporal learned-reliability hypothesis must be defined on the same
onboard windows for ALL recordings; localization using GT is diagnosis only,
not a deployable gating rule. It cannot be assumed to fix stereo-only failures.

## Read-only accuracy diagnosis while adapter-v2 is frozen

- The apparent heldout4 VINS baseline conflict is a label error, not a source
  mismatch. Historical 9.024/9.960 mm estimates are left-only fused outputs,
  not raw VINS. Their relative-motion input is the same current VINS CSV,
  formal config (`td=-0.009109323`, `estimate_td=0`), recording and reference.
  Current symmetric graph is a different algorithm recipe. Do not promise that
  reproducing the historical raw input will reproduce the old fused result.
- A predeclared all-duration temporal rule (fixed +/-1 s, >=8 paired edges,
  joint own-residual-bad fraction >.25, weighted residual P95 >15 mm) did not
  flag Sep30 take06. Do not implement it or quietly lower its threshold.
- Duration-stratified onboard census of 24 available graphs reveals that
  Sep30 take06's >=1 s edges have joint-bad fraction .445 and weighted P95
  22.17 mm, versus zero joint-bad short edges. The candidate explanation is
  that pooling long and short relative-motion constraints hides the long-edge
  problem. This remains a hypothesis; a fixed-rule, final-factor-confidence
  census is required before experimental integration.
- Numerical audit reproduced take06 and heldout4 baseline positions within
  .91 nanometers. All four LSQR solves terminate at istop=2. Comparing the
  identical systems to column-scaled sparse direct solves changes positions
  by at most .000109 mm / .000061 mm: numerical precision is not their
  millimetric error source.
- Sep29 take02 does hit the LSQR iteration limit (istop=7, 5000 iterations).
  The same-system direct solution differs by at most .159 mm per IRLS solve;
  this is a real convergence-diagnostic gap, not an explanation for 18.7 mm
  ATE. Instrument/validate convergence separately; do not call it the main
  accuracy fix or loosen the trajectory acceptance threshold.

No GT-driven window selection, graph/reference edits, sensor capture or live
service transition occurred during these SLAM checks. Frozen production
dependencies remain unchanged until the full adapter-v2 batch closes.

## Adapter-v2 full corpus closed (fresh strict audit)

All 25 processed; 24 scored, 17 max-10mm PASS, 7 scored failures, one explicit
primary-scale-unobservable preparation failure. All 13 frozen source hashes
still match. `adapter_acceptance_v2.json` records strict FAIL before any new
accuracy code. Correction-cap counts on these same 25: uncapped17, cap10mm10,
cap25mm16, cap40mm17, cap100mm17. No cap is promoted.

Six ordinary scored failures have maxima 10.463, 14.022, 18.702, 11.457, 13.038,
15.352 mm. The seventh is the separately verified raw-Tracker-contaminated
223.479 mm reference case, retained in the denominator and original scores.

The preregistered duration-specific census exists under
`duration_reliability_census_v1/`: 24 graphs, source hashes, no GT inputs. The
fixed rule flags 13/24, including both the failing Sep30 take06 and passing
Sep27 heldout2. Therefore detection alone is not acceptance. Next is a bounded,
globally fixed factor-space ablation with existing passing controls; no frame
deletion, trajectory smoothing, GT replacement or per-record thresholds.

## First fixed duration-gate ablation and replay validation

The first complete single-case probe (Sep30 take06) changed maximum ATE from
15.351919 to 15.217702 mm: only 0.134216 mm improvement, still FAIL. It zeroed
378 long learned factors (189 pairs), retaining all 1143 scored timestamps,
the original stereo factors and unchanged external reference. No promotion.

The first full replay was stopped after 12 records for independent-review
input-contract repairs. Its partial artifacts remain under
`segment_probe_batch_v1/`, with explicit `termination.json`; its RUNNING
summary is not evidence of a completed batch.

Two apparent rotation mismatches were quantified across all 24 available
baseline estimates: maximum SO3 serialization difference 1.911278e-9 rad,
timestamp difference zero, first-node serialization difference <=1.229169e-9 m.
The replay validator now tests SO3 differences against 5e-9 rad and still
rejects a genuine 1e-5 rad change. Gauge/schema/identity lever/primary-eye and
shared-scale-state guards were also added. These are artifact validation fixes,
not a relaxed trajectory precision threshold. Fresh targeted tests: 114 PASS;
independent review approves only the bounded experimental replay.

`segment_probe_batch_v2/` is the new full-25 globally frozen duration-gate
experiment. No factor math in the 13 original dependencies was changed.

## Next isolated geometry hypothesis (not accepted)

Current learned relative-motion edges use endpoint-dependent world alignment
`A_i = R_vins_i R_track_body_i^T`. Even if the original learned camera positions
are globally closed, using a different A_i for every edge can break closure.
The read-only 24-record census found take06 cycle closure up to 4.604 mm, but
also a passing take05 case up to 5.273 mm: closure magnitude is NOT a validated
failure selector and must not become a per-record GT-derived rule.

A separate pure experiment will fit one SO3 world gauge per eye from onboard
synced orientations, preserve metric camera position differences, and use
VINS physical body rotations only for the camera-to-body lever term. This
telescopes around cycles by construction. All 24 caches can be reconstructed
from existing source-bound reports. Confidence, scales, stereo observations,
correction limits, reference timestamps and scoring extrinsics remain frozen.
Constant gauge can smear real orientation drift; synthetic geometry tests and
full-corpus score comparison are required before any production change.

## Full duration-gate experiment rejected

`segment_probe_batch_v2/summary.json` closed 25/25 with 24 scores, 17 max-10mm
passes and the same one preparation failure. All 15 frozen hashes still match.
No previously failing trajectory passed. Sep29 take08 (a pass) changed max
6.089 to 6.510 mm; heldout2 changed 6.277 to 6.476 mm. The main take06 change
remained only 15.352 to 15.218 mm. `segment_probe_batch_v2_comparison.json`
retains every record and both summary hashes. Decision: reject production
promotion; no further threshold tuning in this family.

Experimental module/runner/tests and compact diagnosis evidence were committed
as `59c3573f88286e435593e7b97fbab5cbb25a7a32`, non-force pushed to the owned
`sencang` branch, fetched and restored to a fresh temporary directory: all ten
files match local content SHA-256, zero mismatches. This backs up an experiment
and its diagnostic capability, not a completed 10 mm algorithm.

## Constant-gauge first case and controls (before full25)

Actual cache reconstruction reproduces baseline learned factors exactly (zero
vector error) in all three checked records. This isolates the changed gauge/
lever geometry from source preparation, factor membership or confidence changes.

- Sep30 take06: maximum 15.351919 -> 15.104560 mm; still FAIL, 1143 samples,
  overlap 1.0. Mean 3.279452 mm and P95 5.838234 mm. Small benefit is not a
  complete diagnosis or fix for the remaining peak.
- Sep27 heldout2 (passing control): 6.277 -> 6.184248 mm, 1141 samples,
  overlap 1.0, remains PASS.
- Sep27 heldout4 (near-limit control): 10.463471 -> 9.568733 mm, 1143 samples,
  overlap 1.0, now PASS under the unchanged scorer.

`constant_gauge_first_controls_comparison.json` binds the three estimates,
source factor identity checks and both probe summaries by hashes. It is
development evidence, not a three-sample generalization claim. Fresh tests:
143 PASS. The separate full25 fixed-policy job is now running under
`constant_gauge_batch_v1/`; no production runner has been changed.

## Constant replay cache binding repair (2026-10-02)

Full v1 closed with 25 records, 18 scored, 15 PASS, six NEW cache reconstruction
failures and the known primary-scale-unobservable case. The six reconstruction
failures must not be misreported as trajectory regressions: manifest cache
hints referred to the earlier failed left cache in four cases; two right hints
were null. The baseline artifacts already bound the actual successful caches.

The isolated runner now resolves the unique per-eye metric trajectory from the
validated baseline input hashes and rechecks loaded source hashes. Missing or
ambiguous bindings are rejected. Red/green tests reproduced the stale-hint/null
failure; independent review approved only this bounded experimental repair.
First fixed Sep29 take02 reproduces 1335 original factors with zero vector
error; its maximum is 18.719518 mm, still FAIL. Fixed controls Sep27 dev1 and
Sep29 take06 pass at 6.524297 and 6.520835 mm respectively. A fresh full25 replay
is running under `constant_gauge_batch_v2/`; v1 evidence remains intact.

The two-file cache fix was committed as
`77105629f89417c0b889282d8aa26ff96631a402`, pushed non-force to the owned
`sencang` branch, fetched, and both files restored/compared byte-for-byte under
`/tmp/umi-gauge-cache-fix-backup-20261002.BtWgu6`. This is not production
promotion or a claim of full-corpus precision acceptance.

## Physical stereo lever experiment: corrected census and pre-score failure

The v1 lever census did not exactly reuse current merged-scale confidence and
optional report rejection; its P95 must not be quoted as a pure lever effect.
Corrected `stereo_lever_geometry_audit_v2/` rebuilds all 35254 shared rows in
24 scored records, zero selection failures and zero raw duplicate matches.
Lever-only displacement changes: global P50 0.090883 mm, P95 0.300520 mm,
maximum 2.342799 mm. This supports a geometry consistency test, not a claim
that this small term explains the remaining 15--19 mm trajectory peaks.

First real physical probe take06 was rejected BEFORE scoring because raw
camera indices/times were incorrectly assumed to be exact VINS indices/times.
`physical_stereo_first_v1/` keeps the failed evidence. The adapter/module are
being repaired to preserve original timestamps and reproduce current nearest
reference mapping and same-eye dedup before LR selection. Independent review
also requires output-factor hash binding for the combined constant-gauge
variant. No production change or score filtering is enabled.

## Fixed constant-gauge full25 result and next information source

`constant_gauge_batch_v2/summary.json` closed COMPLETED_WITH_FAILURES, 25/25
records, 24 scored, 18 PASS, six scored FAIL and one scale-unobservable input.
At job close MainPID was zero, ExecMainStatus 3, and all 16 frozen code hashes
still matched. Baseline had 17 PASS: heldout4 is the one new pass and no old
PASS became FAIL. Not all metrics improve: Sep29 take02 maximum changed
18.702402 -> 18.719518 mm, take08 6.088607 -> 6.135901 mm. Worst raw-reference
contaminated take03 remains explicitly present at 223.956860 mm; it is not a
valid estimate of ordinary SLAM precision and is not removed from this audit.
Take06 still fails at 15.104560 mm. NO production promotion/full-goal claim.

Physical-only v4 first plus two controls now all score with complete timelines:
take06 15.017590 mm FAIL, heldout2 6.278972 mm PASS, heldout4 10.354881 mm FAIL.
The adapter preserves raw timestamps, maps reference indices, and mirrors
current unused-candidate gap filtering without dropping any original shared
row. Same-eye duplicate ambiguity is a visible failure, not an approximation.
`physical_stereo_batch_v4/` is the next frozen full25 run.

Independent rotation census binds 24 actual adapter-v2 baseline sources:
69760 accepted raw free-PnP observations, 69619 mapped within the current 10ms
rule; no constrained/IMU-copied rotations. Relative free-PnP vs VINS angular
discrepancy P95 is 0.8048 degrees, max 7.7246 degrees. LR transported rotations
are numerically identical: right stereo reports derive the same physical
stereo measurement. Do not double-count them as independent rotation factors.
No point correspondence/inlier sets are saved, so this supports a relative
SE(3) pose-factor pilot, NOT point-level BA without extracting new inputs.

Independent architecture review confirms current position solver fixes R and
cannot jointly represent this rotation/translation information. Next isolated
pilot will optimize body SO3 poses and positions together with one shared
stereo SE(3) factor per pair, gyro relative rotations, fixed VINS short-motion
and learned displacement priors. No GT, scale fitting, correction cap or score
selector. Pilot explicitly omits acceleration/velocity/gravity states and is
NOT a complete production visual-inertial replacement. Synthetic validation,
first case plus controls, then full25 are required before promotion.

After v2 closed, constant runner was amended ONLY to publish the output factor
hash required by combined-probe provenance. Red test failed on absent field;
fresh complete targeted suite after the one-field fix reports 179 PASS.
Old completed v2 artifacts are not mutated/relabelled to add this field.

## Pose-only SE3 pilot and full batch close (2026-10-02)

Physical lever v4 full25 closed: 24 scores, 17 PASS, one primary scale-unobservable
input. All16 frozen hashes matched at job close. Constant gauge v3 also closed
25/25,24 scores,18 PASS, six scoredFAIL,one unscored, zero frozen hash changes;
it repeats v2 mathematics and adds output-factor hashes, not new precision gain.

New isolated solver/extractor/runner pass 198 targeted regression tests. Peer
review approved bounded first1 + controls only, NOT production. Raw free-PnP
rotation is carried through the existing shared stereo selection without
counting derived right-eye geometry twice. Fixed-R and joint-R,p controls use
identical factors and fixed declared weights. Camera-only gaps retain trusted
VINS endpoint deltas; gyro bridge requires raw IMU coverage/sample count/hash,
max gap10ms and formal noise sigma gyr_n*sqrt(dt). Formal td remains -0.009109323.

Read-only source preflight passed heldout2,heldout4,Sep30take06:1141/1143 nodes,
all original shared rows retained,1493/1675/1494 stereo factors and matching
gyro intervals. Raw observed IMU gaps below2.55ms;heldout2 has two camera-only
66.67ms gaps, not IMU outages. Root runner separately checks full timestamp
identity, finite SO3 poses, unchanged first-pose gauge and frozen estimate hash
before scoring; nonconvergence yields unscored failure, never fallback.

`umi-joint-stereo-se3-first-v1-20261002.service` launched Sep30take06; real
precision result is pending. Output `joint_stereo_se3_first_v1/`. Scope remains
pose-only: acceleration,velocity,gravity absent. No claim of10mm/full visual-
inertial repair. Production code/GT policy untouched; strict all25 goal active.

### Combined close and first-pilot numerical failure

`gauge_physical_combined_batch_v1/summary.json` closed25/25,24 scores, zero17
frozen-code hash changes,MainPID0/ExecMainStatus3. Physical-only control still17
precisionPASS; combined gauge+physical has18 completeprecisionPASS,19 maximum
translation<=10mm. Sep29take07 now max9.863734mm but stillFAIL on rotationRMSE;
do NOT count it as fullPASS. Sep30take06 max14.756369mm remainsFAIL. RawTracker
contaminatedtake03 remains223.760108mm and primary-unobservabletake05 unscored.

Firstpose-onlypilotv1 fixed-R exhausted200 nonlinearfunctionevaluations without
convergence (cost241.755201->220.360981), so it was NOT scored. Generic nonlinear
finite-difference solver is inappropriate for this fixed-R linear control.
Ownjob stopped,MainPID0/inactive/dead verified; originaldiagnostic/results
retained and explicittermination.json says incomplete. Nextnumericalrepair
uses exactsamefactorloss/weights: direct sparsefixed-R solution and vectorized
jointresidual. This is infrastructure correction, NOT acceptedprecisiongain.

NewSE3 capability committed56cd9127530a41a534965a21b40189b5e423b44c and pushed
nonforce toowned sencang/codex/dual-ir-frontend-20261001. Fetchedremote restored
to/tmp/umi-joint-se3-backup-20261002.ZsrP8S; all8changedfiles byteidentical.

Independent combined-evidence review confirms no oldbaselinePASS lost, all24
scored timestamps/quaternions/overlap counts unchanged and referenceprovenance
unchanged except expected artifact paths. Exacttake07 rotationRMSE2.213955deg
comesfrom position-basedSE3alignment; outputquaternions didnotchange atall.
Attitude-alignedrotationRMSE0.825579deg and rotationRPE~0.29720deg unchanged.
Keep currentfullscoreFAIL; do not silently substitutealternativealignment.

Exactnumericalrepair passed peerreview and202 fresh targetedtests,compile and
diffcheck. Scalar/vectorized residualequality and sparsefixed-R vsdenseleast-
squares tests hold; originalJacobian sparsity unchanged. No factorweight or
scoring change. 400nodeperturbedfixed-R benchmark36.71s->0.06455s,LSMRstatus2
converged; numericalspeedup is NOT precisiongain. Firstv2take06 restarted under
900sboundedownCPUservice `umi-joint-stereo-se3-first-v2-20261002`, realresults
pending. Originalfailedv1 artifacts remain untouched.

Firstv2closed1record/2variantsboth unscorednonconverged,19frozenhashesunchanged,
MainPID0/Exec3. ActualfixedR matrix16863x3426 has101169nnz; LSMRhardcoded2000
iterations exhausted,normar0.00068728,normr3.143876,conditionestimate254.776.
Joint200nfev exhausted,cost241.755201->73.397931. Do not score partialsolutions
or claim thisinternalcost improvement as precision. Nextread-onlybenchmark
must use thisrealmatrix to establish convergencebudget/preconditioning before
anothernumericaledit; synthetic400nodes was insufficient performanceevidence.
Numericalrepaircommitcc095dfafe76d0ff950a1939d597781d0ca576fc backed nonforce
toownedremote,fetched and restored4filesbyteidentical at
/tmp/umi-se3-numerical-backup-20261002.E2qzyz.

RealAread-onlybenchmark: graph-derivedLSMRbudget13704stopped9492/status2,
normalgradient6.18e-6,cost220.36089165; sparse normaldirect0.0099s finite,
normalgradient6.94e-11,cost220.36089077 (sameLSQRoptimum). Thus hardcoded2000
budget was unsuitable, not a changedobjective. FixedRexactinit +x_scale=jac
joint200nfevstillfailed after74.77s,optimality1.51499,cost73.379526: do NOT
blindly increase nonlinearbudget as a precisionfix. NextanalyticJacobian is
for sameposefactorloss; standardSO3Jacob formulas checked against primary
https://arxiv.org/pdf/1812.01537 eq143-146 and independentcentraldifferences.
Reviewer derivativeerror max2.20e-9 forrotation/gyro and~4.6e-10translation.
Runtime/precision gains still need actualsamefailedcase+controls verification.

### Verified numerical conditioning repair, not precision acceptance

Actual take06 analytic-Jacobian solve still exhausted 200 outer evaluations with
default inner LSMR tolerances. A bounded no-GT benchmark isolated numerical
conditioning: exact gradient-verified fixed-R position initialization,
`x_scale='jac'`, inner LSMR atol/btol1e-10 and maxiter4*nvars converged by ftol
in4 outer evaluations,2.69s,cost220.360891->73.376338. No weights, factors,
outer convergence tolerances, timeline, gauge, or scoring policy changed.
Implementation repeats this result in2.59s; fresh root targeted suite206PASS,
compile and diffcheck clean. This proves numerical operability only; actual
frozen first case plus two independent controls must score before any precision
claim. Joint pilot remains pose-only, no acceleration/velocity/gravity states,
and production is not promoted. All failed previous pilot artifacts retained.

First-v3 closed3/3 and six variants converged; original19 frozen hashes match.
Joint max errors heldout2=5.352177mm PASS, heldout4=10.287017mm FAIL,
Sep30take06=13.751503mm FAIL. Full25-v1 then closed25/25 with24 scores and one
original unobservable source; fixed-R13PASS,joint-R14PASS versus baseline17
and combined18full/19translation. Joint loses three baseline passes:
Sep27ind4 9.973->13.074mm,Sep29take01 9.383->11.928mm,
Sep30take03 9.871->10.086mm; no old failure becomesPASS. Reject production
promotion. This pilot is not an apples-to-apples replacement of the established
fullVI p,v,g,bias/IRLS graph: it intentionally omitted those states and used a
plain pose objective. Next integration must retain established fullVI constraints
and prove original-R control identity before assessing extra rotational data.

Numericalrepair4fed23fa943f8fb0e38ce1a251b199bf11ab492b backed nonforce toowned
sencang/codex/dual-ir-frontend-20261001; fetchedremote restoredthreefiles
byteidentical at/tmp/umi-se3-conditioned-backup-20261002.ugqzNu. Fresh206tests
and independent boundedpilot review passed. No code/promoted policy changed
while first3/full25 ran; precisiongoal remains active and failed.

Independent source audit confirms fixed-R13 vs joint14 alone does NOT test the
new R information inside the established backend: experimental weights differ
(stereo sigma12.5mm vs4mm, learned200mm vs8mm, VINS1000mm vs8mm), plus no
velocity/gravity/accelbias/preintegration/IRLS and raw ungauged learned vectors.
Do not tune those experimental weights to conceal this mismatch. Next staged
single-R feedback retains complete prior VI graph and recomputes gauge/lever
vectors; original-R arm must reproduce combined output byte-exact or stop.

Corrected diagnostic: an agent's first12-14deg local PnP conflict was an inverse
rotation mistake and is discarded. Proper Z.T@(Rj.T@Ri) gives localPnP P95
heldout4~0.54deg,take06~0.65deg, consistent with original rotation census.
Take06 remainingbad indices353-355(11.76-11.83s) lie within20-21 longedges but
have0short/midlocalstereo support and endpointincidence0,1,0. Objective local
stereo residualP95~0.023mm despite externalpositionerror13.75mm: internal
residual alone cannot certify absoluteaccuracy. Source learned-vs-stereo
displacementP95~17.61mm remains a real frame-consistent disagreement. Heldout4
has2badnear-finalnodes, no>=30framefactors at tail. Weaklocaltopology is a
diagnostic lead, NOT proof that filling any gap will fix precision.

New isolated staged rotation feedback wrapper retains the existing complete
VI solver and all its weights/IRLS. It recomputes constant gauge/physicallever
factors with original-R control then frozenjoint-R feedback, consuming no
pilot positions. Original-R control must byte-match closedcombined estimate
and vectors beforefeedback. All original expected inputhashes fromfourupstream
candidates guard rawIMU/referenceCSV/sourceartifacts throughout construction
and scoring; mutation is visible STOP_CODE_CHANGED. Fresh214targetedtestsPASS,
compile/diffclean, independentreview approves boundedfirst3 only thenfull25
afteractualcontrolidentity. First3 launched under ownbounded CPUservice
`umi-joint-r-vi-feedback-first3-v1-20261002`; no precisionclaimyet.

Source rejection audit: both eyes near take06badwindow each367observations,
31accepted(multisecondonly),336rejected including317translation_excitation_low.
All149short and75medium rows are rejected; no unused valid right shortedges.
`align_mast3r_scale_with_stereo.py` calculates freePnP+metrictranslation before
the lowexcitation check, but stores no metrics on that rejection path. Existing
cache cannot distinguish metricdistance<3mm from learneddelta<1e-4 nor judge
reprojection quality there. Do NOT accept/reuse rejected rows or lower gates
based on these incomplete artifacts; fresh diagnostic geometry is required.

Staged first3-v1 closed3/3 with all6 unscored, before any solver comparison:
first-gauge validation compared a 9-decimal CSV quaternion with the original
in-memory rotation at1e-12. Observed roundtrip deltas0.63-1.24e-9rad match
the existing serializer, not a physical gauge change. Narrow fix validates
only that exact canonical roundtrip at unchanged1e-12, then restores the
original first rotation. Genuine gauge changes remain rejected. Fresh216
targeted tests PASS in11.97s, diffcheck clean; independent review approves
bounded first3-v2 only. No weights, timeline, graph or scoring policy changed.
First3-v2 running under `umi-joint-r-vi-feedback-first3-v2-20261002`.

First3-v2 closed3/3, all original-R controls byte-identical. Heldout2max
6.182894->5.939977mm; heldout4max9.444787->8.514762mm; take06max
14.756369->14.735589mm still FAIL. No production promotion. Frozen full25
launched automatically under `umi-joint-r-vi-feedback-full25-v1-20261002`.
Canonicalfix caab66b4189cbe30796fa353ea4d4376fffb7634 pushed nonforce to
owned sencang/codex/dual-ir-frontend-20261001, fetchedremote restored3committed
files byteidentical at/tmp/umi-r-vi-gauge-fix-backup-20261002.OwVtHv.

Internal gyro/VINS-only audit of first3: calibrated rawgyro integrations have
mean correction-rate norms0.0323/0.0332/0.0341deg/s, corresponding~1.23/1.26/
1.30deg over38s. Convention Log(gyro_delta.T@VINS_Ri.T@VINS_Rj)/dt, so a
bias-to-subtract would have approximately opposite sign. YAML static bias
correction is already applied; no dynamic gyro bias columns exist in cached
VINS CSV, and pose-only pilot has no gyro bias state. This is an omitted-state
confound, not evidence of sign/dimension/td bug or proven cause. Do not retune
sealed imu_rotation_constraint_weight. Any further rotational experiment must
address this physical state/observability explicitly, not hide the conflict
with noise-weight sweeps. No GT used in these internal statistics.

Standalone diagnostic now replays only source-rejected lowexcitation pairs,
with an exact-function return profiler and before/after binding/source hashes;
no cache/acceptance/factor mutation. Fresh226targetedtestsPASS. First raw DB3
smoke8pairs reproduced8rejections, but root caught an index-space mismatch:
reference indices353-355 are epoch1790758834.6053965..1790758834.6720788;
raw learned trajectory indices409-411, not353-355. Initialrawwindow323..385
was earlier and is NOT evidence about the precision peak. Correct time-bound
window maps to raw379..441; new16pair diagnostic launched. Originalsmoke
retained. Raw reports omit depth/parser/sourcehash metadata; maxdepth0.6 is
explicitly taken from workflow and uncertainty is flagged, not claimed exact
historicalsource reproducibility. Other parser fallbacks are declared.

Staged rotation feedback full25-v1 closed25/25,24scored and original1unscored.
Control and feedback both18fullPASS/19max<=10mm, no oldFAIL->PASS and no
oldPASS loss relative to combined. All controls must match combined exactly.
Do not promote the new rotation feedback: it adds work without new pass.
FullVI take06max14.756369->14.735589mm remainsfail. Correct raw-window
16pair diagnostic succeeded16/16 lowexcitation reproductions; measured short
PnP translations0.086-0.963mm, all learned lengths<1e-4, inlier ratios
0.905-1.0. This corroborates a stationary/lowmotion window, not a badscale
measurement. IMPORTANT this window was selected from the discarded pureSE3
pilot peak11.8s, not yet verified as the retained fullVI peak. It cannot be
used to justify repairing the retained VI failure until its actual timestamp
is separately located. All diagnostic rows remain rejected/no emittedfactors.
Independentreview approves diagnostic-only, fresh226testsPASS; production
source remains unchanged.

Independent currentpeak localization resolves the branch mixup: retained
fullVI take06 row1030 epoch1790758857.172943592 (+34.330376863s), baseline
15.351919mm, combined14.756369mm, stagedR14.735589mm. PureSE3 pilot row354
at11.796170712s is a different failure. Current raw trajectory index1086
matches peak exactly; cameraCSVrow1086/IRframe1116. Correct rawwindow
1056..1116 is now replayed, not the earlier379..441. Full25review verified
48/48 onboard manifests/outputhashes,24/24controls exactvectors; baseline
17full/17translation versuscombined/staged18full/19translation withone
sourceunobservable retained. No stagedR gains vscombined.

Bias-only physicalstereo diagnostic on3records (16fit+8disjointheldout long
edges) shows weak evidence: fullrank numericalJacobian butfit/heldout RMS
gains small, weightedheldout2 gets worse0.2904->0.2986deg. Estimatedbias
axes differ andformal1sigma~0.0147-0.0194deg/s. Do not implement/promote a
bias-driven correction fromthis. Formalgyr_w2.89e-7 is a randomwalk rate,
NOTan initialbias covariance. Candidatehypothesis rejectedpendingstronger
evidence; nosealedgyroweight tuning andnoGT used.

Currentpeak RAW-window 1056..1116 contains45/45 accepted free-PnP rows,
zero lowexcitation rejections. Initialempty diagnostic v1 misleadingly said
PASS withzero inspectedpairs; narrowfix now returnsNO_MATCHING_PAIRS/exit3.
Freshv2 confirmszero selected/no emittedfactors,227targetedtestsPASS11.90s.
No production trajectory or factor acceptance changed.

Read-only RAW SE3 triangle check: take06 currentwindow80triples,
translationclosure median1.117/P953.986/max4.573mm; rotationmedian0.127/
P950.432/max0.575deg. Heldout2window transP955.395mm/max8.513mm;
heldout4P954.732/max8.971; ind2P952.681/max3.173. No duplicatepairs.
This does not support a uniquelybroken local rawstereo geometry atcurrent
failure. Whole-record badcyclesexist butalsoin passingcontrols; notselection
or groundsforfiltering. Nextboundeddiagnostics inspect graphassembly and
actual LSQR convergence before proposing any new model/factor change.

Combined fullVI numerical replay4cases: all16LSQRcalls istop2/finite,
relative normalgrad2.45e-8..5.40e-8, max replaypositiondifference2.12e-8m.
No current numericalfailure explanation; no scaled/directrerun triggered.
Acceleration biasJac finite-difference review errors7e-10..3e-9; no signbug.
Potential robustnessgaps (IMUcoverageclamping, LSQRdiagnostic omission) are
separate fromcurrentpeak and mustnotbe soldas a10mm accuracyfix.

Longmetricgraph census4cases: take06 has15 >=2s sharedstereo edges crossing
peak+/-1s, maximumduration8s; heldout4 haszero such crossings yetpasses.
No missinglongedges explanation. Earlier Sep28 local/window/shapeBA already
failedgeneralization; do notrepeatthatlane orblindfullp/R/v/g/ba/bg build.
Reviewerrequires source-onlygyro-bias Schur/positioncoupling evidencefirst;
currentweakbiasdiagnostic andzero stagedRpassgain do notsatisfyit.

Nextboundedall24census tests representationconsistency of learnedconfidence:
constantgauge/physicallever changeddisplacementvectors while preserving old
own_stereo_residual_m/confidence metadata intentionallytoisolategeometry.
Compare residuals inactualnewcommongauge before any experiment; no threshold
sweep,GTfactorconstruction,perrecordrouting orproductionchange authorized.
Emptydiagfix409fbaced3c788db9a57c2622d93b4523fd061de remotelyrestored3files
byte-identical at/tmp/umi-empty-diag-fix-backup-20261002.FcPflH.

All24 confidence-representation census:68975learnedfactors/35254sharedrows,
zero missingpairs. Old/newconfidence mostlycorr~.999; take06residualmedian
4.32->4.57mm/P9517.53->18.31mm, confidence ratioP991.055, only17/2985
materialchanges underdiagnostic screen. No representationweightpatch justified.
Rawreference review: all24same calibration/bodyconfig/queryoffset,
fulltimestampoverlap. Ordinaryfailures have no sourceTracker200mmjump pattern;
fixedlever-error explanatoryR2median.021, take06.0188. The alreadyknown
Sep29take03 rawjump remainsseparateinvalidreference caveat, noGTrefitting.

Following systematicdebugging architecture-review rule, no fourthposition/
rotation/weightpatch is attempted. Nextnewobservable isreciprocalphysicalSE3:
classical producer computesforwardandreversePnP, butscalarcombiner retains
onlyforward metric3D displacement. Existingfullvectorvalidator has no
productioncaller. Newboundedstandalone4record/12uniformacceptedpair replay
will captureoriginalcombineinputs/results withoutmutation, assess3D/Rclosure,
and preservehistoricalsource-reproducibility uncertainty. No qualitygate,
factor,metricmean,trajectory orproductionpromotion fromthisdiagnostic.
Freshoriginal5 regressionfiles78PASS11.45s; thisiscodevalidationnotaccuracy.

Important conceptualcorrection: do NOT describe thecurrentlocalpositionblock
as unobservable/globaltranslationgauge. Firstpanchor andallframeVINSchain,
plus15metriclongcrossings, make localshiftobservable; SE3scoring removesglobal
gauge. Source-onlyfixed1s-grid 3mm triangularbump addsrelative-factorcost:
33-34s3.948,34-35s7.850,35-36s6.468, notzero. Uniformglobal3mmtranslation
adds5.68e-14 torelativefactorsonly, notanchoredfullobjective. Therefore
"absoluteoffsetblindness" earlierwording iswithdrawn; possibleweakmode or
biased/correlatedbridgeforces remainsunproved. Gradient force decomposition
mayidentify competing sources, butmustnotbe translatedintoGT-selectedgates.

Accepted reciprocal3D diagnostic v3 closed four records, 12 uniform pairs
each: take06 closure median0.328/P952.526/max2.734mm, RclosureP950.218deg;
heldout2 0.684/3.553/4.403mm and0.350deg; heldout4 0.526/2.180/3.325mm
and0.264deg; ind2 0.623/3.306/5.819mm and0.448deg. All48 replayed forward
vectors exactly match saved source vectors; no missed/incomplete captures,
hash failures or emittedfactors. Historicalsourcehash verification remains
false explicitly. v1/v2 are debug artifacts, onlyv3 authoritative.
Noncommuting frame regression fixed inverse to -Z_ji.T*d_j; reverse direct
check is -Z_ij*d_j. Independentreview APPROVE diagnostic/backup only,
fresh35testsPASS0.25s. This is not an accuracyfix and does not support a
uniquelybad reciprocalvector explanation for retainedtake06.
Next readonly source-class force/stiffness decomposition uses fixed1s grid
3mm triangular p perturbations along all3axes, replayed original fullVI
state/finalsolve weights. Check against objective finite differences and
stationarity; compare4records before proposing model changes. No GTselected
direction, weightpatch, geometrygate or productionpromotion.

Source-force replay4records completed using actualfourth LSQR system (not
post-solve next-IRLS weights). Numericaltrajectorydelta max2.85e-8m,
roworderexact andfixed1s/3mm/xyz finite-difference errors<1.55e-13.
Take06 34s-z linear learnedleft-3.640/stereo+3.393/right+0.249,
total+4.7e-8; quadratic7.58. PASSheldout2/4 alsohaveopposingstereo/learned
forces. No isolatedfailure discriminator andno license forscalarweighttuning.
Source-force reproducible script/json beinghardened forsourcehash guards;
v1 retained, v2 tobindallconsumed sources. Notanaccuracyfix.

Important source-parity audit: currentbaseline/constant/physical/combined
alluse same192rawreportpaths over24preparedrecords,70332acceptedobservations
(LK54644/SIFT15688), pnp_refined=False70332/True0. Sep27/28 validated
SIFT-LM+gyro ten has40differentmeasurementreports, acceptedFalse13661/True2056
forpnp_refined. Current17/18PASSistherefore internallysame-source butNOTsame
sourceasthatvalidatedten. Historicalten8PASS2FAIL—notall10PASS—remainsfact.
Evidence: .planning/stereo_spatial_repeatability_20260927/progress.md:141-177;
reports/stereo_spatial_repeatability_20260927/sift_lm_gyro_candidate_ten_v1/
dev2/validated_graph_command.json explicitlybindsnewmeasurementpaths.
Nextgloballyfixed probe reuseslegacyfreeSIFTLM+rawgyro5deg policy; original
LK/scales/frontends/VINS/backend/scoring stayfrozen. Firstmeasurement-only
physicalsharedstereo arm isnotfull27restore. All25sourcepreflightrequired,
thenfailedtake06+heldout2/4+ind2, independentreviewbeforeactualrun. Newsource
overridehashes areseparatefromoriginalbaselinehashmap, noprovenanceforgery,
noderivedrightdoublecount oraccepted-falsefactor. Productionunchanged.

Source-force v2 nowhashguarded131consumedsource/inputpaths withbefore/after
equality and34/34candidateinputs verifiedperrecord; assertsallframe nodes,
inactivevisual/scalepriors,rowordering,replayandfinite-differenceconsistency.
Independentreview APPROVE diagnostic/backup, no precisionfix implied.
Reciprocaldiagnostic code/ledger7412c56e remotelyfetched/restored3files
byteidentical at/tmp/umi-reciprocal-diag-backup-20261002.aPx7Uv.
NewSIFTsourcepreflight all25 preservesunobservabletake05failure;24ready,
15688SIFT/54644LK. Firstadapterdraft incorrectlyrequiredlegacygraph/dataset
forRIGHT aswellasLEFT; maincaughtthisbeforeanyLMrun: refinementonlyneedsLEFT
legacygraph. Targettake06/held2/ind2 originalleftgraphpathsarepresent.
Do not invent shimgraphs or declarea blockerfromderivedRIGHT missinggraph.
Fresh39targetedtestsPASS0.29s; sourceprobeimplementationstillpendingreview.

2026-10-02 source-only bounded launch: independent reviewer approved
run_sift_lm_physical_probe.py after complete before/after source mutation
guards and truthful run/preflight labels. New wrapper tests6PASS; combined
source/evaluator tests11PASS0.25s. All25 preflightv3 retains24ready and
unobservabletake05; READY does not imply legacy direct-callable for all24.
Actual four-record source-only replay launched at sift_lm_physical_four_source_v1
for take06/heldout2/heldout4/ind2. heldout2 has reported50refinedSIFTpairs;
this is source-generation progress, NOT solver/scoring or a precision gain.
Existing raw reports, timestamps, production and score gates unchanged.
Second-stage matched LEFT-only two-arm evaluator remains under independent
review. Common accepted-pair intersection is explicit, so its control is not
the prior complete dual-eye production policy. No full27restoration claim.

Source wrapper eab8ec37 backed to userowned sencang branch and fetched/
restored3files byte-identical at /tmp/umi-sift-source-backup-20261002.dcVKzX.
Root caught second-stage comparator naming mismatch before launch:
local_motion_factors are original batch_adapters_v2/both, not constantgauge
best combined. Renamed reference_raw_baseline and added identical raw learned
factor context/path/hash/count regression. Independent post-fix review
APPROVE bounded source-measurement isolation and backup, not best-current
comparison or production. Fresh source/evaluator/physical tests31PASS0.24s.
If this source hypothesis improves, same-source current-best replay is still
required before claiming improvement over18fullPASS/19translationPASS.

Four source-only replays CLOSED4/4, nofailures; all guardedinputs unchanged.
Unique refinedSIFT accepted/total held2 267/267, held4 322/322, ind2 261/261,
take06 303/303. Four matchedLEFT two-arm fullVI scores CLOSED4/4 at
sift_lm_physical_four_score_v1: unrefined->refined maximumATE(mm)
held2 6.35419->6.12628 PASS->PASS;
held4 10.29777->10.25752 FAIL->FAIL;
ind2 14.60402->13.90986 FAIL->FAIL;
take06 17.32348->16.57184 FAIL->FAIL.
Allfour raw/refined acceptedpairsets identical (1493/1675/1353/1494),
zero droppedpairs, complete output timelines, no scoregate change.
Bodyvectors changed161/215/185/206, confidencechanged258/322/247/283 via
same existing observationconfidence formula. This is SIFT-LM+gyrosource
effect including geometry/confidence, not solely a vector-only ablation.
Smallconsistentimprovement but NOextraPASS and NOTbestcurrentcontext.
Next exactdual/constantgauge/physical comparator must control-replay current
best trajectory<1e-7m before attributing any sourceupgrade; implementation
reuses existingbackend/selection, noGTpolicy and separateoverrides.
Capability f006c61c backed to sencang, fetched/restored5files byte-identical
at /tmp/umi-sift-source-score-backup-20261002.qYmv2R. Production unchanged.

2026-10-03 exactcurrentbest dual-context adapter launched boundedfour after
Root/independentreview repairedfailclosedcontracts: originalcontrolreplay
hardgate<1e-7m withfinitep/R/t/count checks; combinedreference schema/policy/
inputSHA binding; actualconsumedinputbefore/afterhashes; finalprovenance
written/frozen before score. Scorerlocalwrapper restoredfinally; shared
backend/math unchanged. Newtests6PASS; related52testsPASS0.25s, existing
five acceptance suites78PASS11.82s. Newartifacts only, production unchanged.
This arm isPARTIAL LEFT SIFT-LM sourceupgrade: RIGHTmetricreports derive
oldLEFTgeometry andremainunchanged. It doesNOT claim fullmetric-source
parity; staleRIGHTderivedgeometry canwinfixedconfidence, tobeaudited.
Ready legacycoverage:16direct+8adapterneeded+1unobservable, not25direct.
Of8readyadapterneeded,4havepayload/datasetbutlackoldgraph;4havevalidgraph
outside reports anddatasetatkeyframe_dir. No missingrawpayload found for8.
Do not write shimgraphs or discardthese; genericimmutableinputadapterneeded
onlyif currentbest probe supports expansion.

PartialLEFT sourceupgrade atcurrentbestcontext CLOSED4/4, controlreplays
EXACT0p/Rdelta for all4, sameconstant learnedfactorSHA botharms.
MaximumATE(mm) original->refined:
held2 6.18289->6.03514 PASS->PASS;
held4 9.44479->9.53230 PASS->PASS (max/P95regressslightly);
ind2 14.40292->14.16120 FAIL->FAIL (meanregressslightly);
take06 14.75637->14.46512 FAIL->FAIL.
NOadditionalPASS; full25best remains18full/19translation, no promotion.
Metricwinnercensus all6015sharedpairs: originalL/R/tie3255/2695/65,
partialrefined3436/2531/48,303winnerchanges.767LEFTbodyvectorschanged;
192stilltakeRIGHT-onlyoldderivedgeometry (198withRIGHTincludedties).
Perrecordtake06/held2/held4/ind2 suppressed49/38/79/26.
This confirms incomplete source propagation, not proofitcausesATEfailure.
Nextfixedsourceconsistencytest derivesRIGHTmetricreports fromrefinedLEFT
via existingconversion (one sharedgeometricfactor, NOTindependentright
measurement). RIGHTlearningfrontends/constantlearnedfactors stayfixed,
geometry/refconfidence are updatedthroughnativeexistingpolicy only.

Consistent RIGHT source stage v1/v2 CLOSED four failures each BEFORE solver:
v1 wrongly compared full RIGHT factory metadata against LEFT (native RIGHT
adds right_rotation_from_left); v2 wrongly required raw RIGHT source trajectory
to equal downstream IMU-metric trajectory. Both are new probe interface bugs,
not recording failures or precision outcomes. All failure artifacts retained.
Preparer now validates shared factory fields exactly plus fresh DB3 RIGHT R,
preserves original RIGHT factory metadata; raw geometry trajectory remains
the original trajectory_frames.csv for native scale/gates while downstream
imu_metric_trajectory.csv remains baseline-hash-bound and unchanged. Both
are source-guarded and timestamp-checked. DB3 hash deduplicated per record.
Four real-layout lightweight preflights verified paths/lineage/calibration.
Fresh related42tests PASS0.31s; independent review approves bounded4 source
v3 then paired currentbest comparison, NOT production or all25 promotion.

Consistent source v3 CLOSED4/4ready, all16RIGHTreportsPASS, nofactoroutputs,
allconsumed-source guards verified. Paired currentbest score CLOSED4/4 with
EXACT0p/Rcontrolreplay and fulltimestampoverlap1/samples1141,1143,1143,1143.
MaximumATE(mm)original->consistentrefined:
held2 6.182894->6.019389 PASS->PASS;
held4 9.444787->9.595465 PASS->PASS (P95/maxslightlyworse);
ind2 14.402923->13.725837 FAIL->FAIL;
take06 14.756369->14.439234 FAIL->FAIL.
All4mean/RMSEdecreased; 3/4P95improve. NOnewPASS. This is bounded geometric
source consistency evidence, not production/all25 promotion or a10mmfix.
NativeRIGHTgeometry remains LEFT-derived: 2learningfrontends are independent
but these metric stereo rows share one measurement source. Nextbounded
readonly audit assesses trulyRIGHT-centric temporalPnP using actualRIGHT
pixels and calibrated stereo depth; cannot swap images with positive LEFT
SGBM or reinterpret LEFTdisparity at RIGHTpixels. No weights/threshold sweep.
Capability c3313ba1 remote-restored8files byte-identical from sencang at
/tmp/umi-right-source-backup-20261003.DDdVXG; latestresultsnotyetbacked.

2026-10-03 continuation: consistent four-record result evidence was backed
in b3e011a38b696e3be38ea84480d3cca02a7dfc3c, restored byte-identical at
/tmp/umi-source-consistency-evidence-20261003.M0SekW.
New independent RIGHT pixel motion helper reuses native LEFT PnP in a
mirrored rectified-camera representation, then restores proper RIGHT-frame
rotation and translation (reflection is not treated as a rotation).
Known-motion tests cover nonidentity world pose, heterogeneous depths,
positive/negative disparity signs, native LM refinement and real rounded
D405 baseline metadata. Related 35 tests freshly PASS. This is source
capability, NOT a real-trajectory precision claim or production promotion.
Bounded diagnostic client samples six accepted and six rejected LEFT rows
per record, recomputes fresh SIFT/PnP for BOTH eyes, emits no factors and
reads no GT. Lightweight four-record paths/timestamp preflight PASS:
1199 raw poses per eye, exactly equal time arrays, all source paths present.
Independent client review remains pending before real DB3 pilot launch.

Client review initially blocked stale stage-hash/lineage acceptance; fixed with
declared LEFT/refinedRIGHT/originalRIGHT/rawRIGHT bindings, manifest guard and
timestamp checks before image loading. Empty SIFT/KNN results now cleanly
reject per pair rather than throwing a record-level shape exception.
Fresh 43 related tests PASS; independent re-review approves bounded pilot.
Helper backup 4c1396fe remote-restored byte-identical and 11 known-motion
tests freshly pass against clean remote native dependencies at
/tmp/umi-independent-right-helper-20261003.BKhfTW. Client d759b71c backup
remote-restored byte-identical at /tmp/umi-right-diagnostic-backup-20261003.3WWuLK.
First source pilot v1 failed all four before motion estimation because root
omitted ROS environment setup (rosbag2_py unavailable); retained as launcher
failure, NOT raw recording quality or algorithm precision failure.
Same frozen code under /opt/ros/humble/setup.bash: source pilot v2 CLOSED
4x12, exact6 accepted+6 rejected LEFTsource rows each, allhashguards PASS,
0GT/0factors/0backend/0scoring. FreshLEFT/RIGHT crossmatrix both/Lonly/Ronly/neither:
held2 8/0/0/4; held4 7/0/0/5; ind2 7/0/0/5; take06 6/0/1/5.
Bothaccepted physical-vector closure median/max(mm): held2 .333/.446;
held4 .386/1.446; ind2 .421/.740; take06 .572/.663.
Take06 pair1125->1150: freshLEFT pnp_failed17inliers/57points;
freshRIGHT accepted22/62, reprojectionP95 3.927px, distance69.796mm.
This is evidence of RIGHT source complement, NOT ground-truth accuracy.
Most sampled rejects are static lowexcitation; therefore next predeclared
4x24 source-only audit stratifies12 accepted+12 non-lowexcitation rejected
primaryreport pairs per record. This changes diagnostic sampling only,
not tracking gates, frame retention, graph factors, weights or scores.
No independentRIGHT factor promotion until stronger source coverage and
guarded paired currentbest replay show an actual precision improvement.

Visual-stratified 4x24 source pilot CLOSED with allfour hashguards PASS,
exact12accepted+12non-lowexcitation-rejected sourcepairs each, 0factors/GT.
FreshLEFT/RIGHT crossmatrix both/Lonly/Ronly/neither:
held2 20/1/0/3; held4 19/0/0/5; ind2 17/2/4/1; take06 19/0/2/3.
Therefore complementary availability is genuinely bidirectional, but native
FORWARD-only source acceptance is not a usable-factor claim. Bothaccepted
closure median/max(mm): held2 .586/7.172; held4 .744/5.032;
ind2 .436/24.068; take06 .885/20.003. Twoeyesagreeusuallyyetcanconflict2cm,
despite BOTH native forward PnP acceptance and reprojectionP95 below4px.
Next bounded source audit adds the SAME native temporal forward/reverse
combine_bidirectional_scale policy, with raw forward and reverse outputs
retained. No newly-added vector gate, threshold tuning, graph, supervision,
frame trimming, precision report or backend run implied. Do not blindly
promote forward-only RIGHT estimates on these internal disagreement cases.
Visual diagnostic capability 4734e79e remote-restored byte-identical, plus
30fresh tests against clean remote dependencies PASS at
/tmp/umi-right-visual-diagnostic-backup-20261003.D5Ij5i.

Native bidirectional four-record source audit CLOSED, same 4x24 predeclared
visual pairs, all source guards PASS, no GT/factors/backend/scoring. Native
combined crossmatrix both/Lonly/Ronly/neither:
held2 19/0/1/4; held4 19/0/0/5; ind2 15/1/5/3; take06 18/0/2/4.
Bothaccepted factory closure median/P95/max(mm):
held2 .426/3.390/7.172; held4 .744/4.058/5.032;
ind2 .436/1.415/1.776; take06 .869/4.736/6.330.
Existing native reverse-rejection policy excludes severe20-24mm forward
conflicts while preserving RIGHT-only complement. No threshold was tuned.
This is internal measurement consistency, NOT absolute trajectory accuracy.
Capability3758a3d1 restored from sencang at
/tmp/umi-right-bidirectional-diagnostic-backup-20261003.1DDwHq;
33fresh tests against clean remote dependencies PASS.

Next actual trajectory experiment: fixed-pair RIGHT source geometry refresh
using realRIGHT pixels and calibrated stereo depth, with unchanged refined
LEFT4 sources and unchanged report-level scale/quality/factory/session. Native
forward/reverse accepted rows replace geometry; native failures retain the
corrected-LEFT-derived RIGHT fallback with explicit per-row provenance.
Rejected input rows and all pairs/timestamps remain exactly unchanged. This
bounded experiment does NOT yet recover missing pairs or fully-independent
RIGHT report-level scale. Same currentbest learned factors/backend/scorer,
exact original-control replay and no loss of old passing controls required.
Reviewer identified pre-image-load row-validation and stale native-field
bugs; executor fixing before expensive launch. Root evaluator consumes this
mixed provenance only through explicit hash/pair/global-scale evidence.

Actual four-record independentRIGHT fixed-pair source refresh launched from
capability b65a8f41 (session74511 / PID2124933), no shared-code edits while
running. Firstheld2 source CLOSED:1525 independent updates/2 explicitfallbacks,
2166 originalrejectionsunchanged; firstheld4 CLOSED:1661 updates/13fallbacks,
2019 originalrejectionsunchanged. Remainingind2/take06 running; NO newATE yet.
Firstrecord exactpair/time/global-scale/factory/session/quality invariants
freshly verified on allfourreports. Native source acceptance alone does not
prove accurate absolute motion: firstprimary new-vs-old RIGHT vector delta
median.706/P954.776/max30.518mm. Actual matched trajectory scoring required.
b65a8f41 remote-restored6changedfiles byte-identical and66relatedtests PASS
at /tmp/umi-independent-right-refresh-backup-20261003.jYU2j5; core78tests PASS.

Bounded throughput helper (not integrated into frozen running experiment):
ego_vio/vio/cached_ir_correspondences.py caches only per-record SIFT features,
fixed native4000/.01/15/.75 parameters, explicitLRUbound and frameimageSHA
binding across roles/eviction. Fresh73relatedtests PASS; independentreview
APPROVE, additional56ordered syntheticpairs exactraw-output equality.
Four real-record readonly equality probe: first4accepted pairs andreverse
each, 32directedpairs/15414matchingpoints, exactpointarrays/order/dtype/valid.
Perrecord matches and raw/coldcache/warmcache(seconds):
held2 3746 /1.3195/.7103/.0423; held4 5149 /1.1122/.7319/.0715;
ind2 3011 /1.0226/.7030/.0357; take06 3508 /1.0767/.6807/.0506.
Fiveframes/10featureentries each. NoPnP/factors/backend/scoring/GPU emitted.
This proves correspondence equivalence and repeated-match throughput only,
NOT an end-to-end speedup or precision improvement. Existing source producer
and evaluator unchanged by this helper; integrate only after frozenrun closes.

Independent RIGHT fixed-pair source stage CLOSED exit0, all4ready/0failures:
held2 1525updates/2fallback/2166rejectionsunchanged;
held4 1661/13/2019; ind2 1313/24/2356; take06 1482/9/2202.
Totals5981nativeupdates/48explicitfallbacks/6029originalacceptedrows.
Root independently reloaded stage and validated allfour complete provenance
proofs via evaluator validator (10proofpaths/record; 21sourceguardpaths/record).
Original rejected rows, pairs/times/global-scale/session/factory/quality are
preserved. Fresh64 directly-related tests PASS. First attempted test command
used nonexistent test_derive_right_stereo_sources.py and ran zero tests; corrected
command above is the passing evidence, not that failed invocation.
Supervisor automatically proceeds to paired currentbest-control/refreshed
four-record evaluation, output independent_right_geometry_dual_four_score_v1.
No newATE or promotion yet. Raw row-scale distribution audit on firstthree
shows native and fallback medians close, no observed gross LEFT/RIGHT units
mismatch; this is not a proof of absolute scale accuracy. Next missing-pair
recovery contract under read-only review, not yet implemented or launched.

Actual independentRIGHT four-record trajectory experiment CLOSED,4/4 scored:
currentbest original -> refined (mean/P95/max mm):
held2 2.520/5.135/6.183 -> 2.098/4.158/5.153 PASS;
held4 3.796/5.589/9.445 -> 3.463/4.755/7.959 PASS;
ind2 4.972/11.527/14.403 -> 3.983/9.864/12.426 FAILmax;
take06 3.180/5.688/14.756 -> 3.123/5.860/14.401 FAILmax.
Both passing controls retained; all4max improve but NO extraPASS and take06
P95 worsens0.173mm. Root independently checked allfrozen-codehashes, complete
1141/1143/1143/1143 sample timelines exactlyequal betweenarms, orientationequal,
local_motion_factors SHAidentical, onboard-only metadata and oldPASS retention.
Originalcontrol replaymax <=2.84e-8m, wellbelow1e-7m limit. No promotion.
Best full25 still18full/19translationPASS; this pilot is not25record acceptance.
Next justified NEW source pathway: symmetric LEFT/RIGHT native bidirectional
recovery of originally rejected non-low-excitation rows, ONLY from normally
merged PASS reports. Preserve8source reports byte-identical; separate appendix
retains original rejection and native successes/failures. No optionalFAIL
salvage, scale/global-quality rewriting or thresholdchange. Pure sharedpair
append path dedups each physicalpair once and maintains symmetric confidence
winner/tie-average lever transform. Implementing isolated NEWfiles, followed
by fresh tests/review and same4record paired evaluation before broad25 run.

Symmetric native recovery capability now implemented in six isolated new
source/test files. Explicit fail-closed guards reject stale source hashes,
unguarded consumed paths, optional failed/conflicting reports, low-excitation
original rows and invalid native bidirectional results. Original8reports are
not edited; recovered candidates live in a separate provenance appendix.
Pure shared-pair construction preserves original rows and appends each new
physical pair once, with unchanged confidence winner/tie-average and calibrated
lever transformation. Wrapper integration test exercises actual adapter and
physical-transform call path (one tied LEFT/RIGHT pair becomes20mm, original
row unchanged). Independent code-review APPROVE, zero remaining issues;
fresh directly-related suite131PASS. No actual recovery ATE yet, no promotion.
Actual four-record readonly preflight: held2 LEFT/RIGHT641/641 eligible pairs;
held4 866/867; ind2 748/518 (one conflicting RIGHT report normally excluded);
take06 635/638. These5554 candidates are not accepted factors until native
validation and exact reference association pass. Automatic next branch is
four-record native source recovery, then unchanged-currentbest-control paired
evaluation; compare also with the closed fixed-pair RIGHT experiment above.
All consumed source/helper/evaluator/backend code frozen once run starts.
Parallel RK3576 branch remains blocked by unreachable target and absent real
ARM candidate; no deployment or hardware/service mutation performed here.

Capability eb7942ea21d9e6fddc7e568b4b92e20e91875989 pushed non-force to
sencang/codex/dual-ir-frontend-20261001, fetched HEAD equality confirmed;
all7changedfiles restored byte-identical and131fresh tests against clean
remote dependencies PASS at /tmp/umi-symmetric-ir-recovery-backup-20261003.RQpmZW.
Actual source -> paired evaluator automatically chained in session10560,
sourcePID2202786; DB3 opens READ_ONLY. No code edits during this experiment.
Recovery output independent_ir_recovery_four_source_v1, followed automatically
by independent_ir_recovery_dual_four_score_v1 (samefourrecords). Goal remains
validation_failed until full25 max10mm contract actually passes; no recovery
ATE or new acceptance claim at launch.

First actual appendix readonly admission check exposed wrapper provenance bug
before backend launch: raw trajectory_frames.csv was compared against baseline
body/IMU metric trajectory_imu_metric.csv / imu_metric_trajectory.csv. These
correctly differ in path/content. Independent reviewer confirms all4 LEFT/RIGHT
raw/metric full1199-row timelines exactly equal (maxdt0). Producer remains
frozen and running; firsttwo native appendices closed. Wrapper will be repaired
ONLY after source+original evaluator chain closes, preserving costly source
outputs. Five new contract regressions currently RED (5FAIL/15PASS): distinct
raw/metric files, source-declared raw path, raw consumed guard, metric baseline
hash binding and exact raw/metric timeline. Tests alone edited (not consumed
by running producer); no geometry/scales/weights/gates/caps changed.
Firstheld2 actual native recovery171/1282rows (LEFT86/RIGHT85); full guarded
source hashes verified unchanged. Native failures retained (mainlyPnP failures)
and native acceptance is NOT a trajectory precision result. Initial readonly
guard command used unavailable hashlib.file_digest onPython3.10; corrected
streamingSHA implementation passed. Initial debug path candidate.json was
incorrect; subsequent authoritative validate_baseline_artifact loaded correct
candidate and confirmed raw-vs-metric mismatch above.

Actual symmetric recovery source CLOSED4/4ready0failures. First chained
evaluator CLOSEDexit3 before backend: all4record variants explicitly failed
raw trajectory mismatch (zero scored, not counted as precision failures).
After chain closure ONLY wrapper binding corrected: contextRAW path/hash must
match all normally-admitted source reports and consumed guard; baseline metric
path/hash verified separately; complete raw/metric timelines require EXACT
equality. Raw-index validation uses raw times; reference association uses
metric times. Producer/geometry/scales/weights/gates/caps unchanged.
Five contract regressions RED5FAIL/15PASS -> GREEN20PASS; full136relatedtests
PASS, independentfocusedreviewAPPROVE. Root actual4record metadata/admission/
physicaltransform preflight nowPASS: held2 167recoveredeye candidates/95new
physicalpairs/1588totalpairs; held4 212/124/1799; ind2 152/115/1468;
take06 251/148/1642. No same-eye replacements, no physical-pair duplicates.
Actual nativeaccepted counts171/212/152/251 (4held2 candidates excluded by
unchanged exact-reference binding). This is internal source acceptance, NOTATE.
Next paired output independent_ir_recovery_dual_four_score_v2; reuse all4
validated appendices without rerunning expensive source extraction.

Wrapper binding capability d4776ce184b28cd47023ced804ba7f8907d42161 backed
to sencang branch; all5changedfiles byte-identical after remote restore and
136fresh relatedtests PASS at /tmp/umi-ir-recovery-binding-backup-20261003.ac3ijR.
Actual paired recovery evaluation session1899 CLOSEDexit0/4of4scored:
originalcurrentbest -> recovered (mean/P95/max mm):
held2 2.520/5.135/6.183 -> 2.026/3.801/5.256 PASS;
held4 3.796/5.589/9.445 -> 3.524/6.022/8.240 PASS;
ind2 4.972/11.527/14.403 -> 3.763/9.193/10.987 FAILmax;
take06 3.180/5.688/14.756 -> 2.717/4.973/13.259 FAILmax.
All4originalmax improved and both oldPASS retained, NO extraPASS; held4P95
worsened0.432mm vsoriginal. Versus the preceding actual independentRIGHT
fixed-pair arm (max5.153/7.959/12.426/14.401), held2/held4max slightlyworse
and ind2/take06max improve1.439/1.142mm. No promotion or full25claim.
Rootfreshverification PASS: allfrozen codehashes, full1141/1143/1143/1143
timelines/orientation unchanged betweenarms, learnedfactor SHAidentical,
originalcontrol replaymax <=2.834e-8m <1e-7m. The large physicaltransform
vector-delta field on appendedrows compares zero template to actual native
motion; it is not an ATE or a claimed228mm trajectory correction.
Automatically continuing evidence audit of ind2/take06 residual over10mm
frames/constraintcoverage/nativeclosure, including held4 regression; no new
cap/weight/threshold sweep and no GT-based source/candidate selection.

Closed-four residual coverage audit: exact frozen scorer reconstruction agrees
with precision.json. ind2 max10.987mm at CSV1104;22samples >10mm, mainrun
1102-1116 (0.467s). take06 max13.259mm at CSV1030;only4samples1027-1030
(0.100s) >10mm. Both are covered by existing stereo and learned constraints,
not an uncovered endpoint/gap: ind2 peak55stereo/81learned factors, take06
52stereo/86learned factors. No recovered newphysicalpair has a learned local
factor in these windows (8new ind2,13new take06); avoid claiming a learned
LEFT/RIGHT closure validation for those new pairs.
Post-solve signed residual pull audit uses w^2*(target_delta-solved_delta),
not an exact stored historical LSQR gradient or a causal proof. take06 nearpeak
learnedLEFT target residualP95=22.13mm vs currentstereoLEFT2.23mm; ind2
learnedRIGHT33.07mm vs newstereo2.70mm. Large learned pull also appears in
held2PASS, so magnitude alone is not a failure selector. held4P95 regression
window129-170 has small stereo residuals and a subtle objective balance change;
do not assume a single bad recovered edge without source evidence.
Next bounded read-only audit: do frozen learned factors' source-consistency
metadata still refer to old stereo measurements while native geometry is
refreshed? Check original coordinate/gauge/lever/deduplication semantics and
unchanged8mm formula across all4 before any implementation. This is a hypothesis,
not a confirmed production bug; no generic weight/gate/cap sweep authorized.

Source-consistency exact audit closes all4: reconstruct_tracks/load_eye reruns
the identical hash-bound orientation refinement; baseline factor identity
PASS with target maxabs0 at2980/3349/2536/2985factors. Current policy
learned_motion_consistency_limit_m=None:8mm is confidence Huber scaling,
NOT an enabled hard consistency gate. Refreshed native observations materially
change RIGHT source confidence, e.g.take06(844,854) .013->.661, ind2(958,964)
.078->.680. Independent contract review allows ONE parameter-unchanged source
consistency experiment after exact controls, not revival of sealed weight sweeps.
New isolated helper/adapter4files implement source metadata sync only, preserve
current constant-gauge targets, reference timeline, scale, original factor
count/keys/order, solver/gates/caps. No recovered newpair becomes learned factor.
An existing observation with no matched native source explicitly retains its
hash-bound old source; all4 exact adapter preflights PASStargetmaxabs0/count
unchanged. ind2 one boundRIGHT rawpair575/595 (reference519/539) retains old
measurement; held2's80remaining raw fallbackrows are outside usable reference
intervals, not80 scored omissions. Changedfactors2941/3341/2357/2921.
Independent review APPROVE, fresh165relatedtests PASS; two added actual adapter
call/restoration tests bring focusednewtests31PASS. No real ATE for this source
sync arm yet and no production promotion. Initial root preflight accidentally
used wrongind2/take06filterIDs and processed onlyheld2/held4; exact IDs corrected
and separate secondpreflight processesind2/take06. Both sessionsCLOSEDexit0.

Capabilityf33a27165fadb912a8c658f3af993a92fd8ed0dd pushed non-force to
sencang/codex/dual-ir-frontend-20261001; fetchedHEAD equalslocal,all5changed
files byte-identical after remote restore at
/tmp/umi-source-consistency-backup-20261003.NJVq7M;167fresh relatedtests against
clean remote dependencies PASS. Actual source-consistency four-record paired
experiment started session96906, outputindependent_ir_source_consistency_four_score_v1,
all consumed code frozen. No newATE/promotion at launch; production code/config
unchanged. Parallel RK3576 freshbounded SSH againCLOSED255No route to host,
target/release/recording-idle notverified; no hardware/service/deployment mutation.

Source-consistency actual4record run session96906 CLOSEDexit0/4of4scored.
mean/P95/max mm: held2 2.082/3.848/5.097 PASS;
held4 3.581/5.898/7.884 PASS; ind2 3.630/9.025/10.648 FAILmax;
take06 3.177/6.035/14.723 FAILmax. Compared with previous recovery-only
max5.256/8.240/10.987/13.259, first3max improve buttake06regresses1.464mm;
NO extraPASS; no promotion/no weight sweep continuation. Rootfreshallfrozen
codehashes/fulltimelines/orientations/exact originalfactor targets+order+count
and actual newsource contextpath/hash PASS. Existinggoalcore78tests freshlyPASS.
Next source-geometry evidence: native combine_bidirectional_scale only averages
scalar scale and leaves forward displacement/quaternion unchanged. Reverse
vector/quaternion are transient and omitted from saved summaries. Existing
validate_bidirectional_motion only validates closure, not production-fused SE3.
Independent audit found no sealed fullnative forward/reverse SE3 geometry trial.
Proceed with bounded symmetricSE3 source-only geometry experiment; retain full
rawforward/reverse poses, original native scalar acceptance unchanged, no new
gate/weight/cap/scale-state sweep, no GT and no score-based source selection.

Native symmetric SE3 implementation now complete in isolated new helper and
source producer, not production alignment edits. Forward/reverse PnP camera
transform conventions verified; midpoint computed on SE3, not scalar-scale
averaging. Both original acceptance and complete raw forward/reverse geometry
preserved; near-pi branch ambiguity and zero-motion candidates fail closed
with diagnostic evidence (not fake zero scale). Two regression tests reproduced
both failures RED before fixes; root fresh related suite188PASS, independent
focused review21PASS/APPROVE, py_compile/diffcheck PASS. Source producer caches
up to2048disparity frames to avoid repeated SGBM, with output-equivalence and
process-local patch restoration tests. Mathematical exceptions are not accuracy
gate tuning. Original global scales and report quality remain frozen.
Next automatically: backup these4files+thisledger, freeze consumed code, then
native source-only four-record extraction. Paired score consumer is separate
bounded work; it must use saved same-native scalar baseline vs midpoint on the
identical recovery-v2 physical pair layout, original learned factors unchanged,
explicit common frozen fallback, noGT source or candidate selection. No actual
SE3 ATE result at this point. RK3576 SSH fresh again255No route to host;
target identity/candidate/deployment remain unverified, no hardware mutation.

Capability080c79263193fd683c96ca9181514122adf2016d backed non-force to
sencang/codex/dual-ir-frontend-20261001, fetched5files byte-identical at
/tmp/umi-bidirectional-se3-backup-20261003.5rw5dT;188fresh remote-dependent tests
PASS. Initial real source launch session93560 CLOSED3/0ready: omitted ROS
environment, all4 explicitly fail rosbag2_py import before extraction. This is
launcher error, not video/algorithm accuracy; failedv1preflight kept. Corrected
launch uses both /opt/ros/humble/setup.bash and ros2_ws/install/setup.bash;
verified import before native extraction, new output bidirectional_se3_four_source_v2.

Actual v2session86922 CLOSED3/0ready: all4 fail explicit scale source mismatch
before output/backend. Root source diagnosis corrects our producer assumption:
native align_mast3r_scale_with_stereo.py:1074 scale is projection
dot(d_cam,mast3r_delta_i)/mast3r_distance^2, NOTnorm(d_cam)/visual_distance.
Therefore forwarddistance/scale need not equal reversedistance/scale; the
failing check was our new adapter bug, not dataset rejection or native defect.
TDD repair is now scoped to geometry-only isolation: keep saved same-native
scalar baseline scale/scale_estimator byte-equal for both score arms, validate
native explicit mast3r_distance positive finite and symmetric; only update
midpoint displacement/quaternion/metric_distance, label unchanged scalar scale
provenance. No source rows/report confidence/gates/weights changed. Failedv2
preflight retained, no score launched; all live consumed-source jobs closed
before producer edits. Freeze/review/backup before new v3native extraction.

Projection producer repair GREEN8focused/22helper+producer/189related tests,
independentfreshreviewAPPROVE. Root also made projection fixture physically
consistent with visualdelta[.02,0,0], forward[.02,.02,0],reverse[-.021,.01,0],
projection scales1/1.05 and proper cosines, not merely hand-authored inconsistent
norms. Focused22PASSafterfixtureupdate. Frozen scale means existing confidence
formula (inlier ratio,rotation_error,scale,bidirectional_disagreement) unchanged;
it does not use updated metric_distance or quaternion/vector. Back up only
producer+test+ledger, then immediately newnativefour sourcev3. Newconsumer is
not live yet: rootfound firstversion source status/report admission/time-bound
skip/layout/provenance and frozen_code_paths recursion defects before any
backend; assigned bounded TDD fixes, no inaccurate score claim.

Projection fix8a2773e10685dfd7ee351129111da14faba6b1a6 backed to owned
sencang branch, remote3files byte-identical at
/tmp/umi-se3-projection-fix-20261003.LEQkeY;189fresh restored-dependent testsPASS.
Actual sourcev3session44317 nowrunning with bothROSsetupenvs, PID2300292;
2m27s fresh processcheckCPU569%,RSS5.15GiB, no early producer exception.
No finalsource report orATEyet. Baseline recovery-v2physicalpair layouts verified
1588/1799/1468/1642, frozen learnedfactor sourceconstant_ir_gauge_selected,
framebody_imu_origin. Consumer may update geometry onlywithinexactfrozenlayout;
extra nativepairs excludedfrommain butretained diagnostics, missingpair common
frozenbodygeometry fallback without inventing camera measurements.

Sourcev3 firstheld2appendix nowcomplete (secondrecordrunning). Rootreadonly
fullnative-from-raw reconstruction across3224acceptedobservations EXACTPASS:
left2174attempted/1614accepted,right2168/1610; confidence delta vs savedscalar
baseline max0.0. Forward/reverse edge vectorclosureP95=2.794mm,max26.621mm;
midpoint-vs-forward edgegeometrydeltaP95=1.365mm,max13.663mm. These are native
edge diagnostics, NOTATE or evidence of10mm acceptance. Allfailurerowsretained.
Consumer14mockfocusedtests/fullrelated203PASS are insufficient: rootfound
activefixedrowconfidencenotrefreshed before realphysicaltransform and missing
fullSE3rawreconstruction; independentreview REQUESTCHANGES confirms2HIGH plus
recovery-reference paths missing variant inputprovenance MEDIUM. TDD fixes
assigned before any consumer backend/score, source producer/helperremainfrozen.

Consumer TDD fixes now16focused/205relatedPASS; independentfreshreviewAPPROVE,
fullnative recomputed (including mathinvalid rejects), realphysicaltransform
confidence refreshed identically betweenarms, recovery-reference paths added to
variant input provenance. Root firstACTUALheld2 consumer metadata+physical
preflight session81659 CLOSED0/PASS: fixed1588rows preserved exactkey/order,
same3140reference-bound scalar/SE3candidates, sameconfidence1048rows refreshed,
fallback0physicalpairs;84nativeoutside-reference candidates diagnosed(72gap,
12missingendpoint) withraw1118nativefailuresretained, notglobalframe omissions.
No backend/no scorer launched. Root accidentally printed fullmatched1588pair
keys in readonlyoutput(41k tokens); subsequentreports must summarize counts,
notdump arrays. Sourcev3 now2/4appendices written, thirdrecordrunning; consumed
producer/helperunchanged. Back up reviewedconsumer2files+ledger before score.

Consumer635c045ec01fa37fb5c33ad695d4be07ecb05cea backed to sencangbranch,
3files restored byte-identical at /tmp/umi-se3-consumer-backup-20261003.Rj4GT3,
205fresh restored-dependent testsPASS. Before any actualscore root detected
another integration defect: consumerpatches paired.ORIGINAL_VARIANT toscalar,
but reused paired.run_arm:647 condition would require same-native scalarcontrol
replay oldcombined trajectory within1e-7m. That is not the proposedcontrol and
would incorrectlyfail. Requested TDD real score-hook isolation and correctly
named summaryaggregate (aggregate also consumes oldvariantglobals), no general
replayvalidation removal/productionedits. Sourcev3stillfrozen/now3of4appendices
complete at16m08s, fourthrunning, consumerbackendstill0launched.

Oldvariant replay integration fix nowTDD18focused/207relatedPASS; reviewer
freshAPPROVE. Adapter no longer aliases old paired.ORIGINAL_VARIANT; uses its
own two-arm summary aggregate. Newtest calls the real paired.run_arm scorehook,
fails if oldcombined replay validator is invoked, and verifies actualmetadata
hook. Original oldcontrol validation untouched. Rootfullraw recomputation across
second/third source allrows EXACTPASS, confidence delta0; held4 nativeleft
2541attempt/1770accepted,right2541/1765; ind2 left2101/1398,right1700/1248.
No freshATEyet. Back up adapterfix before frozen4source final score run.
