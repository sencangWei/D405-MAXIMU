# Retain keyframe gains; test the missing per-frame constraint

This is an isolated diagnostic extension of the retained metric-relative
candidate, not a replacement, production release, or precision acceptance.
The user explicitly requires retaining useful partial improvements, with the
fixed failing five first and the five passing controls next. All records stay
in the denominator; full25 and independent new-recording acceptance remain.

## Evidence and hypothesis

The source-bound interval audit locates ind2 RIGHT577 and take04 LEFT807 outside
every retained metric keyframe graph. Their position steps already occur in
online tracking. Simultaneous local scale changes are evidence of coupling, not
proof that scale alone caused them. See interval_tracking_gap_20261003.md and
tracking_opt_replay_20261003.md; native pose units are not calibrated metres.

Hypothesis: the same already accepted onboard stereo relative factor can
constrain these local seven-state tracking solves, retaining the existing
keyframe factor instead of rolling it back. Only the local normal contribution
changes. No position interpolation, translation cap, new weight, sigma, learned
model, quality gate, or external trajectory supervision is introduced.

## Experiment contract

- Keep the original `opt_pose_calib_sim3` loop, retraction and convergence test.
- Use current/source to reference/target, `poses=[G, identity]`, with
  `G=T_WCk.inv()*T_WCf`. The first seven metric Jacobian columns are the local
  left-retraction increment. Add full information to H, negative J-transpose
  information residual to g, and its cost to the native robust visual cost.
- OFF must call the original path exactly. ON rejects mismatched/reversed frame
  ids, nonaccepted PnP, invalid information, fixed scale, and VINS translation
  target. This helper is CPU-only and does not load GT or a model.
- Synthetic math tests are not real-factor, trajectory, ATE, or 10 mm evidence.

## Real-input boundary

The scheduled captures retain the exact forward indices, canonical pointmaps,
optimizer inputs and asymmetric decoder X/C/D/Q. The capture-only observation
extension below also records the actual native match inputs and raw outputs.
They do not contain an independently computed reverse match or stereo depths.
A real two-frame factor must use the actual captured forward match, reload
hash-bound stereo depths, obtain a TRUE native reverse match, then run the
original bidirectional PnP/cycle gate. Any forward replay must use the captured
warm-start index and actual metric arguments and be checked against the capture.
Never invert indices, substitute an empty initial guess, or borrow another edge.

Native matching is GPU-only: `gn.cpp:108-121` dispatches to `iter_proj_cuda`,
and `matching_kernels.cu:279-318` launches a CUDA kernel unconditionally. The
early CPU-config error did not prove CPU matching works. Raw captured decoder
tensors avoid a new model forward, not GPU matching. Local GN replay is CPU.

The current fixed10 producer and exact-process capture/replay schedulers remain
untouched. No extra GPU work is started while the retained queue is active.
Actual snapshot equivalence, local factor availability/effect, and all passing
control precision checks are pending; none is inferred from synthetic tests.

## Fresh source verification (2026-10-03)

Implemented in local_tracking_metric_candidate.py with focused tests in
tests/test_local_tracking_metric_candidate.py. Independent read-only review
requested changes, then approved after the metric normal retained float64
assembly (casting only completed H/g to native dtype), strict absolute
information symmetry checking, and nonzero OFF-path equivalence tests.

Fresh main-owner combined local/replay/capture/runner/retention suites passed
86/86 in 4.10 s with the installed MASt3R Python runtime. The two source files
also pass AST parsing; git diff --check is clean. The ten specific partial-gain
retention tests independently pass. Synthetic ON tests check signed translation
and scale correction with full information cross terms; OFF tests compare final
poses and the complete nonzero increment trace bit-for-bit for float32/float64.
Exception/success tests verify exact restoration of an existing instance method.

This is a new diagnostic capability, not measured trajectory improvement. No
real selected-frame snapshot or independently gated local factor is available
yet. The original fixed10 queue remains live on take06 LEFT, with four terminal
records preserved (three technical/quality failures and take07 scored). No
passing-five control result or 10 mm acceptance is inferred from these tests.

The independent RK3576 deploy task still lacks verified board access/identity
and the actual ARM candidate/native runtime. No board, release, recordings,
network, packages, QR settings, or hardware-motion changes were made here.

## Exact matcher observation prerequisite (2026-10-03 continuation)

Source inspection found a concrete reproduction gap: `FrameTracker.track`
passes its previous `idx_f2k` as the native matcher's initial index. Recomputing
with the default identity mapping is a different input, not an equivalent
reproduction of the failed tracking call. This is a diagnostic prerequisite
correction, not a newly established trajectory root cause or ATE improvement.

The process-local observer now wraps the exact `mast3r_utils.matching.match`
module function only while a selected tracking call is active. It records
pre-call CPU clones of X11/X21/D11/D21, actual initial indices and metric
arguments, loaded matching configuration, raw returned indices/validity, and
the corresponding asymmetric inference call index. Return identity and the
original function are preserved, including on exceptions. It adds no matcher
or model invocation and changes no production math, gate, weight or cap.

Actual calls may include an optional stereo-conditioned retry or a different
reference keyframe. Future factor preparation must identify the unique actual
current/reference call and confirm its returned indices against the captured
optimizer's mapping; ambiguity is not resolved by taking the last call blindly.
The reverse correspondence remains a separately computed native match, not an
inverted forward map. CPU-only mock contract tests do not establish native
matching equivalence, factor availability, precision, or passing controls.

The retained keyframe candidate and the running fixed10 queue remain unchanged.
At this checkpoint take06 LEFT is active after four terminal records; the
passing-five cohort and the scheduled real-input captures remain pending.
Partial gains remain development evidence, not a binary reject or promotion.

Fresh main-owner capture/replay/local-factor/runner/retention source tests pass
90/90 in 3.77 s with the installed MASt3R Python runtime. Independent read-only
review inspected the exact native call site, approved this diagnostic-only
observer change, and separately passed all 19 capture tests. AST parsing and
diff whitespace checking pass; dedicated static type tooling was unavailable.
No selected-frame native snapshots or ATE results are created by these tests.
