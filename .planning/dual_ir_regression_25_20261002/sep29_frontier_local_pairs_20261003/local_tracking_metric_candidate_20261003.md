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
early CPU-config error did not prove CPU matching works. Forward asymmetric
Xii/Xji tensors cannot be swapped to stand in for native reverse Xjj/Xij.
The reverse requires a second original asymmetric decoder call in reversed
frame order. Captured encoder feat/pos/img_true_shape avoid image encoding,
not that decoder call or GPU matching. Local GN replay is CPU.

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

## True reverse decoder and exact forward validity (2026-10-03 continuation)

Independent source review rejected swapping the forward asymmetric pointmaps
as an equivalent native reverse: the original symmetric path uses separately
decoded Xjj/Xij and Djj/Dij. That proposed implementation is not being used;
this does not reject the retained metric-relative candidate or its partial gains.

The observer now also saves encoded feat/pos/img_true_shape for both actual
forward input frames, after original inference populated its lazy encoder cache.
This adds tensor observation only, with no extra model/matcher call in tracking.
The isolated reverse helper calls the unchanged asymmetric decoder once with
keyframe first and current frame second, reusing those captured encoded inputs.
Actual model/GPU work remains queued until the retained fixed10 run is idle.

The CPU forward selector checks the unique current/keyframe match association,
actual warm start and native optimizer inputs. It keeps raw matcher validity
separate from C/Q-filtered tracking validity; metric graph prep must use the raw
field. Captured native Q is preserved for thresholds/output. CPU Q recomputation
is a formula diagnostic with at most two representable-step rounding difference,
not a new matching/gate tolerance. Above-bound mismatches reject the input.

Review of the initial selector found five defects; all were surgically fixed
without abandoning the direction. It was then approved as diagnostic-only.
CPU mocks and new contract tests are source validation, not real reverse match,
accepted bidirectional PnP, measured ATE improvement, or 10 mm acceptance.
The fixed failing-five/passing-five cohort, complete coverage and final full25
plus new-recording acceptance requirements stay unchanged.

The real producer uses encoded_inputs.frame_i/frame_j and outer input frame IDs,
not current/reference aliases or invented inner IDs. RED tests exposed this
fixture/producer mismatch and a reversed returned provenance binding; both were
repaired before use. Reverse helper review is APPROVE with zero remaining
findings. Fresh main-owner combined observer/forward/reverse/replay/local-factor/
capture-runner/retention suites pass 130/130 in 3.80 s. Six source/test files
parse successfully; all 87 guarded live-queue source hashes remain unchanged.
No native reverse decode/match, local-factor or ATE result is implied by these
mock validations. The queue has moved from take06 LEFT to RIGHT; passing-five
controls and the scheduled real input capture/replay are still pending.

## Real-pair diagnostic wiring; not a new precision result (2026-10-03)

`probe_local_tracking_metric.py` now connects the captured actual forward
match, true native reverse decoder/matcher, original bidirectional stereo/PnP
gate, and local seven-state OFF/ON solve. `local_tracking_pair_graph.py` uses
raw forward validity, canonical current/reference pointmaps, actual native Q,
and the unchanged local graph configuration. It rejects legacy filtered-mask
aliases and invalid configuration rather than silently changing the inputs.

The capture runner freezes raw current/reference stereo image hashes after
verifying the preserved prefix identities. The probe verifies those images,
capture/native/model/config inputs and all six probe helper files before ON
and before/after retaining the actual pair artifact. Gate rejection and probe
failure remain explicit diagnostic outcomes, not proof the direction is useless.
No Tracker, VINS translation target, trajectory interpolation or new cap is used.

Independent review approved this source-only slice after two artifact binding
issues were repaired. Fresh main-owner nine-suite tests pass 150/150 in 4.02 s;
the CLI help check and diff whitespace check pass. Source-change tests cover
both accepted/rejected gate branches; CPU fake tests check actual reverse
tensors, raw validity and native Q formula. These are not native GPU, real
factor, ATE or production acceptance tests. All 87 guarded queue hashes still
match. The live fixed10 queue has four terminal records and is on take06 RIGHT;
passing-five controls are not yet scored.

`probe_after_tracking_replay_v1.sh` schedules the six actual local probes only
after the exact capture and CPU replay chain exits, freezes one helper revision
for all six, and preserves independent failures without abandoning the candidate.
Its runner still refuses a busy GPU. The retained keyframe candidate is not
rolled back. Final full25 and independent new-recording acceptance remain open.
