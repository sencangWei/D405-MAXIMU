# Incremental retention: user correction and fresh trajectory check

Partial improvement is not precision acceptance, but also is not a reason to
discard a direction. Keep each source-bound candidate and its outputs. Retain
the incremental development candidate when most fixed failing records improve
and all five historically passing controls still have max ATE <= 10 mm with
comparable complete coverage. Keep explicit regressions and technical failures
in the denominator. Do not promote on one recording or a local geometry proxy.

The existing fixed ten-record cohort and full25/new-capture completion standard
remain unchanged. Ground truth is used only after the estimate is frozen for
evaluation, never by the frontend, scale solver, fusion, or production selector.

The joint metric candidate remains retained (ROOT 117b080 + evidence 0f8fff2;
actual MASt3R toolchain 58c9f61), not rolled back. Its ind2 LEFT full frontend
finished successfully: 1199 actual tracked poses / 1199 input frames, 86 actual
joint metric solves; trajectory SHA256
606e0565a6f781b4303e011c570bb4e9fa7507aac5984d47c18d1948f1ebd78e.
This is not an ATE or precision PASS. The RIGHT v1 run was subsequently
interrupted (details below). The take02 RIGHT prefix failure is retained as coverage
failure, not evidence that the metric direction is useless.

Review found that historical frontend commits differ from current 58c9f61.
Therefore frozen historical scores are historical references only. Fresh
default-off controls use the identical current code/data/calibration/model and
effective config, differing only in tracking.metric_relative_joint=false.
They export actual full-frame poses with --require-complete; interpolated
missing tails are not accepted as observations.

Fresh downstream evaluation must regenerate the four onboard LEFT stereo
reports from original raw DB calibration, derive RIGHT reports with explicit
LEFT-derived geometry labels, and regenerate both IMU metric trajectories.
Then replay the unchanged symmetric -> constant gauge -> physical stereo
constant gauge backend and unchanged frozen reference scorer. No old estimate,
score, or source SHA is relabeled as fresh. This path does not include the
separate native recovery / missing-pair appendix.

Execution update: RIGHT v1 later returned exit143; both native producer PIDs
are gone and no full native export exists. The caller/producer artifact remains
preserved; it is an interrupted technical run, not a precision result. A first
default-off LEFT control failed with a confirmed CUDA OOM while sharing the GPU
with the growing RIGHT graph. Serial control LEFT v2 then finished successfully
with 1199/1199 actual poses. Subsequent controls, fresh scale/fusion/scoring,
and a fresh RIGHT metric replay are queued serially in
run_ind2_fresh_serial_validation_v1.sh. No failed output is overwritten and no
algorithm objective, weight, cap, or precision threshold is changed.

Fresh serial validation checkpoint: default-off LEFT and RIGHT v2 both
completed with 1199/1199 actual poses. The unchanged raw-stereo primary stage
then returned its quality-failure code 3: relative_p90_p10=0.571 exceeds the
existing dispersion gate. The complete control frontends and failed scale
report remain preserved. This is an internal-scale quality failure, not an
ATE measurement or a reason to reject the retained metric-joint direction.
Do not weaken the primary gate or relabel the control as scored. The serial
queue continued to a fresh RIGHT metric-joint replay v2 with the same frozen
objective, code, model, input, and calibration. No new comparable ATE exists
at this checkpoint, and no ten-record or full25 acceptance is claimed.

Same-day source backup verified: ROOT owned remote sencang branch
codex/dual-ir-frontend-20261001 resolves to
6bc8284710deb4f62996a076129e7dd33e3b961d after a fresh fetch. A clean archive of
that fetched revision was restored to /tmp/umi-metric-ab-restore-RnRwiH;
all seven newly backed-up files match the working source byte-for-byte,
including the unchanged frozen scorer dependency. The restored focused suite
passed 80/80 tests in 1.88 seconds with the previously restored owned MASt3R
source. These tests establish source/contract reproducibility, not native
runtime packaging or precision acceptance; raw recordings, calibration,
model checkpoints, and native runtime remain external prerequisites.

Next bounded investigation: all ten fixed cohort records have verified full
LEFT/RIGHT clocks (1199 frames per eye), including the two 09-30 RIGHT datasets
under frontend_observation_20260930/dual_ir_same_code_20261002 rather than
batch_v1. A read-only audit confirmed the fresh OFF primary uses the same raw
DB/factory calibration/window/free-PnP/depth settings as historical production.
Its changed LEFT geometry, not a stereo orchestration parameter mismatch,
explains the new dispersion failure. The fresh OFF RIGHT export is byte-identical
to the historical RIGHT source; the historical LEFT frontend provenance differs.

