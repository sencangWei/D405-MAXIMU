# Sep29 take02: new local learned-geometry evidence

This is a cause experiment, not a new accepted trajectory or a 10 mm claim.
The five-failure/five-pass development cohort remains frozen. No precision
thresholds, model weights, solver caps, calibration or production defaults change.

## Exact recording and original failure

Session: `d405_720p_rgb_stereo_ir_20260929_195058`, not Sep30 take02 retry.
Both prepared input datasets contain 1199 frames. Original LEFT output stops
at input586 (587 poses) and skips587; RIGHT stops587 (588 poses) and skips588.
There is no evidence that the complete Sep30 take02 rescue artifacts restore
this Sep29 record. Original logs lack numeric first-failure match/pointmap
diagnostics; repeated relocalization fails rather than an export/input cap.

## Frozen actual pair probes

`frozen_pointmap_results.jsonl`: eleven actual GPU pair inferences over failed
LEFT/RIGHT and passing Sep30 take03 LEFT. `keyframe_pointmap_results.jsonl`:
three extra inferences against the actual saved previous keyframe image IDs.
Model/config are unchanged; source/config guards match before and after.
The model is loaded once per batch; no full frontend/SLAM/scorer is run here.
Commands are preserved as two executable Python artifacts. They require the
MASt3R virtualenv/toolchain PYTHONPATH and CUDA library path, as in the normal
workflow. Raw source PNGs are reused; recordings remain untouched.

| Record / eye | Pair, current -> reference input | Raw 3D pass % | Descriptor-refined 3D pass % |
|---|---|---:|---:|
| Sep29 take02 LEFT | 586 -> 585 | 20.72 | 9.92 |
| Sep29 take02 LEFT | 587 -> 586 | 2.65 | 2.21 |
| Sep29 take02 LEFT | 587 -> 584 (not a saved KF) | 42.86 | 35.53 |
| Sep29 take02 LEFT | 587 -> 585 (saved previous KF) | 45.36 | 48.35 |
| Sep29 take02 RIGHT | 588 -> 587 | 0.95 | 1.08 |
| Sep29 take02 RIGHT | 588 -> 585 (not a saved KF) | 48.00 | 49.07 |
| Sep29 take02 RIGHT | 588 -> 576 (saved previous KF) | 29.10 | 23.75 |
| Sep30 take03 LEFT, passing control | 587 -> 586 | 78.86 | 82.70 |

These are freshly inferred pairwise pointmaps before real tracker's accumulated
keyframe state, confidence intersection and pose solve. They are **not** its
exact `n_opt`, a metric displacement estimate, or ATE. Raw 3D distances are in
model units. Right datasets have no stereo-depth provider; the new opt-in
`--pointmap-only` explicitly marks metric depth unavailable, rather than using
left-eye depth in right coordinates. The default LEFT stereo path/output is
unchanged. Four lightweight branch/CLI tests and the existing43 suite pass:
47 PASS. Independent critic approved with no blocking issues.

An earlier three-pair LEFT stereo probe is retained as `left_exploratory.jsonl`:
its diagnostic source was edited during execution, so it is not a frozen
authoritative run. Frozen pointmap-only results reproduce its raw matching
fractions, but do not authenticate its metric/shape-correction outputs.

## Causal interpretation and falsifier

The local learned geometry becomes strongly reference-dependent at the actual
loss frontier; both eyes' adjacent matching degrades. Merely choosing one eye
instead of the other is not a sufficient repair here. Older real keyframe
images still have useful local matches. This supports testing reference reuse,
not proof that it fixes shape/scale/ATE or that all failures share this cause.

The existing optional previous-KF retry is restricted to age<=8 input frames.
For LEFT587 the prior saved KF585 meets that condition; RIGHT588's previous
KF576 is12 frames old and does not. Do not silently increase that bound or
declare a usable pose from the pointmap fraction alone.

Two new full, state-faithful LEFT frontend replays completed from frame0
with identical input/config/code and explicit disabled other rescue flags:
control (`MAST3R_TRACK_PREVIOUS_KF_RETRY=0`) versus existing retry (`=1`). Each
writes `MAST3R_MATCH_LOG` and normal workflow artifacts; no Tracker input is
provided. `frontend_replay_commands.json` preserves exact commands. Restore
coverage is only a first check: quality/scale/rotation and frozen ATE plus
passing regressions are required before promotion. No new fusion estimate has
been accepted. Final 25-record and independent-recording requirements remain.

## Terminal native frontend A/B, 2026-10-03

Both workflow processes finished with exit0 (control184s, retry190s), with the
same prepared1199-frame LEFT dataset, checkpoint/config and other rescue flags
disabled. Control's587-row trajectory is byte-identical to the original Sep29
LEFT artifact. The retry produces1199 native rows over39.926119089s, rather than
interpolating a missing tail. Its exact trajectory hash and actual retry match
counts are preserved in `frontend_terminal_evidence.json`.

