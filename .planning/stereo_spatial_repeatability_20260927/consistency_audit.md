# Consistency audit: production-depth raw vs LM+raw-gyro gate

Date: 2026-09-27

Scope:
- Read-only audit of `reports/stereo_spatial_repeatability_20260927/production_depth_raw_ten_v2` and `reports/stereo_spatial_repeatability_20260927/production_depth_lm_gyro_gate_probe_ten_v1`.
- 166 uniformly sampled accepted production edges across ten cases.
- Original = `free` observation from production-depth raw replay. Candidate = `raw_gyro_gate` observation from LM+raw-gyro-gate probe.
- Confidence was recomputed with the same functional form as `scripts/fuse_mast3r_stereo_imu.py:1317-1345`.
- Local reference matters: for each sampled edge, reference scale was computed with the `local_stereo_scale_state` logic from `scripts/fuse_mast3r_stereo_imu.py:1423-1515` / graph use at `2079-2091`, using full merged production accepted observations. Candidate confidence is a bounded single-edge counterfactual: replace only that sampled edge with the candidate observation and recompute its local reference. This is not a full all-factor replay.

## Aggregate table

| method | n | cand accepted | conf median raw→cand | floor raw→cand | VINS diff median raw→cand mm | VINS improved | gyro residual median raw→cand deg | reverse closure median raw→cand mm | abs scale-vs-local-ref median raw→cand |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| LK | 99 | 99 | 0.694→0.703 | 1→1 | 1.208→1.104 | 49/99 | 0.193→0.184 | 0.263→0.215 | 4.83%→4.69% |
| SIFT | 67 | 67 | 0.261→0.287 | 3→4 | 4.585→3.826 | 28/67 | 0.530→0.496 | 2.188→1.794 | 2.79%→2.67% |
| all | 166 | 166 | 0.460→0.464 | 4→5 | 2.090→2.093 | 77/166 | 0.270→0.268 | 0.438→0.449 | 3.81%→3.78% |

Interpretation:
- LM+raw-gyro-gate gives small aggregate confidence gains, especially SIFT, but not a clean onboard consistency separator.
- Median VINS displacement improves for SIFT and LK separately, but the all-edge median is essentially unchanged because improvements and regressions are mixed.
- Reverse closure improves in median for LK and SIFT separately; the pooled median need not follow each group median. Do not infer changed method composition: the selected groups are fixed (99 LK / 67 SIFT), and closure availability can differ.
- Scale is relational (`stereo_motion_norm / MASt3R_motion_norm`); scale-vs-local-reference disagreement is not proof the stereo measurement is physically wrong.

## Fresh2 edge 590→610, actual full-candidate graph confidence

This edge is the clearest measurement-vs-weighting boundary.

Candidate `fresh2 / stereo_scale_bidirectional_report.json / 590→610 / SIFT`:
- local GT error, evaluation only: 2.744 mm.
- onboard VINS displacement disagreement: 13.568→3.152 mm.
- raw-gyro rotation residual: 2.465°→0.431°.
- reverse closure: 1.290 mm for candidate.
- Parent verification recomputed the exact full-candidate merged/filter/accepted path (1353 edges), not the sampled single-edge counterfactual. Its confidence remains floored:
  - local reference scale: 0.994531541
  - candidate scale: 1.566131959
  - scale vs local reference: +57.4743%
  - inlier ratio: 0.481
  - rotation quality: 0.924
  - scale quality: 0.0102322
  - bidirectional quality: 1.0
  - raw confidence: 0.00455, clipped to 0.05

Inference boundary:
- The edge became physically much more consistent with independent onboard proxies and evaluation reference.
- The graph still downweights it because its translation scale is incompatible with the local MASt3R-relative scale field.
- This is not simply "bad measurement"; it is a conflict between an apparently corrected local metric displacement and the relational local-scale model used by the graph.

## Counterexamples: VINS improves while graph-style confidence stays floored

All examples below are SIFT from `fresh2`.

| family | edge | VINS mm raw→cand | gyro deg raw→cand | reverse closure cand mm | scale vs local ref cand | cand confidence |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| bidirectional | 590→610 | 13.568→3.152 | 2.465→0.431 | 1.290 | +56.3% | 0.05 |
| long_hops | 1065→1105 | 12.590→7.660 | 1.346→0.939 | 6.458 | +30.0% | 0.05 |
| bidirectional | 1065→1090 | 6.433→5.753 | 0.588→0.562 | 2.565 | +40.2% | 0.05 |
| long_hops | 570→630 | 5.207→4.958 | 0.238→0.176 | 0.775 | +97.0% | 0.05 |

These are evidence of measurement/weighting tension, not evidence that the confidence formula is wrong. The scale term is relational to MASt3R/local stereo consensus.
These table rows use the bounded single-edge counterfactual reference described in Scope. In particular its +56.3% is not the actual full-candidate +57.4743% above; do not mix the two reference contexts.

## Case-level notes

- `fresh2` is the outlier for sampled local-reference scale disagreement: SIFT candidate median abs scale-vs-local-ref ≈30.0%, LK ≈27.8%; SIFT has 4 floored candidate samples, all with VINS improvement.
- `fresh4` does not show the same sampled SIFT pattern: SIFT candidate median abs scale-vs-local-ref ≈1.94%, confidence median delta +0.065; LK median abs scale-vs-local-ref is higher at ≈11.46%, but sampled LK shifts are known small and do not justify blind LK replay.
- Across all 166 records, raw-gyro/LM improves some independent onboard proxies but not enough to define a new gate or threshold.

## Stop condition / limitation

This audit uses existing sampled records and exact local-reference confidence computation. It does not:
- run a new graph/backend,
- sweep weights or thresholds,
- infer stereo truth from scale alone,
- reopen closed factor-weight/backend families.

The smallest useful artifact for future discussion is this table plus the per-edge counterexamples above: it shows where an independently improved local motion can remain low-confidence because the graph sees a local scale conflict.
