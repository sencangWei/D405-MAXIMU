# Phase9: retain local interior-shape information, diagnostic only

The full graph target remains unmet: joint9/10PASS, worst13.801442mm. This
phase does not produce a new global SLAM trajectory or claim that loss of
intermediate geometry is the unique cause of the remaining offset.

## Bound

Reuse exactly five time-stratified pairs on all ten recordings, 41 raw frames
and nine sampled BA states per pair. Original stereo/gyro model, gates,
training/heldout split, calibration, td and initializations unchanged. Instrument
only nine-state joint solve calls; five-state independent solves remain native.
Retain every refusal. The extra instrumentation is not used for admission,
confidence, graph weight, candidate selection or interpolation.

## Single correlated affine profile

Original solver packing: rotations3(F-1), centers3(F-1), landmarks3P, bias3.
First camera center zero and rotation identity are required when condensing
its Jacobian. Project rotation/landmark/bias nuisance columns away using the
span of their normalized columns, without forming a full row-space projector.

For transformed residual r and Jacobian [Jc Jn], nuisance span Q gives
C=Jc-Q(QT Jc), b=r-Q(QT r). With retained SVD C=U S VT, store W=S VT,
a=UT b, rank/null directions and constant ||b-Ua||². The residual a+W delta
preserves the **local linearized** nuisance-profile objective up to that stored
constant. Keep the entire group, including cross-node coupling; do not split
it into independently weighted displacement edges.

Original pixel residuals are manually soft-robust transformed. This is not
raw-pixel covariance, not a meter sigma, not exact nonlinear profiling, and
cannot be equated to the native 4mm engineering penalty. Gyro information
reuse is explicit. Rank deficiencies retain null axes, not artificial certainty.
Even solver-accepted factors declare available_for_graph=false.

## Verification and actual next output

1. Tests: both endpoint halves correct but interior bow observable; rigid-world
   transform evaluated against the same first-gauge factor; cross-state
   coupling, null/rank0 behavior, sparse packing, finite guards, tall thin-SVD.
2. Linear synthetic explicit nuisance least-squares equals affine profile plus
   stored constant; profile gradient agrees with finite differences. Original
   nonlinear solver results and refusals unchanged by instrumentation.
3. Ten-case/50-pair schedule and both summary input/decoded identities locked.
   Validate source/input hashes before and after; restore hooks/argv in finally;
   refuse output overwrite; unavailable diagnostics remain explicit.
4. Separate endpoint-row and unique-pair-group counts, rank histogram and
   refusal/missing/unavailable counts. Decoded hashes describe loaded image-byte
   identity, not a new disk-file rehash claim. No GT reads before census freeze.
5. Code and evidence committed to owned sencang branch and clean remote restore
   checked bytewise and with fresh tests. No production or graph promotion.

Only after this census freezes can a separately reviewed evaluation compare
all retained interior states with external reference. Such relative-window
errors would still not be full-trajectory ATE or permission to pick factors.

## Pure grouped graph-row prototype, not native graph integration

`scripts/stereo_window_shape_graph.py` represents one correlated group as
`r(x) = b + A x`, retaining all nine exact camera-origin correction nodes.
For eight relative centers, `q_i = R_first^-1(p_i - p_first)`, and each graph
translation correction contributes `R_first^-1(d_i - d_first)`. Optional scale
contributes `s R_first^-1(p_i - p_first)`, **not** a scaled body/camera lever.
Then `b = affine_offset + W(q - q_BA)`; `A` retains the full coupled `W`.

Negative indices, missing interior nodes, aliased translation columns, scalar
scale overlap and invalid diagnostic/frame/gauge flags fail closed. No row is
turned into independent per-node edges or weighted using the old 4 mm penalty.
Candidate-state diagnostics report first-camera LOCAL displacement from BA and
from the initial shape, not an absolute world-position correction certificate.

The present factor is explicitly pixel+gyro-conditioned. This prototype does
not insert extra gyro residuals or claim statistical independence from native
attitude/preintegration inputs. Native production fusion is not edited. A real
consumer must replace the same-source two seam endpoint factors, not append
the grouped profile on top of them, and expose all nine correction nodes.

Synthetic tests independently differentiate physical corrected camera centers
with dense cross-node sensitivity, nonzero affine offset and nonidentity first
rotation. Correct endpoints with a 12 mm bowed interior exercise the information
that the old endpoint-only interface cannot represent. Common rigid transforms,
common translation null space and rank-zero groups have explicit tests.

This still represents only the frozen local linearized profile. Real graph
integration, covariance calibration, nonlinear relinearization and production
admission remain separate uncompleted work; no numeric ATE benefit is claimed
from these row-construction tests.
