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