The shared-observation diagnostic is saved in
shared_stereo_geometry_diagnostic_v1.json with source hashes and explicit
non-ATE/non-acceptance labels. For the identical 593 OFF-accepted raw stereo
observations, metric-joint LEFT reduced forward-projection scale dispersion
0.571610 -> 0.410501 and rotation P95 2.677522 -> 2.563217 degrees; rotation
maximum slightly increased 4.522480 -> 4.581192 degrees. This supports retaining
the effective component, not unconditional promotion or discarding it because
one remaining metric is worse. Actual full-frame scored trajectories are still
needed; this subset diagnostic has selection bias and is not the fresh ON
bidirectional report.

A real orchestration bug in the new evaluator was reproduced and fixed:
optional LEFT quality FAIL returns 3, while RIGHT derive quality FAIL returns 2;
RIGHT derive additionally requires a passing LEFT report. Under the unchanged
reject_window policy, optional LEFT FAIL now records an explicit skipped RIGHT
dependency with zero geometry and a FAIL result; the real merge consumer rejects
that optional report. Primary LEFT/RIGHT quality gates remain strict, as do GT
independence and coverage requirements. Red tests reproduced both the return-code
error and failed-LEFT dependency error; targeted evaluator/optional-policy/RIGHT
scale tests then passed 38/38, and independent review approved the two-file fix.
No frontend objective, weight, cap, or precision threshold was changed.

The updated evaluator source was pushed to the owned remote at bfee8e1740d5daf1632087da32e33286df9484fb.
A fresh remote archive restored to /tmp/umi-optional-scale-restore-jamiA5 matches
all four newly committed files byte-for-byte; its restored focused source suite
passed 108/108 tests in 2.23 seconds. This remains source/contract evidence, not
precision acceptance or native-runtime packaging.

The fixed-ten continuation is now implemented in scripts/run_metric_joint_fast10.py.
It records frontend/scale/evaluation failures and continues to the next record,
never overwrites an output root, refuses RUNNING reuse, and skips unnecessary
other-eye replay when a source-bound terminal coverage failure is already known.
The queue binds every one of the actual frontend CODE_PATHS, model checkpoint,
and selected paired source manifests/clocks/calibration files before producers.
Objective/checkpoint mutation tests prove it stops before the next producer
rather than mixing source versions across the cohort. Independent read-only
review approved the repaired queue; the focused queue/evaluator/retention suite
passed 37/37 tests in 20.42 seconds, including 14 queue tests.

run_fast9_after_ind2_v1.sh is an offline continuation launcher. It waits for the
exact current ind2 process/start-time identities before launching the remaining
nine fixed records serially, so no second full GPU producer is introduced. It
excludes ind2 (already being processed), preserves take02 RIGHT's known failure
in the denominator, and schedules the remaining failure records before all five
passing controls. Preparing this launcher is not evidence that it has launched
or that any of the remaining trajectories have passed.

Independent RK3576 deployment remains live-blocked by No route to host and the
missing actual ARM candidate/native runtime. No QR, network, udev, package,
recording deletion, reboot, or robot-motion changes were made.

## Fresh completion and environment fault (2026-10-03)

The ind2 RIGHT v2 full replay completed: 1199 actual poses / 1199 input frames,
112 keyframes and 616 metric factors in its final graph; elapsed 2042.490 s.
Both LEFT v1 and RIGHT v2 passed a fresh independent full-coverage/source-bound
frontend validation. This is still not an ATE PASS.

The actual fresh ON primary LEFT stereo report now passes the unchanged gate:
relative_p90_p10=0.4093186696, versus the fresh OFF failure near 0.571. Keep this
incremental component. The evaluator then failed before RIGHT output with
ModuleNotFoundError: rosbag2_py. The RIGHT derive CLI rereads raw DB factory
calibration but was launched without the ROS setup used by the LEFT CLI. The
traceback and a minimal direct load_stereo_calibration reproduction agree.
Fix this orchestration environment, not the frontend objective or quality gate.
There is no ATE from this failed evaluation, and no precision conclusion follows
from its exit status.

