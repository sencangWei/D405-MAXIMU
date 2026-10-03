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

Two new full, state-faithful LEFT frontend replays have been launched from frame0
with identical input/config/code and explicit disabled other rescue flags:
control (`MAST3R_TRACK_PREVIOUS_KF_RETRY=0`) versus existing retry (`=1`). Each
writes `MAST3R_MATCH_LOG` and normal workflow artifacts; no Tracker input is
provided. `frontend_replay_commands.json` preserves exact commands. Restore
coverage is only a first check: quality/scale/rotation and frozen ATE plus
passing regressions are required before promotion. No new fusion estimate has
been accepted. Final 25-record and independent-recording requirements remain.