At input587 actual tracker optimization candidates rise from3845/147456 to
66706/147456 with the previous saved keyframe. Seven rejected calls at inputs
587,588,590,591,592,594,595 recover on that path. This confirms the specific
tracking-tail symptom can be repaired; it does **not** prove recovered geometry,
scale, gyro consistency or10mm ATE. A previous Sep30 rescue restored coverage
but failed scale/rotation checks, so coverage alone is not an acceptance gate.

Existing stereo-PnP scale/continuity estimation ran against this recovered full
trajectory, using the unchanged synchronized prepared LEFT and RIGHT images
and factory baseline. It did not rerun MASt3R or read Tracker. No production
defaults change. Only a geometrically valid candidate proceeds to the frozen
five-failure/five-pass regression and eventual full25/blind test.

The previously committed diagnostic code/results (88f7fd0a) were fetched from
`sencang/codex/dual-ir-frontend-20261001`, restored into an isolated directory,
and all9 changed files compared byte-for-byte. The restored47 tests passed.

## Actual downstream falsifier: coverage restored, source continuity FAIL

Both fresh stereo runs finished with exit3. Short windows have1190 candidates,
699 accepted observations and685 robust scale inliers; estimated scale is
0.3704053526450242m/model-unit, relative dispersion0.27266934865318376. Long
windows have687 candidates,169 accepted and168 robust inliers; scale is
0.3632688295798062, dispersion0.25667161150473405. Both scales pass the existing
dispersion calculation but **both full reports FAIL** because of the same
isolated step808->809:37.229mm short-scale /36.512mm long-scale. There are no
timestamp gaps; synchronized stereo skew is0ms. Summary/hash evidence is in
`retry_metric_quality_evidence.json`; complete raw reports remain in the
untracked `retry_metric_quality_v1/` experiment directory.

At that boundary the final native trajectory differs from online: online step
is8.739mm at the same diagnostic scale. Frame809 has59625/147456 optimization
points (40.44%), and is not one of the seven retry failures. Saved keyframes
are791(index105),809(index106),810(index107); final full translation809 equals
final keyframe translation809 exactly (quaternion normalization introduces tiny
rounding differences). Consequently an incorrect ordinary-frame export anchor does
not explain frame809 itself. Evidence favors graph-induced/keyframe-state
relative disagreement. It does not yet identify the bad graph edge or establish
an exporter/backend fix. The online trajectory is not a substitute: its largest
step at877 is138.272mm under the same diagnostic scale.

Calibrated-branch correction: the initial printed config says `use_calib=False`,
but workflow `--calib` overrides it **after** printing; the real active tracker
branch is `opt_pose_calib_sim3`, not ray/distance. Neither `stereo_keyframe_pnp`
nor `stereo_fix_pose_scale` is active. These are branch facts, not a proposal to
repeat closed parameter families.

The ordinary IMU scale command then failed closed (exit1):
`choose_attitude_scale()` rejects a stereo report whose full result is FAIL.
No new metric trajectory, fusion candidate or precision PASS was written.
Independent critic rejected broadly accepting failed reports: scale evidence
may be diagnosed separately, but sourceFAIL and final gates must remain intact.
The next repair target is native graph/pose consistency at the keyframe switch,
not interpolation, an increased jump bound or a hidden scale-gate bypass.

Fresh calibrated400Hz gyro comparison (stride10, formal td=-0.009109323) is
diagnostic only:119 intervals, median0.411deg/P952.432deg/max3.440deg; restored
tail59 intervals median0.570deg/P952.478deg/max3.240deg. Original prefix58
intervals P951.779deg; no precision/rotation acceptance is inferred from these
numbers. Existing source stages bind old LEFT587/raw+metric hashes; inserting
new1199 LEFT reports into them would violate provenance and index bounds.

## Observation-only graph snapshot: narrower cause, not a repair

A new exact1199-frame LEFT retry replay saved the existing default-off graph
snapshot at raw frame809. It completed with exit0; workflow reported118s.
Its native trajectory is byte-identical to the prior retry (SHA256f50045dd...),
so capturing the snapshot did not change the measured source. The1.2GB snapshot
remains local; its hash, tensor checks and one unchanged calibrated-GN replay
are recorded in `graph809_replay_evidence.json`.

The107-keyframe snapshot has finite tensors and in-bounds edge indices. Its
first solve moves KF809 by0.04125965 **model units, not millimetres**, while
KF791 moves0.03332065. At this solve KF809 has only two incident directions:
791->809 and809->791. There is **no accepted retrieval incident edge at809**
in this snapshot; deleting alleged false loops here is not evidence-backed.
This initial update does not by itself explain the final37.229mm discontinuity.
The remaining bounded diagnostic is parent-relative motion across this and
subsequent graph updates. No failed source report is promoted, no continuity
gate is relaxed, and no frontend/fusion precision improvement is claimed.

Development regression remains the fixed failure-first5+passing5 set in
`config/dual_ir_fast_regression_10_20261003.json`, not a repeat of all25 on every
iteration. Its cached-backend run took460.7s;6 records passed,2 scored failures
and2 technical/unscored cases remain. New frontend source changes require fresh
source bindings and measurements. Full25 plus fresh recordings remain final
acceptance requirements. Fresh fast-runner tests:6 PASS.