The queued continuation did actually launch (PID 3453631): the reused take02
coverage failure is preserved, and take04 LEFT started a fresh full frontend.
Any evaluator source correction is guarded: finish the current producer, then
stop at the next guarded boundary and reuse the completed frontend in a new
queue/output root. Do not kill it, mix source revisions silently, overwrite old
artifacts, or replay a completed frontend just for an environment fix.

An isolated CPU analytic-Jacobian candidate was also verified, not integrated.
The original metric residual, information, objective and current production
solver remain unchanged. The source-bound v2 benchmark compares 5514 factors
over seven frozen contexts / 21 pose sets: approximately 23.23x linearization
speedup; residual/information differences zero; Jacobian max absolute difference
7.4643e-11. H/g relative differences are near 1e-11, with absolute maxima
0.04138136 / 0.00043706 because of the information scale. Full-pose and trajectory
equivalence and ATE are explicitly untested. Independent review found no
mathematical blocker; static type tooling was unavailable, with compile/import
and tests used as substitutes. A fresh focused CPU suite passed 26/26 tests.
Do not switch the frozen accuracy candidate to this derivative mid-cohort.

The ROS launch fix passed a fresh 28-test queue/evaluator suite and the ten
incremental-retention tests; independent review approved the two-file change.
The real RIGHT derive CLI now opens the DB read-only and writes its report.
It returns the existing quality-failure code 2, not a module-import crash: one
isolated position step at frame 576 is 40.514 mm. The same current-code OFF RIGHT,
conditioned on the identical LEFT ON raw observation source, has the same frame
576 jump at 40.783 mm. Thus this defect is present without the retained metric
component. The diagnostic has explicit shared-geometry/selection-bias labels in
ind2_retained_stage_comparison_v1.json; no ATE or independent RIGHT pixel geometry
is claimed. The existing primary continuity gate remains strict. Keep the
effective LEFT scale improvement and investigate the shared RIGHT jump separately.

The isolated derivative source/benchmark and seven small frozen pose fixtures
were pushed to owned sencang at 149e2ac3f83895af3c2d4a9ae393584e13cba710. A fresh
remote archive restored to /tmp/umi-metric-analytic-restore-iNMNWA matches all 12
newly committed files byte-for-byte; the restored analytic test suite passed
14/14. Native runtime/data/calibration/model packaging and full trajectory
equivalence remain outside this source-only backup claim.

run_fast10_after_ros_fix_v2.sh prepares a new source-consistent fixed10 run after
the current guarded queue/producer finish. Its terminal-artifact reuse index
keeps completed LEFT/RIGHT outputs and the known take02 coverage failure; it
does not terminate native jobs, overwrite outputs, or clean up linked sources.

## Incremental rather than binary decisions (2026-10-03 continuation)

The fixed10 v2 queue completed take04 RIGHT with all 1199 inputs in 593.318 s.
Its evaluation records the existing LEFT frame806->807 step at 34.503213 mm;
it does not silently drop the failing record. The queue has continued to take07
LEFT. No fresh ATE or ten-record no-regression acceptance exists at this point.

Two independently inspected failures now locate a missing constraint more
specifically than "graph optimization pulls the geometry": ind2 RIGHT577 and
take04 LEFT807 are non-keyframe poses, absent from every retained metric graph,
and their jump already exists in online tracking. Source hashes, graph brackets,
and export comparisons are in interval_tracking_gap_20261003.md. Independent
read-only review confirms the proposed soft per-frame metric-relative factor
can be added inside the original tracker GN. It remains a hypothesis requiring
actual local inputs, flag-off equivalence and passing controls. Keep the existing
keyframe benefit; do not replace it, cap translation, interpolate, or weaken gates.

The isolated analytic derivative was also tested in seven frozen native graph
contexts, without changing any live frontend/solver CODE_PATH. Four final solves
are bitwise exact (right587, right592 and both passing-control graphs). Three
are not: right613, left877 and left1044 have fresh-original vs analytic translation
differences up to 0.000142554 in native coordinates (not calibrated metres).
Original/analytic repeats are each exact in those three cases, and the fresh
original itself differs from the older frozen solve. All iteration counts stay
at ten and pose0 remains pinned. The original/analytic native solve timing is
roughly tenfold apart, but there is no full-trajectory or ATE equivalence proof.
Retain this exact-objective speed candidate as a development artifact; do not
discard it for a strict bitwise failure or silently integrate it into the frozen
accuracy queue. Existing strict diagnostic failures remain explicit. Evidence:
metric_analytic_native_equivalence_v2/summary.json,
metric_analytic_native_equivalence_v3_right613_repeat/summary.json,
metric_analytic_native_equivalence_v3_remaining4/summary.json.

