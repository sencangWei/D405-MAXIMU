# Isolated native grouped-shape consumer: preimplementation contract

Status: design only. Full290 UMI-only capture is still running. This document
does not authorize production promotion, a new real graph run, or GT-based
selection. Full-trajectory accuracy remains joint9/10PASS/max13.801442mm.

## New structural experiment, not another parameter sweep

Retain one coupled first-camera-gauge profile for all nine local stereo BA
centers. Existing endpoint-only integration loses the interior coupled shape.
Do not change the raw schedule, gates, native weights/caps, calibration, td,
selector or downstream complementary/smoothing pipeline. No extra gyro rows.
The profile is pixel+gyro-conditioned, not calibrated covariance or independent
of native IMU information; any result is initially diagnostic only.

## Matched-node controls

1. Replay original baseline for bit-identity.
2. Endpoint control retains the same-source seam endpoints and forces the
   union of native required nodes, endpoints0/last and all accepted nine-state
   group indices.
3. Grouped-shape control forces exactly the SAME nodes and replaces only the
   corresponding stereo translation LSQR rows with compact correlated rows.

Preserve the native accepted observation list and its order. Upfront filtering
is NOT equivalent: it changes local_stereo_scale_state-derived visual priors,
scale references, confidence ordering and IRLS edge bookkeeping. That was a
design blocker found during independent review, not an observed new SLAM bug.

## Exact row-level replacement

For N correction nodes, E accepted native stereo edges and R=N-1 when a
relative-motion trajectory is present (otherwise R=0), the native stereo block
starts at `3N + 3 + 3*max(N-2,0) + 3R + 6*(N-1)` and has3E rows.
Expected total rows add gravity/bias6 and3 rows per static pair. Guard dimensions
on EVERY native lsqr call and fail closed if source layout/hash changed.

Remove exactly the six rows from the two same-source endpoint factors for each
available accepted group, never a partial pair or another stereo source.
No duplicate group IDs; accepted-but-unavailable shape remains explicit, not
silently counted as integrated. All other rows and target entries stay exact.
Append `J_shape*x = -r0`, retaining off-diagonal cross-node coupling. Appended
rows equal the sum of compact ranks; rank-zero groups remain explicit.

Native unknown columns are position3N, velocity3N, gravity3, accelbias3,
optional metric-scale1. Shape affects position and optional scale only. Scale
derivative uses camera translation `p_i-p_first`; physical body/camera lever
is not scaled. All nine group nodes must be exact, not interpolated proxies.

Keep the four native IRLS iterations, preintegration, clipping and full-rate
refinement unchanged. Removed endpoint weights may still update internally but
are inert because their rows are removed on EVERY iteration. Restore scoped
hooks in finally; zero-group no-op must be bit-identical to native execution.

## Predeclared diagnostics, not a confidence certificate

Record per-iteration removed endpoints/rows, appended rank rows, accepted edge
ordering/count, all-nine inclusion, initial/final grouped residual norms and
their ratio, max local displacement from initial and BA reference, and native
clipping counts. Reject nonfinite/rank/dimension/provenance violations.
Do not invent a millimetric trust-radius pass gate from this uncalibrated affine
profile or choose thresholds using GT. Describe linearization displacement;
it does not certify nonlinear accuracy or eliminate shared gyro information.

Distinguish the LSQR solution from the actually returned camera trajectory:
native correction caps and the unchanged later full-rate refinement may alter
local shapes. Report grouped residual/displacement at the uncapped solution,
after native cap and at final camera output separately. Do not label the
uncapped solution's residual as the final SLAM trajectory's residual.

## Before any real graph or score

Freeze full290 summaries and hashes, prove unchanged solver endpoints and
admission on allten against the old full census, review/test the isolated
consumer (including exact ancillary-row identity and exception restoration),
back up source to owned sencang and verify clean remote restoration. A separate
review must then approve bounded graph execution. Freeze ALL30 graph variants
before opening GT; score complete trajectories with the existing no-scale SE3
contract, retaining all failures and all pose rows. Target remains allten
max<10mm with no prior-pass regressions, followed by newly captured validation.