## Subsequent graph update pinned: frame877, not first809 solve

One existing observation-hook replay completed in118s with255 graph events.
Its1199 native final **and online** trajectories are byte-identical to the prior
retry. Using parent791's Sim3 **before** solve809, recovering frame808's stored
relative translation and transporting it through every later solve reproduces
actual final808 within2.253e-7 model units; frame809 matches exactly. The saved
trace therefore explains the actual exported discontinuity, not a guessed
parent or a different replay. Numeric evidence/hashes are recorded in
`backend877_transport_evidence.json`; raw trace remains local.

At the unchanged short diagnostic scale (not an accepted metric source), the
808->809 step is9.888mm after solve809 and7.514mm after solve865. **Solve877
changes it to36.308mm**, followed by37.197mm at891 and37.229mm at final1044.
The solver accepts742->877,731->877 and865->877. This pins the triggering
update, but does **not** prove these are false loops or that removing them is
a valid repair.

Independent existing free stereo-PnP checks actually become more consistent
with those three keyframe relative poses after solve877: all three pass their
existing pair checks then, versus direction/rotation failures for the two
nonlocal pairs before. After scales are0.3962/0.3597/0.3688;742's forward/reverse
scale spread is19.5%, and731 uses the single-direction SIFT fallback, so these
are not absolute-accuracy truth. The865->877 bidirectional spread is1.26%.
Raw808->809 stereo rejects both online direction and final rotation consistency;
the online trajectory cannot be substituted as a geometrically valid fix.

The source failure is now localized to a subsequent graph correction plus
ordinary-frame single-parent transport disagreement. Root cause of the
underlying conflicting learned geometry remains to be isolated. No metric
or fusion source is promoted, no gate is changed, and there is still no new
10mm precision result. Other scored fast10 failures are being checked
independently so this one incomplete record does not monopolize optimization.

## Exact ordinary-frame capture for one bounded structural falsifier

The default-off process-local hook in `dense_capture_hook/sitecustomize.py`
was independently reviewed, then captured actual ordinary frame808 (parent791,
reference index105) during one full native replay. Both new-KF and failed
tracking returns are refused. Seven focused tests pass, including scalar-bool
compatibility. The full1199-frame source completed in118s; final and online
CSV files are byte-identical to the previous retry. The actual pointmap,
confidence, cached encoded features, intrinsics and Sim3 data are CPU-cloned,
not fabricated or interpolated.

The simultaneous graph877 capture contains121 keyframes and19 calibrated-GN
arguments. Replaying the unchanged native solver reproduces the independently
observed graph event **exactly**: before and after maximum element difference
both0.0. Hashes/checks are in `dense808_capture_evidence.json`; large tensor
snapshots stay local, outside git. System-helper import warnings and CUDA IPC
teardown warnings did not affect the native capture; exit code0 is verified.

This is still sourceFAIL and not a precision improvement. The next single
variable falsifier is a copied graph with an explicit captured dense808 state
and original-threshold learned geometric links to791/809, keeping every
original edge and solver setting. It must refuse an isolated state, wrong
source images/config/checkpoint, or insufficient matching; it must not score
or promote a diagnostic graph. After a source-valid repair exists, use the
fixed failure-first5+passing5 regression set, not all25 per development cycle.

## Bounded explicit-state falsifier: local structural improvement, not ATE

`probe_dense_native_graph.py` passed independent review after its runtime
binding/device/rejection/serialization defects were fixed; ten CPU tests pass.
One real execution completed with `DENSE_EDGES_SOLVED`. Both learned pairs
clear unchanged0.1 admission in both directions:791/808 fractions0.227/0.459,
808/809 fractions0.383/0.366. Every one of the original584 directed edge rows
is preserved. Original GN arguments/settings/gauge are unchanged; only the
actual captured dense state and its learned geometric links are added.

The808->809 distance after solve877 changes from0.0980222 model units under
single-parent transport to0.0124957 under the explicit state. This is **not**
an ATE result or a valid full metric source. Independent unchanged stereo-PnP
then accepts808->809:direction cosine0.99827, rotation disagreement0.300deg,
332/369 inliers, bidirectional scale spread4.28%, measured motion5.557mm.
The prior online/final pair checks failed direction/rotation respectively.
See `dense808_graph877_probe_v1.json` and
`dense808_stereo_pair_diagnostic.json`; no external reference enters this probe.

This supports the dense/graph structural hypothesis for the exact captured
pair without deleting loops, raising correction caps, interpolating points or
changing the source gates. Remaining discriminators are continuous-window
state consistency, other loop-pair preservation and multi-record controls.
No production code is changed and no new10mm pass is claimed.

## Continuous native window and two historical passing controls

The default-off hook now accepts an explicit comma-separated frame list and
requires a `{frame_id}` path template for multiple captures. Independent review
approved the observational replay. Actual captures are:

| Record | Ordinary interval | Parent / next KF | Captured GN | Replay seconds |
| --- | --- | --- | --- | --- |
| Sep29 take02 (source failure) | 792–808, 17 frames | 791 / 809 | 877 | 118 |
| Sep27 heldout2 (historical pass) | 723–751, 29 frames | 722 / 752 | 752 | 110 |
| Sep29 take01 (historical pass) | 712–737, 26 frames | 711 / 738 | 738 | 92 |

Control windows were selected by native consecutive-keyframe gaps only, not
local GT errors. All three full1199-frame final and online exports reproduce
their respective existing sources byte-for-byte. The failure replay retains
previous-KF retry; controls require no retry. The new graph877 capture equals
the prior snapshot in all19 arguments; unchanged GN reproduces the recorded
121-node after-state with maximum element difference0.0.

`probe_dense_window_native_graph.py` uses actual captured pointmaps/confidence/
encoded features/fullSim3 states, adds the entire ordinary interval to a copied
native graph, and connects its adjacent chain with symmetric learned matches.
All original graph edge rows and solver arguments remain unchanged. Rejected
chains are not solved; input/output Sim3 arrays fail closed on invalid shape,
scale, quaternion or finiteness. Independent review approved execution after
that post-solve validity guard was added. No GT/Tracker input enters the solve.

All three actual probes return `DENSE_WINDOW_SOLVED`:18/30/27 learned pairs
pass the unchanged0.1 threshold, with minimum directional fractions
0.29175/0.55258/0.25880 respectively. Failure808->809 step decreases from
0.0980222 to0.0116792 native units (88.1%); this is NOT millimetric ATE.

`check_dense_window_stereo.py` runs the existing free stereo-PnP estimator on
every adjacent pair and the failure's three877 loop pairs. Its threshold,
factory calibration, seed and method are unchanged. Actual results:

| Scope | Baseline accepted | Explicit window accepted | Remaining unscored/rejected |
| --- | --- | --- | --- |
| Failure18 adjacent +3 loops | 6/21 | 12/21 | Same9 low-excitation pairs |
| Heldout2 adjacent | 30/30 | 30/30 | None |
| Take01 adjacent | 8/27 | 8/27 | Same17 low-excitation +2 bidirectional-scale rejects |

All six direction/rotation failures in the first scope disappear without
dropping any pair;742/731/865->877 stay accepted.808->809 direction cosine is
0.99187, rotation discrepancy0.33676deg, bidirectional spread4.12%. The two
passing-control windows gain no extra geometric rejection; this is not yet a
full-record ATE non-regression claim. Raw disparity warnings are preserved.

44 targeted CPU tests pass. Source/parameter/tensor/image hashes and all original
and dense pose states are in the six `*_probe_v1.json`/`*_stereo_v1.json` reports.
Large snapshot tensors stay local, outside git. Existing unrelated stereo helper
edits are preserved; they only add functions, while the estimator used here is
unchanged.

The full candidate remains unscored and the10mm goal unmet. Safe next step is
to capture the FINAL graph plus native `tracked_poses` (explicit anchor indices
and full relativeSim3). Text exports lose scale and cannot substitute for that
runtime state. First reproduce the original full1199 export exactly, then run
the structural candidate through genuine source gates and fixed failure-first
5+passing5 regression. Full25 and new blind recordings follow only after that.

## Final native export and full-source stereo validation (2026-10-03)

The default-off `export_capture_hook/sitecustomize.py` captures the actual final
128 keyframes and all1199 `tracked_poses`, including each original anchor index,
full relativeSim3, clock scalar, dtype and device. Original native export executes
first and is not modified. The hook needs the toolchain's `thirdparty/mast3r`
directory on PYTHONPATH as well as the root; a native-venv startup assertion is
required because Python can ignore sitecustomize import errors. The first capture
had that import failure and is retained as an unsuccessful capture, not a source.

The corrected replay in `left_retry_final_native_export_v2/` took119s and emitted
1199 actual poses, not interpolated poses. Both original final and online CSVs
are byte-identical to the previous retry source. The final graph1044 has128
keyframes and624 directed edges. Its unchanged GN replay exactly matches all
recorded final keyframe states. Adding the same17 ordinary states792..808 and
18 accepted learned chain pairs preserves all624 original edges and GN
parameters. The final808->809 step changes0.1005091->0.0118062 native units.
Independent stereo checks improve6/21->12/21 accepted pairs; the same9 pairs
remain low-excitation, and all three877 loop pairs remain accepted. No loops
are deleted and no matching/source threshold is relaxed.

`export_dense_window_native_trajectory.py` verifies source/dense tensor hashes,
the exact original keyframe baseline, complete0..1198 coverage and all adjacent
pair decisions before using the ORIGINAL native `save_full_traj` function.
All non-window ordinary frames retain their original relativeSim3 and anchor
index; the17 jointly optimized ordinary states are exported as explicit anchors.
No interpolation, trajectory cropping, Tracker supervision or GT input is used.