Fresh local contracts passed 32/32 tests: eight native-probe safety/continuation
tests, fourteen analytic-factor tests and ten incremental-retention tests.
This is source/contract evidence, not 10 mm accuracy or deploy/HIL acceptance.

Review additionally found stale/missing probe runner hashes were accepted on
resume; two red tests reproduced it. A two-line fail-closed guard now rejects
those summaries before modifying them. The final focused suite passes 34/34,
and independent review approves with no remaining issue in that scope. The
earlier native reports keep their original runner hashes and are not relabeled
as runs of this subsequent provenance-only fix.

## 2026-10-03 15:49 UTC: fresh scores and retained backend gains

The fixed10 queue has now completed its five failing-cohort records and started
the passing control `20260927_heldout2` LEFT (1199 inputs). Three records remain
explicit technical/quality failures, not exclusions: ind2 EVALUATION_FAILED,
take02 COVERAGE_FAILED, take04 EVALUATION_FAILED. take07 and take06 completed
scoring but still fail the unchanged accuracy gate. The queue remains live;
no producer was stopped, restarted, or source-modified for this checkpoint.

Fresh scores below are successive backend variants of each SAME NEW frontend,
not strict frontend OFF/ON or historical-version comparisons. All six scores
have 1143 samples and timestamp overlap 1.0. Keep the useful existing backend
components; these rows alone do not establish whole-cohort non-regression.

| Record | Backend | Mean mm | P95 mm | Maximum mm |
| --- | --- | ---: | ---: | ---: |
| 20260929_take07 | fresh symmetric | 4.401765 | 8.881092 | 15.272219 |
| 20260929_take07 | constant gauge | 4.169034 | 8.060292 | 14.584341 |
| 20260929_take07 | physical stereo + constant gauge | 4.014820 | 7.534899 | 12.109613 |
| 20260930_take06 | fresh symmetric | 3.285687 | 5.714138 | 15.219813 |
| 20260930_take06 | constant gauge | 3.274206 | 5.734147 | 14.960596 |
| 20260930_take06 | physical stereo + constant gauge | 3.198423 | 5.611702 | 14.697581 |

Score source: `full_metric_joint_fast10_v2/<record>/eval/` with
`fresh_symmetric_baseline/<record>/both/score/precision.json`,
`constant_gauge/<record>/selected/score/precision.json`, and
`physical_stereo_lever/<record>/physical_stereo_constant_gauge/score/precision.json`.
The selected estimate SHA256s remain
`446659d2128f8077b3d50de8f45942dcd79ba08f17d9ab9777693c4665069604`
(take07) and `f4d0d263629ae7d7b7d479ee67816d01e45876d8008d8030200134783c47458e`
(take06). Workflow manifests confirm unchanged estimates, body origin, frozen
reference manifest b5ac5b8f..., and no SLAM supervision.

Read-only reconstruction using the original interpolation/SE3 scorer reproduced
both selected reports to 1e-12 m (no estimate changes). take07 has 38 >10 mm
samples: a 27-sample block at body elapsed 35.964091-36.830779 s, peak frame1152,
and an 11-sample block at 37.730852-38.064224 s, peak frame1193. Exact timestamps
map both peaks to the actual LEFT source clock (zero time mismatch). The final
LEFT metric graph excludes both peaks: 1152 is bracketed by 1143/1162, and 1193
lies after its last keyframe1162. This newly scored record is not the historical
09-22 handoff's identically sized 27-frame block.

take06 has only five >10 mm samples (within10 ratio 0.995625547), in one block
at elapsed 34.197039-34.330377 s. Peak frame1086 has exact source-clock matching
and is absent from BOTH retained metric graphs: LEFT brackets1060/1088, RIGHT
1059/1087. This narrows the remaining diagnostic to interval-frame states;
graph non-membership is locality evidence, NOT proof of causation or an ATE
improvement from a new local solver. Keep the queued true-local OFF/ON capture,
replay and native-factor probe, then verify full trajectories and controls.
All87 guarded source hashes were freshly unchanged around the read-only take07
reconstruction. No weight, cap, model, continuity gate, or source version changed.

