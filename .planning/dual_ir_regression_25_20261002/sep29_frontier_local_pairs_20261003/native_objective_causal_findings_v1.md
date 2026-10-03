# Native objective causal audit — 2026-10-03

This is source-only diagnostic progress, NOT a new fusion ATE or 10mm PASS.
The native production frontend/backend is unchanged. Frozen seven graphs come
from THREE independent recordings (five graphs of failing Sep29 take02 and two
passing controls); do not report them as seven independent recording passes.

## Fresh evidence

1. `native_objective_change_v2.json`: seven original-control solves decrease
   the float64 analysis true-Huber objective on COMMON pre/post masks. RIGHT
   graph587 delta -2,898,718; graph592 -1,709,710; graph613 -484,942. Thus mask
   support loss alone does not explain the falling cost. This is not native
   bit-exact objective evaluation or proof every iteration decreases cost.
2. `native_derivative_audit_v1/report_v2.json`: original hash-pinned CUDA
   `calib_proj_kernel` H/g exposed in an isolated extension; no solver invoked.
   Thirty directed worst/control edges across seven graphs agree with float64
   fixed-pre-mask central differences using independent homogeneous matrix
   exponential left retraction. Worst gradient relative error 6.55e-5, IRLS
   J^T W J relative error 1.83e-6; native-vs-independent retraction max 5.18e-7.
   This rules against an H/g/sign/retraction bug on AUDITED edges, not every
   possible native graph state. H is IRLS, not the exact true-Huber Hessian.
3. PnP consistency partition: independent stereo-consistent matches on RIGHT
   576→587 start good and are pulled bad (count16413, medianQ3.47,
   pixel median0.59→4.91). Accepted PnP guards an edge but its RANSAC membership
   does not replace native dense correspondence masks. Direct <=2px classes
   are geometric-consistency classes, NOT RANSAC membership or causal proof.

## Coupled state falsifier, predeclared and rejected

Depth-shape-only and median-gauge-only negative trials did not by themselves
exclude a coupling between these states. The coupled trial simultaneously uses
Z'_ik=D_ik/r_i and fixed s_i=s0*r_i/r0, so supported world-depths share one gauge
s_i Z'_ik=(s0/r0)D_ik. Unsupported points, original directed edges, masks, Q/C,
sigmas, iteration count and thresholds are unchanged. Original replay exactly
matches frozen control; repeated variant, fixed targets, zero scale increments,
and pose0 invariants pass. No reference/Tracker input or trajectory scoring.

| Graph | Original max PnP rotation disagreement, deg | Coupled, deg |
| --- | ---: | ---: |
| right587 | 9.2098 | 9.4363 |
| right592 | 10.6715 | 10.8358 |
| right613 | 10.9671 | 11.0358 |
| left877 | 3.5898 | 1.4196 |
| left1044 | 3.5898 | 1.4195 |
| passing_held2_752 | 1.1932 | 0.8187 |
| passing_take01_738 | 1.1891 | 1.0496 |

All three RIGHT graphs fail the predeclared <2deg criterion. Candidate is
REJECTED_GEOMETRY_CRITERION; do not promote LEFT-only improvement or run full25
for this rejected candidate. Summary verdict was explicitly added AFTER solve;
the original solving-runner SHA is retained, and no fresh solve/ATE is claimed
by the metadata amendment.

## Next branch

The tested native objective favors a compromise that moves good metric geometry
away from the independent stereo solution. Scale/shape conditioning alone or
together is insufficient for RIGHT; stop this conditioning family. Distinguish
which graph factors/point classes exert the conflicting demand before a source
fix. A physically accepted stereo/PnP guard currently only guards acceptance,
not consistency of dense point constraints. Next experiment must test that
boundary causally; don't infer causality from conditional residual statistics.

Any genuine source-valid candidate must regenerate affected frontend outputs,
then run the fixed historical failure5 FIRST + Sep27–30 passing5. Keep failures,
reference quality failures, and full25 denominator; never relax the10mm gate.
Only after stable fixed10 run full25 and new independent capture. Reusing
unchanged scores is not new validation. Goal remains ACTIVE and unachieved.

## Validation and backup scope

Actual isolated CUDA inspection contracts: 2 PASS. The graph audit also uses
actual CUDA, not a mocked backend. CPU tests cover retraction, frozen pose-SHA
requiredness, source provenance, criterion rejection/no false precision pass.
Back up only source/text/tests; compiled extension, PT graphs and images remain
local. Independent read-only code review requested and remediation applied.