The first offline export failed its byte-equality gate: numpy.float32 f-string
formatting differs from str(), and CPU vs native CUDA Sim3 composition differs
at float rounding precision. Those fidelity issues were corrected, not tolerated
with a weaker comparison. Exportv2 exactly reproduces the original full file:

- Original/baseline SHA256: `aa21ec3a29bd24d1c53a6f549a43e6a42f8230c680ad068289f8da1138a5e00c`.
- Variant SHA256: `b28a42e929b883d04b75a88b4be373a1acbdf10d2b33bc7860e3aa2343dc4793`.
- Full native snapshot SHA256: `fc5dfe6ec5da79541207ace135d06929c65ed4ab31cd7a22e5b53a08c65108cd`.
- Probe SHA256: `a65c6cf47ce28cc1d3a7745dfee95846974af559893da2008f63cf9591e0937d`.

The existing converter's `--require-complete` check passes with1199 source
poses. Fresh full-source short stereo scale validation evaluates all1190 pairs
and returns PASS:697 accepted observations,688 robust inliers, scale0.3698707613
m/native-unit and dispersion0.2790218. The unchanged full-trajectory continuity
gate returns PASS with0 jumps,0 unverified gaps and maximum contiguous motion
step26.1143mm. That is a MOTION STEP, not ATE. The previous37.229mm isolated
source discontinuity is no longer present. This result is only full-source scale
and continuity validation; no fusion10mm pass or production promotion is claimed.

Independent code review approved the hook/exporter after the clock and device
fixes. Fresh targeted tests:66 PASS (22 new hook/export +44 existing diagnostic
and fixed10 tests). Large pointmap/runtime tensors remain local, outside git.

The fixed development cohort is still exactly failure5 followed by passing5
from Sep27–30 (`config/dual_ir_fast_regression_10_20261003.json`). Do not rerun
full25 per edit, change the cohort to improve a score, discard failures, or claim
cached old sources validate a new front-end candidate. Source-independent
caches may be reused only with unchanged hashes; changed front-end outputs
require new source-bound scale reports and downstream manifests. Full25 and
new blind recordings follow only after fixed10 stabilizes.

The current paired consumer still binds LEFT through the old baseline candidate
manifest; the old RIGHT588-frame fallback is derived from that old LEFT source.
Neither is a valid substitute for this new LEFT1199 source. Genuine complete
RIGHT recovery and a new paired lineage (or an explicitly separate partial-source
diagnostic contract) are needed before claiming a new fixed10 paired regression.
The new full-source PASS does not erase this remaining failure or meet the goal.

## RIGHT retry-age cause diagnostic and fixed10 policy (2026-10-03)

The user reconfirmed the development loop: fixed failing recordings first,
then five fixed historical passing controls from Sep27–30, about ten total.
The checked-in fast10 configuration/runner enforces those cohorts and phase
order. Full25 is reserved for a stable candidate and then fresh blind data;
unchanged, hash-bound source caches can be reused, but a new front-end cannot
be validated by replaying an old source and presenting its old score as new.

RIGHT prepared input is complete1199 frames. Its original native output ends
at587, with588 exported poses. At frame588, active reference587 fails while
the earlier keyframe576 has a usable learned geometric match. The existing
8-frame previous-reference limit blocks this12-frame-old alternative.

`run_native_retry_age_diagnostic.py` makes one runtime-only AST substitution,
8->12. It enforces the reviewed exact main SHA and exact comparator/env guard,
fails closed for conflicting retry environment, preserves native spawn module
identity, and restores process globals. Actual toolchain source, checkpoint,
configuration, first tracking attempt, and matching thresholds are untouched.
Independent review initially requested four fixes; after those fixes and11
fresh tests it returned APPROVE. This is a cause diagnostic, not a promotion.

The real native replay exits0 but is still incomplete:589 actual poses0..588,
not1199. At588 the normal valid optimization count is1260/147456 (0.85%);
the retry against576 is31160/147456 (21.13%) and succeeds. Native snapshot
records588 anchored to576, with no new keyframe added. At589 the next normal
attempt still targets587 and yields325/147456 (0.22%); the576 alternative is
now13 frames old and no retry is attempted. Final keyframes remain576 and587.
Therefore increasing the horizon only moves the stop by one frame. It is not
an accuracy fix and does not justify another horizon sweep.

Actual hash-bound result is in `right_retry_age12_native_v1/diagnostic_result.json`.
The native snapshot remains local; text telemetry and diagnostic code are
backed up. Full-source scale and paired ATE are deliberately NOT run on this
incomplete candidate. Next investigate persistent recovery reference/pointmap
state: a successful alternate-reference recovery must not immediately revert
to an unusable active reference. A subsequent repair still needs complete
native coverage, unchanged geometry/continuity gates, failing-five first and
passing-five controls before any full25 or production claim.

Fresh targeted tests:46 PASS across retry-age, fast10 and dense/native export
diagnostic tests. An earlier command used a nonexistent fast10 test filename
and ran no tests; the corrected command was executed and its46-pass result
read before this report. Goal remains ACTIVE and the10mm maximum is unmet.

