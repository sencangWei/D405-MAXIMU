# Deterministic joint-factor linearization repair

This is a technical repair of the retained joint metric/native candidate, not a
new weighting experiment or a trajectory precision result. The user policy in
`incremental_retention_policy_20261003.md` remains in force: partial improvements
are retained; final max ATE <=10 mm and fixed failure5/passing5 are unchanged.

## Measured failure, before repair

`metric_joint_repeat_diagnosis_left877_v1/diagnostic_run.json` is a terminal
DIAGNOSTIC_FAILED run, elapsed233.149s. Its invariant diagnostic separates:

- pose0 pin exact, maximum pin delta0;
- repeated joint solves differ by6.92605972290039e-5 in MASt3R native units,
  **not metres**;
- iteration0 poses_before and native H/g are byte-identical;
- iteration0 joint H/g, dx and updated poses first differ.

A separate CPU-only test prepared the actual LEFT877 factors once (121 poses,
562 factors/281 accepted pairs), then repeated factor5/edge295/frame270->0.
Residual and information were identical; Jacobian maximum difference was
2.240652108298491e-12. SciPy logm consumed global NumPy RNG state. Installed
SciPy1.14.1 logm uses randomized onenormest in its order/scaling selection.
This proves a stochastic metric linearization component, but does not by itself
quantify its amplification into the full solver difference.

## Surgical change

Only `_sim3_log` changed. For a valid positive-scale Sim3 matrix[sR,t], compute
sigma=log(cbrt(det(sR))), principal SO3 omega=Log(R), A=sigma I+[omega]x, and
W=integral_0^1 exp(uA)du. The upper-right block of exp([[A,I],[0,0]]) gives W,
including A=0. Solve W rho=t. Return[rho,omega,sigma]. This is the same exact
Sim3 residual as before without the randomized generic matrix logarithm.

No new factor multiplier, normalization, damping, clipping, admission threshold,
pose pin, convergence setting, production source replacement or GT input.
The usual SO3 principal-log ambiguity at exact pi is not changed or claimed
solved.

## Fresh verification

The RNG-preservation regression test failed before this edit. After the edit,
17 targeted tests passed: factor math/preparation, joint normal adapter, trial
verdict and exact replay invariants. Added tests check RNG independence, exact
repeat, generic-log oracle agreement, exp/log round trip, near identity,
scale-only and sub-pi large rotation. Independent code reviewer: APPROVE,
no issues; py_compile passed.

The same seven frozen contexts are replaying into
`metric_relative_joint_native_falsifier_v2/`, not into prior evidence. The first
two RIGHT contexts completed with exact repeat/pin and the previously measured
geometry improvement unchanged. This is not fresh full-trajectory ATE, not a
fixed10/full25 acceptance result, and not production promotion.

Next: finish exact same-cohort replay, then regenerate affected frontend
trajectories and compare fixed failure5 FIRST plus passing5. Majority failure
improvement and no passing-control crossing10mm justify an incremental
development baseline; they do not replace final25/new-capture acceptance.
