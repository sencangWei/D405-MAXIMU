# Full seam geometry graph control — target not met

2026-09-28. Research-only, **no production promotion**. All ten cases and all
three variants completed. The official SE(3), no-scale evaluation contract,
external-reference bytes, time alignment, extrinsics and thresholds were kept
unchanged. GT was used only after all 30 graph outputs had frozen.

## Full trajectory maximum ATE (mm)

| Case | Replayed baseline | Joint seam geometry | Independent control |
| --- | ---: | ---: | ---: |
| fresh1 | 7.809 | 8.583 | 8.591 |
| dev1 | 6.458 | 6.438 | 6.338 |
| dev2 | 8.271 | 8.247 | 7.864 |
| heldout1 | 5.921 | 6.027 | 6.075 |
| heldout2 | 5.974 | 5.989 | 6.054 |
| heldout3 | 6.831 | 6.687 | 6.595 |
| heldout4 | 8.913 | 8.907 | 8.905 |
| fresh2 | 11.323 | 8.265 | 8.154 |
| fresh3 | 6.310 | 6.295 | 6.253 |
| fresh4 | 15.333 | **13.801** | 13.961 |
| Official PASS count | 8/10 | **9/10** | 9/10 |

The ten baseline maximum errors replayed with exactly zero difference from
the frozen prior baseline. Neither experimental variant loses any of the eight
baseline passes, but several individual maxima/means worsen. Joint is not
uniformly better than independent. The prior full metric-window experiment
was also 9/10 PASS, with fresh4 maximum 14.016499 mm; reducing that to 13.801442
mm is **not** a 10 mm solution or proof of future-recording reliability.

## Remaining failure, not isolated one-frame spikes

Joint fresh4: mean 5.554484, median 5.775342, RMSE 5.883745, P95 8.714560,
minimum 1.066624, maximum 13.801442 mm. 1116/1142 (97.723292%) evaluated poses
are within 10 mm; the sole official failure is `ate_translation_max_over_limit`.

Using the exact official rigid alignment, matched timestamps and final pose
files, the remaining over-limit block is consecutive scored indices 996–1021
(26 frames), elapsed 33.197901–34.031315 s. Maximum is scored index 1014, with
estimate-minus-reference vector [6.697527, -1.963217, -11.906666] mm in the
reference world axes. This is a continuous mostly-Z position offset, not an
isolated bad frame appropriate for neighbor interpolation.

Baseline fresh4 had 63 over-limit samples in blocks 759–772, 780–787 and
987–1027. Joint leaves only the final 26-frame block; independent leaves 31
samples at 992–1022. These error chunks are evaluation-only diagnostics and
must never choose estimator windows, confidence, admission or replacement.

## Integrity and limits

- All 30 graph, complementary, input-quality and smoothing stages return 0.
  Official scoring returns 0 on 26 attempts and 3 on four threshold failures
  (baseline fresh2/fresh4, joint fresh4, independent fresh4). Those failures
  and their outputs are retained; they are not execution crashes or passes.
- Graph output keeps all 1199 poses (dev2: 1200). The unchanged downstream
  VINS-overlap contract produces/evaluates 1142 poses (dev2: 1143), identical
  counts/timestamps to the baseline. No new trajectory crop or deletion.
- New joint input: 506 endpoints / 253 accepted pair groups. Independent:
  540 endpoints, including six honest partial pairs. Two endpoints from a
  joint solve share observations and are **not independent information**.
- Existing 4 mm graph penalty and confidence 1 are engineering regularization,
  **not calibrated covariance**. This experiment is not covariance-correct
  integration and does not authorize production selection or deployment.
- No production-source change, MASt3R model training, frontend rerun, GT
  supervision, case-specific candidate mixing, threshold change or closed-family
  parameter sweep. Local displacement improvements are not full-trajectory ATE.

## Next bounded structural test

Read-only review confirms that the local BA optimizes nine centers/rotations
and shared landmarks, but the current graph interface serializes only two
20-frame endpoint displacements. Intermediate geometry and landmark-induced
coupling are discarded. Endpoint factors can be satisfied with an interior bow.
This is an information-loss fact, **not proof that it causes fresh4's error**.

Proceed only with an isolated grouped nine-center nuisance-projected sensitivity
prototype and synthetic interior-bow test. Marginalize landmarks, rotations and
bias; expose rank/null directions, preserve one relative first-camera gauge and
correlation. Do not emit more independent edges with the same penalty: that
would be a density/weight change. A zero-centered local sensitivity form also
does not preserve the profiled residual/gradient or prove nonlinear accuracy;
it cannot be promoted as an exact Schur measurement or calibrated covariance.
Any later real-data survey must cover all ten uniformly, freeze before GT,
retain refusals and precede a separately reviewed graph integration contract.

Evidence: [comparison](seam_graph_full_ten_v1/comparison.json),
[all-stage records](seam_graph_full_ten_v1/batch_status.json),
[fresh4 official precision](seam_graph_full_ten_v1/joint/fresh4/official_score/precision.json).
