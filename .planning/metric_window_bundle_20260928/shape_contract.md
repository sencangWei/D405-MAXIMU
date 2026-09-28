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