## 2026-10-03 16:05 UTC: reference boundary correction, no candidate rollback

Correction to the previous interval-frame interpretation: absence of take06
frame1086 from both keyframe graphs does NOT establish an interval-state solver
defect. The frozen reference itself contains larger steps next to this peak.
Read-only reconstruction using the original `load_tracker` and
`interpolate_tracker` functions, the frozen camera/Tracker offset and
`tracker_T_body` reproduces reference positions within 8.316e-10 m. Raw
source-clock binding for frames1080–1092 is unique and matches actual D405
`set_index` (not just a guessed row offset). All input hashes stayed unchanged.

| Native frame | Selected ATE mm | Selected step mm | Reference body step mm | Tracker translation step mm | Rotated-lever step mm |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1086 | 14.697581 | 1.953713 | 1.861120 | 2.174297 | 0.394949 |
| 1087 | 8.533513 | 1.451413 | 6.520824 | 6.385578 | 0.326682 |
| 1090 | 4.132653 | 1.856172 | 5.934095 | 5.948064 | 0.091482 |

The larger changes already exist in original SteamVR matrices, before mapping
or trajectory interpolation. Raw sequence4981 moves 7.084002 mm in 8.329561 ms;
the norm of its recorded OpenVR `vVelocity` times that interval is 0.480542 mm.
Raw sequence4991 moves 4.931873 mm in 8.333723 ms versus 0.465679 mm. Their
rotated-lever changes are only 0.072115 and 0.047720 mm respectively. Both
samples are connected/pose_valid, tracking_result200; query durations are
13.317 and 5.004 us. Therefore the sampling/gap/validity checks passed, but
they alone do not certify physical reference accuracy. These norm comparisons
do not assume the API velocity vector's coordinate basis. They also do NOT
prove that physical motion was impossible, establish a base-station cause,
justify filtering points, or establish any new SLAM precision PASS.

Exact take06 inputs:

- Raw: `reports/steamvr_umi_sessions/20260930_170015_slam_validation_six_take06/tracker_camera_window_raw.csv`, SHA256 `4e91e7832f449b33e768281229d236fe93914365f8dd117154e23408d8326b08`.
- Exported Tracker SHA256 `418203dfed1b50b4b03df5208de6809be018da237bdd33cfb5fb7aa949d6210b`.
- Reference: the previous section's physical-stereo + constant-gauge score `steamvr_body_reference.csv`, SHA256 `76fdcad5f3dbe6e619961a20bed67afa284c2da385a05dc404563bd85c0380a1`.
- Estimate unchanged, SHA256 `f4d0d263629ae7d7b7d479ee67816d01e45876d8008d8030200134783c47458e`.
- D405 clock CSV SHA256 `22b62bb86511e7386917bff54881c23ff0c59a0e8f1d2b2c1cefe17c411119e6`; original calibration SHA256 `135e0fdb8862dc52871f1b0948c7d9495398ddefee1932490f293e97c8285519`.
- Mapping epoch-minus-monotonic1790588072.1841109 s; frozen Tracker query offset -12.786861933161967 ms. This is NOT a replacement for formal VINS camera/IMU td.

take07 is a different signature: an independent bounded audit finds continuous
estimate/reference motion around frame1152 and tail1193, with ordinary adjacent
Tracker query brackets, not take06-style reference recovery steps. Continuity
does not itself prove physical reference accuracy or the precise upstream
cause. Its actual remaining shape discrepancy stays a SLAM diagnostic target.

The take07 audit additionally checked the actual recorded OpenVR `vVelocity`,
not merely a velocity derived from position differences. In raw1135–1161 the
largest adjacent raw query translation is 1.467 mm / 8.308 ms (API-velocity
norm times dt 0.849 mm); in raw1188–1198 it is 1.619 mm / 8.226 ms (1.464 mm).
At camera queries1152/1193, Tracker translation steps are 3.030/4.104 mm versus
API-velocity distances2.946/4.092 mm. These are not take06's 7.084 mm recovery
with only0.481 mm velocity distance. The raw CSV SHA256 is
`39adc8aee97df0b6038d457d8d7fcb5e543fdfb2e99ce80bb3b4dc1197dd86c9`;
reference SHA256 `850d06dd184841674fbde4aeabde4537fe8cea004c907c1bea8e170144bca08a`.
All audited rows retain connected/pose_valid1 and tracking_result200. This is
recorded-motion consistency evidence only, not a new truth-accuracy acceptance.