## Latched recovery reference and right-source false-PASS repair (2026-10-03)

`run_native_latched_reference_diagnostic.py` keeps the normal first tracking
attempt unchanged. A successful alternate reference is retained only while
the active tail index remains unchanged; normal primary success or a changed
tail clears it. The initial retry uses the fixed, already-tested12-frame seed
allowance, not a parameter sweep. Every retry stays `update_reference=False`
and matcher state is reset before/after. Existing keyframes and graph edges
are not modified by the diagnostic retry path. The reviewed exact native main
SHA, checkpoint and configuration are unchanged. Nine focused latch tests,
the existing11 retry tests and independent code review pass.

First launch `right_latched_reference_native_v1/` failed before tracking: wrong
working directory made the native inherited `config/base.yaml` inaccessible.
No trajectory from it is usable. The corrected launch runs FROM the toolchain
root, preserving that failure log and using a freshv2 output directory. Helpers
reading db3 also require BOTH ROS environment setups; the first scale helper
invocation missed rosbag2_py and was rerun correctly. Neither launch failure is
a SLAM matching failure or a reason to alter source/accuracy gates.

The real `right_latched_reference_native_v2/` replay exits0 and exports614
actual poses0..613, without interpolation or a fabricated tail. Online0..588
is byte-identical to the age12 control. Frame589 now retries KF576 successfully
with30251/147456 valid optimization points (20.5%); frames590 and591 also recover.
Normal tracking returns at592. Other local recoveries607/609/610 are recorded.
At614, BOTH primary and alternate fail the unchanged5% threshold:2889 and2643
valid optimization points. Thus the original reference-lifecycle failure is
recovered, but the full RIGHT source is still incomplete and not a10mm result.

Source geometry also fails. Final614 scale is0.4373702320,280 accepted stereo
observations/253 inliers, but has3 isolated step jumps (first586, max63.709mm).
Online614 also fails continuity:1 jump at591, max55.772mm. Final large boundaries
are586->587,587->588 and591->592, corresponding to alternate/native keyframe
reference transitions. This is source motion-step evidence, NOT Tracker ATE.
Neither final nor online candidate is admitted into a new fusion score.

`derive_right_ir_stereo_scale.py` previously emitted top-levelPASS whenever
scale was observable even with nested `trajectory_continuity.result=FAIL`.
The surgical repair computes continuity once and propagates its canonical
failure reason into top-levelFAIL and return code2. Existing thresholds, scale,
observation values and trajectory are unchanged. RED reproduces rc0 on a failed
continuity test; GREEN covers failed/pass continuity and unobservable scale.
Real corrected final/online reports both exit2. The final report's scale and
all observations are exactly equal to the preceding false-PASS artifact.

Read-only manifest-bound audit of all25 historical candidate source reports
finds4 records with a right-source false-PASS:20260927_ind2,20260929_take02,
20260929_take06,20260930_take01. One record has no candidate manifest. All read
report hashes match the candidate manifests. This is a cached SOURCE audit,
not a new25 SLAM benchmark and not proof that this bug explains all ATE failures.
Evidence: `right_scale_nested_continuity_corpus_audit_v1.json`.

The old RIGHT588 baseline is not a safe fallback: it too declares topPASS while
its nested continuity fails (one jump at586, max53.989mm). Do not recycle it as
a valid partial-eye source. Architecture review clarified that bilateral factors
CAN represent a shorter right timeline and complete left timeline: complete per-
eye artifact sets and local raw/metric matching are required, not equal lengths.
However all source gates still apply; full fused/reference coverage and10mm
maximum are unchanged. A valid partial source can complement another eye, but
an invalid partial source cannot be relabeledPASS or cropped to make this work.

Fresh targeted tests:61 PASS, including6 right-scale tests. Reviews approve both
latched diagnostic and continuity propagation. No production promotion, Tracker
supervision, cap/age sweep or full25 rerun is claimed; fixed failing5+passing5
remains the next regression stage after a source-valid paired candidate exists.

## Exact reference-transition/GN diagnosis (2026-10-03; not a new ATE result)

The user fixed the development loop to five historical failures FIRST plus
five frozen passing controls from Sep27–30. Config remains
`config/dual_ir_fast_regression_10_20261003.json`; cached data may be reused,
but an unchanged cached score is not a fresh candidate validation. Do not
replace difficult records, run full25 after every edit, or promote on fast10
alone. Final acceptance still requires the full25 and independent new captures.

The default-off reference-transition capture hook records actual states and
exact original 19-argument native GN inputs without changing native source.
The actual RIGHT replay at `right_reference_transition_native_v1/` has 614
actual poses and is still incomplete. Its full/online trajectory and match
telemetry are byte-identical to the previous latched control. Re-encoding
15 captured frames (577–591) gives exact feature/position equality; five fresh
rematches also reproduce indices and fractions exactly. Capture/cache feature
corruption is excluded for this window, not for all recordings.

