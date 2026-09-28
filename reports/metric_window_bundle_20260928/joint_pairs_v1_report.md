# Adjacent-window experiment v1 — not promoted

Fixed five time-stratified pairs on each of the same ten recordings. Two
20-frame windows share one boundary, 41 raw images are continuously tracked,
nine poses enter the joint bundle. Source, pixel/gyro equations, formal td,
noise settings and calibration remain frozen. No reference was used by the
estimator or to select cases/windows. UMI census completed before evaluation.

## Results

Joint accepted 39/50 pairs (78/100 endpoints); matched independent controls
accepted 91/100 endpoints. Missing cross-window training geometry (0–3 shared
points) is a major refusal reason. Fresh4 pair5 has only one shared training
landmark. Primary raw failures and optimization failures remain explicit.

After freeze, the existing official body reference and unchanged body-to-leftIR
lever/time mapping scored local camera-frame displacements, NOT full-trajectory
ATE. Same78 mutually accepted endpoints:

| Local displacement error, mm | Independent | Joint |
| --- | ---: | ---: |
| Median | 0.553 | 0.767 |
| P95 | 3.160 | 3.490 |
| Maximum | 6.218 | 7.171 |

Joint improved33/78. Reference uncertainty remains; these numbers do not replace
the existing fusion trajectory's9/10 PASS and fresh4 max14.016mm.

Joint withheld prediction also contains large errors (e.g. dev2pair4≈97.8px).
Source-depth prediction over nine nodes has a different horizon/sample set
from independent five-node diagnostics; do not treat those as identically
distributed RMSE or calibrated confidence.

## Decision

Independent review: do not promote into graph/production. A larger bundle does
not by itself fix poor physical landmark continuity. Next fixed structural
experiment seeds distinct stereo landmarks at the seam and tracks the SAME
physical IDs both backwards and forwards, supplementing both windows. No
weight/gate sweep, pointwise reference correction or failed-window fallback.

Frozen evidence: joint_pairs_ten_v1/summary.json, independent_summary.json,
local_joint_evaluation.json and local_independent_evaluation.json. The
independent_view/summary.json is a byte-identical naming adapter for the
existing evaluation-only CLI, not a changed estimator result.
