# Read-only camera-fixed foreground diagnostic

Current evidence: `self_track_diagnostic_v2.json`. The v1 report is retained
but invalidated by `self_track_diagnostic_v1_superseded.json`: the first
diagnostic incorrectly clamped a signed polygon edge denominator. Corrected
ray casting handles both winding directions, horizontal edges and boundary
points; six regression tests pass and an independent review approves v2.

This diagnostic uses the same uniform last fixed-pair B-half in all ten cases
(21 decoded raw frames); no mask, PnP, bundle adjustment, ground truth or
estimator change is applied. It hashes inputs, decoded frames and source code,
and preserves the raw tracker refusal for fresh2.

Fresh4 has **zero source-valid tracks inside the heuristic jaw polygon** in
this sampled window. Other accepted cases have only 1–7. This does not support
camera-fixed foreground contamination as the fresh4 root cause, and does not
justify changing an input mask. It is not a segmentation result, an all-frame
survey, a causal intervention, an uncertainty estimate, or an accuracy proof.

The full seam observation census continues unchanged. This negative diagnostic
does not alter its frozen source hashes, thresholds, calibration or td.
