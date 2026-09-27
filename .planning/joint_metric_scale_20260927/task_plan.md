# Joint metric-scale state (direction C)

## Goal and constraints

Address fresh heldout2's remaining moving-segment scale/shape error after the
accepted static guard. No raw capture fault found; dominant error is consistent
with scale bias, but IMU bias source is not proven. GT is scoring-only. Do not
use diagnostic Sim3 .968622 as an estimator input. Six fixed datasets, identical
frontend/observations/VINS/config. No14closedfamily sweeps or mode switch.

## Hypothesis

Current graph fixes the global geometric-mean scale before optimizing, and can
only adjust per-node position. Add a single global metric-scale state estimated
by the existing absolute stereo-displacement + IMU preintegration + validated
onboard relative-motion factors, alongside local position/velocity/gravity/bias.
This is a new physical state/constraint formulation, not a stereo-mode ablation
or global weight retuning. Proper camera lever treatment: scale only camera
translation, never rigid calibrated lever. Independent metric evidence must
drive scale; guard keeps original relative micro-motion plus small scale change.

## Steps

1. RED synthetic global-scale + rotating-lever tests; frozen baseline graph
   test fixture. Existing tests must stay unchanged when feature not selected.
2. Implement explicit experimental graph option, defaultoff. Existing factor
   sigmas/gates unchanged. Report solved scale and any physical gate failure.
3. Run firstheldout2 then allsix cached downstream with one identical recipe.
   NoGTchoice or per-take knobs. Inspect allinternalgates and unchangedtimestamps.
4. If max<=10mm in allsix without previouslypassing deterioration, independently
   review and connect option to one-key workflow. Otherwise retain stable static
   guard production, investigate evidence before changing another assumption.
5. Backup verified capability/tests/evidence to owned sencang, fetch/restorecheck.

## Success criteria

Synthetic scale bias corrected from UMI-only metric factors; lever distance
unchanged under rotation. Confirmed static bbox <=2mm. AllpriorfivePASSremain;
heldout2 max<=10mm. All1143/1144timestamps retained, no maskederrorframes.
No broader universal10mm claim from these six development recordings.

## Status

- Implemented optional joint scale state; firstprobe and sixcandidate cases PASS.
- One-key fusion branch enables state after sixcase gate preservation; final
  replay and independent re-review APPROVE. Backup d858a15a fetched/restored;
 94filesbyteequal,99restoredtestsPASS,6rescoredPASSall30numericfieldsmatch1e-12.
- This bounded implementation/verification step is complete. Fresh independent
  recordings are the next acceptance step; no universal max10mm claim.
- Prior accepted staticguard backedcommit27872422, documents189fd1f8.
- 2026-09-27 RED-test slice: add
  `tests/test_mast3r_joint_metric_scale.py` only; verify current failure is the
  missing `solve_metric_scale` kwarg before implementation.
