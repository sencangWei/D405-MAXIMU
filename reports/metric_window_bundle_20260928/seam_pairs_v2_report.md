# Seam-born stereo replenishment v2 — local improvement, not SLAM acceptance

Same ten recordings and fixed five adjacent pairs per recording. Existing
primary tracker, solver, factory calibration, formal td and scalar noise stay
unchanged. New seam stereo features are detected outside a7px mask of ALL
existing seam observations, then SAME physical IDs tracked backwards and
forwards over every raw image. New points use fixed source-IDmod5 heldouts.
Their3D initialization uses A INITIAL train-PnP seam pose, not any optimized
pose, learned trajectory or external reference. Combined nine poses are
reinitialized only from training pixels before the original bundle solver.

No reference enters tracking, initialization, admission, solving or selection.
All ten UMI controls finished and froze before external local evaluation.

## Frozen comparisons

Accepted endpoints: v1=78/100, v2=84/100, independent=91/100. Input/calibration/
decoded image hashes and independent initial/optimized endpoints are EXACTLY
unchanged across all ten cases; comparison rejects any difference. All refused
windows remain explicit, never filled from a successful baseline.

| Local displacement metric, mm | v1 joint (same78) | v2 (same78) | Independent (same84) | v2 (same84) |
| --- | ---: | ---: | ---: | ---: |
| Median | 0.767 | 0.593 | 0.815 | 0.743 |
| P95 | 3.490 | 3.203 | 4.342 | 3.417 |
| Maximum | 7.171 | 4.520 | 7.182 | 6.028 |

V2 improves48/78 against v1 and42/84 against independent. Counts differ between
the two comparisons because their mutually accepted windows differ; no failed
window is silently removed from coverage reporting. Independent naming views
are byte-identical data copies required by the existing scoring CLI, not edits
to estimator outputs.

Fresh4 last pair (raw1058..1098) shared training support improved1→9 withrank3;
19 new sampled cross-seam points added (14train/5heldout). It now passes the
original support/consistency gates. Its two local errors peak6.028mm, compared
with independent7.182mm. This is NOT evidence that the trajectory ATE peak was
fixed: the existing best frozen full-trajectory result still has fresh4
max14.016mm and9/10 recordings PASS.

## Remaining risk / next experiment

Still8/50 pairs fail:2geometry,2model-consistency,1solve,3primaryraw failures.
Heldout source-depth predictions are diagnostic, not admission/confidence.
Pairs emit correlated endpoints; no calibrated covariance is claimed. Graph
promotion is not approved. Independent review approves only the next uniform
paired40/stride40/all-ten broader UMI coverage, with matched independent
controls and explicit uncovered-tail policy, before any new graph contract.

Evidence: seam_pairs_ten_v2/summary.json, independent_summary.json,
local_joint_evaluation.json, comparison.json and comparison_to_independent.json.
Fresh expanded287testsPASS; code was backed to owned sencang branch and
restored from remote, with278then-current selected testsPASS.
