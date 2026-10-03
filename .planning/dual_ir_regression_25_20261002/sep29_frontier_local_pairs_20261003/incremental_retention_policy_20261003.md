# Incremental progress is not final acceptance

Latest user instruction: a candidate that improves most failed recordings must
not be discarded just because some recordings remain above 10 mm. Check the
historical passing recordings first; preserve useful increments and optimize
the remaining causes on top. Do not mistake one proxy threshold for a verdict
on an entire architecture.

Implemented in `scripts/run_independent_ir_fast_regression.py`:

- Fixed historical failure5, then fixed passing5; no changing the cohort to fit
  the candidate. Same sample count and no reduced timestamp overlap.
- Majority failure improvements with all passing controls still passing and
  max ATE <=10 mm: retain incremental candidate, not production acceptance.
- Mixed improvements: keep the experiment and its measured regressions.
- Passing-control regression: do not adopt that candidate; keep its evidence.
- Missing scores/technical failures: incomplete comparison, not proof that the
  direction is useless.
- Solver and production selector still have no external ground-truth input.
  Evaluation uses the reference; final max ATE <=10 mm gate is unchanged.
- Fixed10 success is not full25 success or an independent new-capture claim.

## Current evidence snapshot (2026-10-03, not trajectory ATE)

`pnp_supported_outlier_mask_falsifier_v3/summary.json` contains seven completed
native frozen-graph solves. Its predeclared strict geometry criterion failed;
the raw verdict remains unchanged as experiment provenance. RIGHT maxima
improved 9.2098->8.9510, 10.6715->10.6101, 10.9671->10.9376 degrees. Both LEFT
graphs improved slightly but depleted more than half the supported matches on
at least one accepted edge. These are weak/mixed proxy signals, not proof that
all related directions are useless and not ATE improvements.

The new true joint metric/native objective has completed three RIGHT graphs
of the SAME failed recording: 9.2098->7.5296, 10.6715->8.1446,
10.9671->8.4393 degrees. Common native support stays above99%; common dense
cost changes by +0.700%, +0.662%, +0.492%. Original-control replay, repeated
joint solve and pose0 pin are byte-exact on these three graphs.

The first LEFT graph (`left877`) then reported `joint repeat/pin invariant
violated`. Treat that as technical execution failure, not a scientific
negative or an ATE verdict. Remaining LEFT/PASS controls are still running at
this snapshot. Frozen contexts cover three recordings in total; three RIGHT
graphs are not three independent trajectories. No production promotion and
no fresh fixed10/full25 trajectory precision claim have been made.

Verification: 32 targeted CPU tests passed; independent reviewer approved the
incremental-retention wiring and nonidentity forward/reverse Sim3 test. These
prove implementation contracts, not 10 mm precision.

Next: finish the controls, retain partial source evidence, diagnose technical
repeat/pin failures without silently weakening the invariant, then regenerate
affected full frontend trajectories for the fixed10 paired ATE comparison.
Only that comparison can justify an incremental trajectory baseline.
