# Long-loop displacement disagreement — diagnostic, not a repair

2026-09-29. Current production candidate remains **9/10 PASS**; the unchanged
fresh4 maximum-position ATE is **13.801442 mm** against the 10 mm gate.
No estimator, release configuration, calibration, timestamp, or production
selection rule was changed here. SteamVR was read only by the final scorer.

`../backend_match_probe_v1/{fresh4,fresh1,heldout1}_long_loop_displacement_v3.json`
checks saved accepted MASt3R retrieval matches against independent D405
left/right stereo depth. For each directed PnP, source-to-target camera motion
is converted to the target camera centre in the source frame (`-Rᵀt`); the
reverse PnP is averaged in that same frame. The original trajectory is scaled
with its recording's existing onboard stereo scale, and the local relative
displacement is expressed in the first camera frame. This is a UMI-only
geometric consistency check, **not** an ATE estimate or calibrated covariance.

| Case | Accepted long links / checked | Median visual–bidirectional-stereo disagreement | Median forward–reverse spread |
| --- | ---: | ---: | ---: |
| fresh4 (failed) | 7/10 | 11.15 mm | 3.81 mm |
| fresh1 (passed) | 6/10 | 3.52 mm | 1.33 mm |
| heldout1 (passed) | 16/20 | 4.08 mm | 1.24 mm |

At fresh4 909→1085 and 915→1085, the displacement disagreements are
**11.15 and 12.79 mm** after bidirectional averaging; the forward PnP
translation-vector errors are 11.37 and 12.71 mm. The first has only
−1.35 mm length difference but **7.63° direction difference**; visual-vs-PnP
relative rotation differs 1.90°. The coherent 909/915→1085/1095 residuals
decline to about 5 mm at 792→1135. However the first three stereo measurements
themselves have **3.81–6.35 mm forward–reverse spread**. They are evidence of
a local metric inconsistency, not accurate enough to declare each accepted
visual loop a false match. The controls and links are correlated, not
independent millimetre ground truth.

The predeclared diagnostic hypothesis
`../backend_match_probe_v1/fresh4_long_loop_causal_hypothesis.json` removed
exactly three accepted backend edges, 909→1085, 915→1085 and 909→1095,
while holding the frozen frontend configuration/model, all 1199 input frames,
formal td=−0.009109323 s, seam stereo factors and VINS input unchanged. The
replay log confirms exactly three `STEREO_EDGE_DROP` records. Both candidate
trajectories contain 1199 frames. The downstream frozen graph/complementary/
quality/smoothing commands all returned 0; the unchanged official scorer
returned 3 (accuracy gate failure). The fused trajectory was hashed before
external scoring in each chain manifest.

| Same 1142 samples, SE(3) ATE | Max | P95 | Mean | Rotation RMSE |
| --- | ---: | ---: | ---: | ---: |
| Unchanged production candidate | 13.801442 mm | 8.714560 mm | 5.554484 mm | 1.529006° |
| Three-edge deletion, unchanged onboard stereo/IMU scale selector | **15.518939 mm** | 10.347672 mm | 5.703527 mm | 1.394680° |
| Same deletion, onboard-orientation-only scale ablation | **13.919347 mm** | 9.937561 mm | 5.596408 mm | 1.384056° |

The original scale selector switched from the onboard-orientation branch
(0.489421 m/native unit) to the MASt3R-attitude branch
(0.473516 m/native unit) after edge removal. The third row deliberately
omits the stereo-informed selector and uses the same onboard-orientation
mode as the original (0.491770 m/native unit), so the scale-mode switch
cannot explain the whole negative result. That third row is **only a causal
ablation**, not a proposed production scale policy. Neither branch reaches
10 mm, and P95/mean worsen. The edge deletion must **not** be promoted.

Next structural question: can a *well-calibrated* long-baseline metric visual
constraint be added at the MASt3R frontend or graph, with stereo uncertainty
and correlation modelled, rather than deleting a valuable loop? The present
18 mm D405 baseline and 3.8–6.4 mm forward–reverse disagreement at these
specific links preclude treating their PnP displacement as a precise 1 mm
truth. Require a source-only admission/uncertainty rule and multiple-case
validation before any all-ten accuracy claim; do not tune using SteamVR.