Both scored failure records remain in the fixed cohort with original metrics
and unchanged gates. Useful backend gains remain retained; neither incomplete
control evidence nor maximum ATE still above10mm rejects the whole direction.
The fixed10 producer and queued real local OFF/ON diagnostics remain live and
unmodified; all87 guarded source hashes freshly match. No new production
promotion, estimator code edit, reference rewrite or hardware action occurred.
RK3576: a fresh bounded read-only SSH check still returned `No route to host`;
real ARM candidate and all target/deployment/HIL acceptance remain unverified.

## Fresh RIGHT-only geometry diagnostic: retain evidence, not an accuracy claim

The user distinguishes a useful partial gain from final <=10mm acceptance.
Keep the scored backend gains above while passing-control evaluation is
incomplete. Do not reject the whole direction because final maximum ATE
remains above10mm. Conversely, do not call improvement on one failed record
proof of cohort-level robustness. Original sample counts, failed records,
coverage and accuracy gates remain unchanged.

A specific complementary-path gap was found: take04 has a complete fresh
RIGHT frontend, but the normal evaluator exits on LEFT-primary geometry
failure before checking RIGHT. The existing derived-RIGHT path requires the
LEFT-primary report. A new diagnostic-only entry reuses the original stereo
pair estimator through the tested RIGHT mirror/calibration adapter; it does
not rewrite the frozen evaluator or substitute an older RIGHT trajectory.

New files only:

- `scripts/diagnose_fresh_right_stereo.py`
- `tests/test_diagnose_fresh_right_stereo.py`

Fresh main verification: 44 tests PASS across the new diagnostic, existing
RIGHT motion helper and stereo-scale suite; independent review approves the
diagnostic-only boundary. Before/after hashes bind script, helper modules,
native frontend/config/checkpoint, actual RIGHT timestamps and consumed stereo
images. All87 full10 guarded sources still match. The diagnostic uses no GT,
loads no model, runs no backend, emits no factors, and cannot promote a
production/precision PASS.

Same fixed32 uniformly sampled pairs, full1199-frame native continuity:

| Fresh RIGHT record | Accepted / robust pairs | Scale m/native unit | P90-P10 relative spread | Full continuity | Diagnostic |
| --- | ---: | ---: | ---: | --- | --- |
| 20260929_take04 | 7 / 6 | 0.5053898918 | 0.0614361954 | PASS, max step17.860407mm | PASS geometry only |
| 20260927_ind2 | 13 / 9 | 0.4194577050 | 0.0937230657 | FAIL, one43.677159mm step at index576 (576->577) | FAIL retained |
| 20260930_take06 | 16 / 15 | 0.3480338978 | 0.1940597860 | PASS, max step12.848344mm | PASS geometry only |

RIGHT time binding and stereo skew are exactly0ms in all three diagnostics;
before/after consumed hashes agree. These pair caps are diagnostic sampling,
not a reduction of scoring samples. Original stereo depth calculations emit
existing invalid-disparity warnings; report serialization rejects nonfinite
JSON values, and the original correspondence rejection gates are unchanged.

Evidence lives under `fresh_right_primary_stereo_v1/<record>/` in this
directory. Native trajectory SHA256s are:

- take04: `e89fc1689db79a1e52f5c7a01b184e100edf01d83362448650cae230fb2f22fe`.
- ind2: `e0f2a583f712207dc075404a3c2c5636c27a53cb1eeb50d944b798830bed496e`.
- take06: `7551e66be7b1c5914cf02f00390aad0c40800f197c1f9d59a52ddc0e4f38911b`.

Interpretation: take04's RIGHT source has usable metric geometry and no detected
continuity jump under this bounded check; LEFT failure alone is insufficient
to reject RIGHT's potential complementarity. ind2 independently retains the
known native RIGHT interval jump, so successful scale fitting cannot mask it.
Neither result proves final body-trajectory accuracy or warrants automatic
selection of RIGHT. Next run the identical diagnostic on the fresh passing
control once its RIGHT frontend completes, then evaluate any integration as a
separate source-bound candidate. Do not touch the live queue's frozen helpers.

The full10 producer and real local OFF/ON diagnostic chain remain running/
queued. Passing-control precision and final25 accuracy remain unverified.
