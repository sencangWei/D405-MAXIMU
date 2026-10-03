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
