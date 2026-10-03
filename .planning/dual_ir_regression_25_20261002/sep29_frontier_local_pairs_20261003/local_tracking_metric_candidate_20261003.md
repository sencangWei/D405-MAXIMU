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
optimizer inputs and asymmetric decoder X/C/D/Q. They do not contain an
independently computed reverse match or stereo depths. A real two-frame factor
must reload hash-bound stereo depths and recompute TRUE forward/reverse native
matches, verify the forward mapping against the capture, then run the original
bidirectional PnP/cycle gate. Never invert indices or borrow another graph edge.

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