The generalized dense-window helper preserves all 40 original graph613 KFs
and 178 directed edges, including interior KF587. It rejects the actual trial
before variant GN: 10/16 adjacent learned pairs pass, the last six fail the
unchanged 10% GRAPH threshold. The 5% tracking threshold is separate. There is
no candidate export/fusion ATE from this rejected trial. A star diagnostic
576→577..591 passes all 15 pairs, but 576→592 fails reverse matching. Adding
only leaf nodes cannot independently bridge the original bad transition, so
no star-only GN experiment was run.

Native pre/post GN replay is exact at graph587,592,613 (maximum pose delta0).
The CPU factor auditor uses native directed mapping and masks, with common
pre/post support; its float64 Huber analysis is NOT a claim of bit-exact CUDA
kernel cost. At587 the 576→587 common-support pixel median worsens from
0.646 to8.831px while541→587 pixel P95 improves111.13→34.58px. This proves
incompatible factor demands, not that the retrieved541 edge is false.

Independent raw paired D405 depth plus the ORIGINAL learned indices refutes
that proposed false-loop deletion: BOTH541↔587 and576↔587 pass the existing
bidirectional metric PnP gate. Forward P95 reprojection is1.569/1.231px and
cycle translation is3.272/0.846mm. No Tracker, new match indices or relaxed
thresholds enter this evidence.

The follow-up pointmap/pose audit records a more specific source failure:
native PRE-GN rotations agree with independent stereo PnP to0.816/0.411°;
POST-GN rotations disagree by8.568/9.223°. Thus GN moved away from two valid
independent observations in this slice. After a per-frame median depth ratio
fit, canonical-vs-stereo absolute relative depth P95 is21.6/29.7/29.9% for
541/576/587. Those percentages and pose discrepancies are source diagnostics,
not ATE, proof of universal model failure, or permission to delete/reweight
original factors. The next falsifiable comparison is pre/post source geometry
consistency against the fixed failing and passing controls. Missing pre-GN
captures must be reported, not inferred from final poses.

Evidence (small text artifacts; native PT/image captures remain local):
- `right_reference_transition_graph613_probe_v1.json`
- `right_reference_transition_feature_identity_audit_v1.json`
- `right_native_reference_star_edge_audit_v1.json`
- `right_reference_transition_exact_gn_factor_audit_v1.json`
- `right_original_KF587_bidirectional_stereo_factor_audit_v1.json`
- `right_KF587_stereo_pointmap_pose_consistency_audit_v1.json`
- `right_latched_reference_export_audit_v1/export_audit.json`

Test hygiene regression: direct removal of torch from sys.modules by capture
tests caused a combined-suite TORCH_LIBRARY re-import failure (8 failures and
exit139). Replacing only those test removals with monkeypatch.delitem restores
the original modules on teardown. The corrected combined targeted suite has
74 PASS/exit0. The native production hook is unchanged by this test repair.
This is diagnostic progress; no new fusion10mm PASS is claimed.

Fresh combined verification also includes the six failure-first fast10 runner
tests: 80 PASS/exit0. No production SLAM candidate was launched just to repeat
unchanged cached results. Raw sensor data, thresholds and deployed native
toolchain files remain unchanged.

## Depth-shape falsifier completed; fixed10 policy retained (2026-10-03)

All seven predeclared native graph trials completed. Original GN repeated twice
with exact control replay. Only native pointmap XYZ changed: stereo-valid depths
were normalized by each frame's median metric/native depth ratio, preserving
its original median gauge and rays. Matching indices, confidence, graph edges,
thresholds and the other18 native arguments were unchanged. No Tracker/GT data
entered the experiment. Inputs/source manifests and native production files
were hash-bound before execution. Raw graph PT inputs and output pose tensors
remain local; source code, plan and measured JSON evidence are backed up.

Maximum rotation disagreement against independent stereo PnP on unchanged
accepted chronological graph pairs (degrees; NOT trajectory ATE):

| Graph | Accepted pairs | Original GN control | Depth-shape trial |
|---|---:|---:|---:|
| failure take02 RIGHT587 |22|9.209804|9.014641|
| failure take02 RIGHT592 |22|10.671543|10.356316|
| failure take02 RIGHT613 |35|10.967111|10.591717|
| failure take02 LEFT877 |114|3.589815|1.282934|
| failure take02 LEFT1044 |121|3.589809|1.281200|
| passing heldout2 graph752 |23|1.193247|0.847675|
| passing take01 graph738 |41|1.189147|1.051942|

Median disagreement improved in all seven graphs, but the large RIGHT error
persists. Depth-shape error contributes to the LEFT geometric inconsistency;
this trial does not establish a complete bilateral repair, a new fusion ATE,
or10mm acceptance. The two passing controls show no regression in this measured
diagnostic only; that is not proof of unchanged end-to-end precision. Do not
promote the diagnostic or claim that all25 failures have this same cause.

