# Full-timeline geometry pilot — 2026-10-03

Development evidence only. No Tracker supervision, backend run, precision
improvement claim, production promotion, frame deletion, or gate relaxation.

## Hypothesis and scope

The 20260929_take02 LEFT/RIGHT MASt3R saved trajectories end around 19.5 s,
although the native D405 input spans 39.926 s and the device keeps moving.
Existing rejected-row recovery cannot propose observations beyond those saved
trajectory domains. A new proposal source must use the full D405 timeline.

The isolated probe samples twelve uniformly distributed 30-frame pairs after
both saved frontend endpoints. It reads selected native DB3 IR images and
factory calibration, evaluates the existing SIFT/PnP geometry in both time
directions and both eyes, and retains every accepted and rejected outcome.
VINS body poses are converted separately to LEFT/RIGHT camera poses for native
direction/rotation/scale gates. Acceptance therefore uses onboard evidence,
but is not independent of VINS consistency checks.

## Correctness repairs before the valid pilot

- Validate VINS binding only at selected endpoints: the unselected D405 prefix
  precedes VINS initialization by about 1.862 s. No extrapolation or tolerance
  widening at selected endpoints.
- Apply camera rotation and lever arm instead of passing body poses as camera
  poses. RIGHT uses the measured factory transform, not an assumed rotation.
- Read selected frames directly from DB3, bypassing a possibly partial prepared
  cache. Missing images fail visibly; no synthetic fallback observations.

The first three attempts failed in plumbing (prefix binding, missing ROS Python
environment, and incomplete historical factory metadata). They are retained,
not counted as successful geometry or SLAM runs. v4 predates the final direct
image-source repair. The authoritative corrected pilot is v5.

## Actual v5 evidence

Directory: `timeline_gap_probe_v5/` under this planning directory.

| Recording | Proposed pairs | Both eyes bidirectionally accepted | Both rejected |
|---|---:|---:|---:|
| 20260929_take02 — failure case | 12 | 7 | 5 |
| 20260930_take03 — truncated but previously PASS | 12 | 11 | 1 |

Selected VINS endpoint mismatch is at most 2.384185791015625e-7 s for both.
The native factory baseline represented in these recordings is 18.083 mm.
The consumed-source before/after hash dictionaries are identical and the saved
script hash matches the reviewed implementation.

Accepted-pair inter-eye vector closure is 0.378–10.934 mm in take02 and
0.115–5.229 mm in take03. This is internal measurement consistency, **not ATE**.
Seven accepted pairs do not prove that the missing tail can be repaired to
10 mm. Neither proposed geometry nor failed observations have entered a solver.

Root verification: 11 new targeted tests PASS; source script compilation PASS.
Independent review: APPROVE after fixing direct DB3 image sourcing. Remaining
non-blocking provenance limitation: three imported diagnostic/config helper
code paths are not individually listed in the probe's input guard. Existing
producer/consumer/merger files remain unchanged.

## Next controlled step and current blocker

Prepare a pure bridge to the existing body-lever/shared-stereo policy, without
double-counting eyes or inventing zero motion. Compare an unchanged control and
the new source arm on the failure and successful counterexample before fast10.

The original B paired queue separately reported take02 `INCOMPLETE_VARIANTS`:
its control replay differs from the saved current best by 1.657e-5 m, above the
unchanged 1e-7 m identity gate. No scores are accepted for that replay. Diagnose
whether inputs or numerical convergence caused this before the new backend
pilot; do not widen the identity gate or discard the recording.

The maximum-ATE<=10 mm goal remains unachieved.
