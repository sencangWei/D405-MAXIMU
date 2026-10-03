# Tail-coverage falsifier: do not promote denser geometry

The user-directed development loop remains five frozen failure cases followed
by five frozen passing controls; full25 and new recordings are final checks.
This is a bounded two-record cause experiment before any further fast10 run.

## Hypothesis and single changed variable

After both saved MASt3R frontends end, twelve uniformly sampled 30-frame native
stereo pairs leave large holes. Test whether more overlapping observations
repair the remaining tail offset. Reuse the existing source probe with
`--max-pairs 40` on **the entire missing tail**, not GT-selected intervals.
No code, gates, solver parameters, learned factors, weights or correction caps
changed. VINS still supplies onboard direction/rotation consistency checks;
these source observations are not independent of VINS. No Tracker supervision.

Sources: `timeline_gap_overlap40_probe_v1/`. Both records are source READY.
Take02 has 19 both-eye accepted pairs, two RIGHT-only and 19 both rejected.
The passing take03 has 38 both-eye accepted and two both rejected. Source READY
does not mean precision PASS. Every rejected observation remains recorded.

## Actual fresh two-arm outcomes (mm)

| Recording | Arm | Mean | P95 | Maximum | Result |
|---|---|---:|---:|---:|---|
| Sep29 take02 | native control | 8.917 | 18.318 | 18.748 | FAIL |
| Sep29 take02 | +7 pairs, earlier pilot | 7.532 | 14.143 | 16.820 | FAIL |
| Sep29 take02 | +21 pairs, current pilot | 9.119 | 20.605 | 21.213 | FAIL |
| Sep30 take03 | native control | 3.005 | 5.956 | 9.869 | PASS |
| Sep30 take03 | +11 pairs, earlier pilot | 2.639 | 5.479 | 7.556 | PASS |
| Sep30 take03 | +38 pairs, current pilot | 3.041 | 5.994 | 8.086 | PASS |

All fresh arms retain 1143 samples and timestamp overlap 1.0. Their native
controls reproduce the previous two-arm controls exactly. Candidate and saved
arm estimate hashes match actual output CSVs; candidate supervision flags are
false. Actual current solver/scorer times: 21.493 s and 19.453 s, excluding
source extraction. Fresh source/bridge/trial/fast10/telemetry tests: 38 PASS.

Summary SHA256:
- `timeline_gap_overlap40_trial_take02_v1/summary.json`:
  `e4b2025483c8f25f30399eab4a386ef111e4f2d7662fc319955813a4b2ead2be`.
- `timeline_gap_overlap40_trial_take03_v1/summary.json`:
  `fee7b21de255c116d2f7c2971e65df3c53fd65f9893d811e9f907201f8d57944`.

## Decision and evidence boundary

Do not adopt the denser source policy or run full25 for it. This falsifies the
claim that simply increasing missing-tail observation density solves take02.
The graph already contains VINS relative edges over the full timeline: a lack
of absolute GT anchors is not itself a valid diagnosis or permitted remedy.
Next inspect source-vector bias versus onboard relative targets, boundary
coverage and actual solver residuals before proposing a different change.
Missing learned tail coverage remains real, but is not proven to be the sole
cause. The 10 mm maximum-ATE objective remains unmet.

Targeted read-only audit additionally found local target-minus-VINS discrepancies
near 799->829 (Y -14.48 mm), 963->993 (Z -10.31 mm), and 1112->1142
(Y +7.48 mm); these are internal differences, not measured truth errors.
There is no consistent wrong-way direction across the new targets. The trial
reports 764/766 stereo inliers, no downweighted queries and a 24.87 mm maximum
requested position correction. Learned local factors stop at node 530; the old
tail inherits a nearly constant correction through the VINS relative chain.
This supports a constraint-influence investigation, not proof of a specific
faulty row or a licence to retune closed weights/caps. Next measure influence
of source-internal inconsistent observation clusters before changing policy.