Fresh targeted verification:20 tests PASS, including six fixed10 runner tests
and14 depth-shape helper/runner tests. The broader preceding combined suite
had94 PASS; this section does not present that older run as a fresh result.
Evidence: `depth_shape_GN_falsifier_plan_v1.json` and
`depth_shape_GN_falsifier_v1/summary.json`, with all seven job `report.json` files.

The user's latest validation policy remains:
1. Keep the fixed historical failure5; execute them before passing controls.
2. Keep five passing recordings from Sep27–30; do not substitute them per edit.
3. For each genuine candidate rerun all affected stages with matching source
   hashes; reuse unchanged caches only. An unchanged cached score is not a new
   validation. Missing/invalid sources remain visible failures, never cropped.
4. Compare maximumATE, P95, RMSE, within10mm ratio and full coverage on the same
   ten recordings; passing controls must not lose their10mm gate.
5. If a candidate fails, localize its first source/factor inconsistency before
   another targeted change. Once the fixed10 is stable, run full25 and fresh
   independent recordings for acceptance. This development set is not blind.

## Stereo support partition and isolated fixed-scale falsifier (2026-10-03)

The source-only support partition audit completed on all seven preceding
graphs (28 directed edges), with original native common pre/post masks intact.
At RIGHT576->587, both-stereo-supported16830 points have median Q3.454 and
pixel residual0.601->5.040px; unsupported20277 points have Q2.930 and
0.690->16.092px. The reverse direction also worsens in both classes.
Passing heldout2 has substantially more unsupported50616 than supported12835
points, without the same failure. Thus the simple "unsupported/low-confidence
background causes the bad solve" explanation is not established. No residual
mask, point deletion, confidence weight or threshold change follows from it.
Measured evidence is `stereo_support_partition_audit_v1.json`.

New source-constraint experiment, predeclared before its seven-graph run:
keep original pointmaps, pixels, edges, matching/confidence, Huber and iteration
settings. Derive target scales `s_i=s0*r_i/r0` from the frozen independently
measured raw-stereo/native-depth median ratios for every original keyframe.
Remove the seventh scale row/column BEFORE the SparseBlock solve; solve only
translation/rotation. Reuse original calibrated CUDA residual/Jacobian and pose
retraction, with zero scale increment. Pin frame0 as in the original kernel.
This is not post-hoc trajectory warping or a new soft-prior weight sweep.

The isolated source/build directory is `fixed_scale_native_backend_v1/`.
No production files, installation, configuration, recordings or thresholds
change. Original source hashes and explicit -O3/compute120 build flags are
pinned. Local CUDA compilation required existing conda bin/nvvm/bin in PATH;
no package or system configuration was changed. Four actual CUDA synthetic
tests passed (multi-iteration fixed scales/pin, zero iterations, invalid target
fails before mutation, byte-exact isolated original versus production control).
CPU/static plus fixed10 runner verification:21 PASS. Native retained-Jacobian
finite-difference tests are still missing; this diagnostic cannot be promoted
on those tests alone.

Falsifier criteria: original versus isolated native control must be byte-exact
on each real graph; all target scales/pinned pose and repeated solves must be
exact. On the same seven original chronological pair sets, RIGHT worst stereo
PnP rotation disagreement should drop below2deg (toward its pre-GN geometry)
without either passing control's maximum disagreement increasing. Otherwise
reject this candidate before expensive full frontend/fixed10 execution, do not
sweep weights/scales or call an internal diagnostic an ATE improvement.
If supported, implement source-valid full frontends and run the fixed failure5
then passing5 with refreshed affected sources. Full25 remains final acceptance,
not the every-edit loop.

### Terminal measured result: fixed-scale candidate rejected

All seven real graph jobs completed. The original isolated control is
byte-exact with the production CUDA solve on every graph; fixed-scale repeated
solves, zero scale increments, target scales and frame0 pin are exact.
The hypotheses' geometric criterion failed, so no full frontend/fixed10 ATE
run or promotion follows for this variant:

| Graph | Original worst stereo-PnP disagreement (deg) | Fixed scales (deg) |
|---|---:|---:|
| RIGHT587 |9.209804|9.571253|
| RIGHT592 |10.671543|11.308754|
| RIGHT613 |10.967111|11.599211|
| LEFT877 |3.589815|3.722947|
| LEFT1044 |3.589809|3.723057|
| passing heldout2 |1.193247|1.174376|
| passing take01 |1.189147|1.328916|

This refutes the sufficiency of fixing the independent per-KF median scales
ALONE under the original learned pointmaps. It does not prove scale is always
irrelevant, or that combined pointmap geometry has been repaired. The earlier
depth-shape trial improved LEFT but not RIGHT. Remaining source conflict must
be localized (including the learned pointmap's calibrated ray geometry) before
any new source change; do not combine more unconstrained weights or perform
another full25 unchanged rerun. These numbers are degrees, NOT ATE millimetres.
Production, calibration, gates and sensor recordings remain unchanged.
Evidence: `fixed_stereo_scale_GN_falsifier_v1/summary.json` and seven job reports.
