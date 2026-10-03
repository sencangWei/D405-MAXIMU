# Original local-Sim3 replay: retained candidate, not precision acceptance

## Decision rule and current boundary

The user's correction is enforced by `assess_incremental_progress` in
`scripts/run_independent_ir_fast_regression.py`: improvement in most fixed
failing cases plus all five passing controls staying <=10 mm with comparable
complete coverage retains incremental progress, even when failures remain.
Incomplete comparisons and control regressions are not proof that the whole
direction is useless. Current joint-metric candidate is retained, not rolled
back or promoted. The fixed10 source/model/config guard remains unchanged.

Fresh retention tests: 10 PASS. The fixed10 queue is running under PID 3491727,
start time 23315633. Three records have terminal technical/quality failures
(ind2, take02, take04), not new comparable ATE scores. Take07 LEFT and RIGHT
both completed with 1199/1199 actual poses. Its fresh primary LEFT stereo
dispersion 0.4226080567 passes the unchanged gate; downstream evaluation is
still running. Neither coverage nor this internal metric is precision PASS.

## New local-state observation, with source binding

For original online poses and Sim3 scales, the two already identified
non-keyframe translation jumps coincide with abrupt local scale changes:

| source | actual frame | relative local scale change | translation step (native units) | rotation step |
| --- | ---: | ---: | ---: | ---: |
| ind2 RIGHT | 576 | +6.480787% | 0.031284767 | 0.593648 deg |
| ind2 RIGHT | 577 | +13.142606% | 0.111872592 | 2.600763 deg |
| ind2 RIGHT | 578 | -12.422538% | 0.009800698 | 1.157425 deg |
| take04 LEFT | 806 | +8.157331% | 0.009568169 | 2.706429 deg |
| take04 LEFT | 807 | +43.157463% | 0.122704657 | 0.973924 deg |
| take04 LEFT | 808 | +1.211267% | 0.026395229 | 0.912070 deg |

Scale CSV sources relative to this directory:

- `full_metric_joint_frontend_v2/20260927_ind2/right/mast3r_logs/dataset_online_sim3_scale.csv`
  SHA256 `e18e51fd3f693bdc6a3d180d35c73331a1bdb893c0f64250451e2f339219dce5`.
- `full_metric_joint_fast9_v1/20260929_take04/left/mast3r_logs/dataset_online_sim3_scale.csv`
  SHA256 `1bc93ffcbaf4af48476dfed02081b2eb9cea62bf8e476217c0bd668ade73dcee`.

Changes use s[f]/s[f-1]-1 and actual frame IDs. Translation steps are NOT
metres or ATE. This is co-occurrence, not proof that scale caused the jump or
that raw stereo depth is wrong. It motivates inspecting the original local
seven-state solve, not altering global translation, forcing a scale or clipping
frames. Online/full export provenance is in `interval_tracking_gap_20261003.md`.

## Diagnostic completion criterion

Capture the original model/matching inputs for these six actual frames with
the retained candidate, then call original `FrameTracker.opt_pose_calib_sim3`
on CPU twice from fresh tensor clones. Compare to the captured opt return,
not the later post-IMU final frame pose. Keep original objective, confidence,
seven-state increments, convergence and source/model bindings. No model/GT,
robot or VINS translation input is added by the CPU reader.

Installed original LieTorch/FrameTracker CPU smoke already ran without a
model/GPU: a nonzero synthetic initialization converged in 3 original steps,
cost 395.20227 -> 9.2153e-6. This proves the installed CPU call path works;
it does not establish real snapshot equivalence, a precision fix or acceptance.

The native capture scheduler PID 3569517 / start 23591558 is waiting for the
exact fixed10 queue. `replay_after_tracking_capture_v1.sh` waits for that exact
scheduler, then serially attempts six CPU replays, preserving failure logs in
a new output root. Preparing it does not establish that it has launched.
No queued frontend, running script, config or original native method is edited.

Only after actual input/output equivalence is measured can a local metric
factor experiment be compared against this baseline. Preserve beneficial
global metric components, score fixed failing5 first and passing5 next, then
full25/new captures. No 10 mm acceptance is claimed here.

Independent RK3576 deployment remains blocked by target access and missing
actual ARM candidate/runtime. No board mutations, QR changes, recordings
deletions or live robot motion are part of this SLAM diagnostic.

Fresh source verification: installed MASt3R venv replay/capture/runner/retention
suite 68 PASS in 3.42 s, including original CPU solver and multi-frame/hash/
iteration-trace regressions. Independent review approved the four diagnostic
files, not precision. AST parse, launcher bash syntax and diff checks passed.
The launcher actually started as PID 3609162 (exec session 72775) and printed
its exact-scheduler wait message. The fixed10 guarded source/model hashes were
rechecked unchanged. Actual captures and CPU replay results are still pending.
