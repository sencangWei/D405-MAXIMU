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

## Reviewed candidate bridge and closed native batch

`ego_vio/vio/timeline_gap_stereo_candidates.py` now converts the actual full
D405-tail reports into shared body_i rows using the existing reference binding,
same-eye deduplication, max-confidence/equal-tie policy and physical lever
transform. It calls the existing fusion confidence function, not a cloned
formula. It rejects GT/Tracker/promotion/GPU flags and reports without direct
DB3-image lineage. Neither the original timeline nor original rows are cropped.
The helper returns fully transformed rows, never zero placeholder motions.

Independent review: APPROVE after provenance and confidence fixes. Root fresh
validation: 32 targeted tests PASS (6 bridge, 11 source probe, 6 fast10, 9 solver
telemetry); compilation PASS. This proves the adapter contract, **not ATE**.

The original B native paired batch is now terminal and the strict full25 merge
is `COMPLETED_WITH_FAILURES`: 25 retained records, 22 scored, 2 technical control
replay failures, 1 preparation/unobservable failure. Of the 22 scored records,
the original control has 17 full precision PASS and the independent-native
recovery arm has 19. Sep29 take04 improves max 10.902476 -> 6.858090 mm and
Sep29 take07 becomes full PASS (max 9.863734 -> 8.677689 mm; its prior failure
also included a rotation gate). Some previous passes regress in metrics while
remaining PASS, e.g. Sep29 take09 max 7.997074 -> 9.054003 mm. The contaminated
Sep29 take03 reference is retained, not removed to manufacture acceptance.

Take02 strict replay failure is 16.574913 micrometres; a second technical replay
failure at Sep30 take03 is 66.14 micrometres. Take02 recorded stereo rows,
learned factors, timeline, rotations and all common input/source hashes match
the frozen comparator. The remaining difference is in the newly solved position
output; numerical sensitivity is a supported hypothesis, not yet a measured
LSQR-cause proof. The existing 1e-7 m identity guard is unchanged.

The saved continuation has merged all25 and started the fixed failure-first
fast10 run from cached native sources. Fast10 includes all five development
failures and five fixed passing controls. It is not a blind validation set.

Next controlled trial uses two freshly solved arms (cached native recovery
alone, then identical inputs plus full-timeline gap observations). The frozen
currentbest is explicitly a cached comparator, not a claimed fresh control
replay. No existing guard, solver setting or live corpus helper is modified.
