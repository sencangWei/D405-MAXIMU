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

The same saved stereo matches and **same scalar scale** were compared to
MASt3R's `trajectory_online_frames.csv`, which records each pose when that
frame was tracked, before later map corrections. The final trajectory is
substantially closer to stereo, especially in the failed recording:

| Case | Median online disagreement | Median final disagreement | Median paired reduction |
| --- | ---: | ---: | ---: |
| fresh4 | 16.63 mm | 11.15 mm | 9.46 mm |
| fresh1 | 5.80 mm | 3.52 mm | 3.38 mm |
| heldout1 | 7.11 mm | 4.08 mm | 3.96 mm |

On fresh4 909→1085, 915→1085 and 792→1135, the online-to-final reductions
are **16.83, 13.30 and 17.83 mm**. This argues against blaming those final
loop corrections for *creating* the discrepancy: they mostly repair an
earlier online-trajectory inconsistency but leave several millimetres.
“Online” already includes backend corrections made up to that frame; it is
not a tracker-only ground truth. This comparison localizes the residual
upstream of the *later* global corrections, not uniquely to the neural model,
image quality, or a specific tracking step.

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

## 2026-09-29: local backend-scale check (diagnostic only)

The saved 995→1074 backend link is a useful counterexample to a blanket
"later optimization always repairs the visual track" claim: its bidirectional
stereo disagreement grows from 4.30 mm in the online track to 10.12 mm in the
final track. Removing *only* that link, with 1199 input frames and the frozen
downstream commands unchanged, changes the fused trajectory by at most
0.426 mm. Official fresh4 max/P95/mean become **13.756/8.645/5.525 mm**
(1142 samples), versus 13.801/8.715/5.554 mm; the 10 mm gate still fails.
The candidate and predeclared pair are under `../causal_995_1074_v1/` and
`../backend_match_probe_v1/fresh4_995_1074_causal_hypothesis.json`.

Adding one independent D405 stereo relative-displacement observation at the
same 995→1074 link to the graph (without deleting the visual link) moves the
fused trajectory at most 0.073 mm. The unchanged official scorer reports
max/P95/mean **13.826/8.715/5.556 mm**. This likewise fails; its isolated
one-factor result cannot justify a production change. The report generator
uses the graph's native stereo-report schema and marks the original PnP
source explicitly; see `../metric_loop_factor_probe_v1/fresh4_current_chain_v2/`.

Independent onboard chord lengths sharpen the localization. With the existing
body-to-camera lever arm applied to VINS, fresh4 995→1074 measures 117.93 mm
in final MASt3R, 127.88 mm by D405 stereo PnP, and 132.87 mm by VINS.
For the neighbouring 1057→1074 chord, final MASt3R is 149.48 mm versus
165.78 mm VINS; the online MASt3R chord was 162.12 mm. The event trace shows
successive backend solves around raw frames 1074–1135 shortening that local
chord, though other long-loop discrepancies improve. This is evidence of a
*local cumulative backend shrink*, not proof that one edge or one model alone
is at fault. D405 PnP/VINS are independent onboard comparators, not exact GT,
and their own 995→1074 lengths differ by about 5 mm. Source-only chord
reports for failed fresh4 and passed fresh1/heldout1 are in
`../backend_match_probe_v1/*_three_metric_chords_v1.json`.

The existing opt-in frontend configuration with D405 metric pointmaps and
VINS translation/backend priors is now being replayed on fresh4. It is a
*candidate only*: its new visual trajectory requires newly computed stereo
reports before any downstream or external precision comparison. None of the
tests above changes the current 9/10 production candidate.

### Full rescue replay and channel-composition falsification

The opt-in rescue frontend replayed deterministically: its 1199-frame CSV is
byte-identical across two runs (SHA256 `60eada38aa78c54c9437a85c40c336c3c4a2f656100218f89bd792a1112b8b0d`).
Its fresh D405 short/medium/dense/multisecond scales are 0.971588, 0.962194,
0.968805, and 0.959169 m/unit, and IMU scale is 0.989192; all five onboard
scale reports PASS. Yet the existing graph rejects this candidate on
`visual_gyro_rotation_inconsistent`: visual/gyro rotation P95 after fusion is
**1.388°**, above the unchanged 1° gate (old frontend **0.290°**). The
unmodified full rescue therefore has **no legitimate fused precision score**.

A separate, explicitly diagnostic channel-composition experiment rigidly
registered the two visual frontends at the first common frame, preserved
rescue camera positions and baseline camera orientations at all 1199 exact
timestamps, and recomputed IMU scale. This uses no external reference. The
frontends' relative attitude differs by 12.409° at P95, so the composition
tests a real coordinate/kinematic inconsistency, not a small adjustment. It
passes the existing graph and input-quality gates, but the **unchanged
post-only official** fresh4 SE(3) score is worse at the exact same 1142 samples:

| Candidate | Mean | P95 | Max | Max-error raw frame |
| --- | ---: | ---: | ---: | ---: |
| Old plain fusion | 6.338 mm | 11.314 mm | 16.181 mm | 1071 |
| Rescue-position / old-attitude plain fusion | 4.897 mm | 9.532 mm | **17.613 mm** | 1071 |
| Incumbent paired-seam fusion (different downstream controls) | 5.554 mm | 8.715 mm | 13.801 mm | 1071 |

The scored estimate SHA256 `b760131a6a5c9dcfb09fc35cc33eb0bb387f6757aaa23d0af559ca52ebea548c`
was unchanged before/after external scoring. The plain-fusion rows compare
like with like; the paired-seam row is context, **not** a controlled comparison
against this hybrid. An attempted paired-seam replay correctly refused to
mix the new source trajectory with controls bound to the old source report
(`primary stereo report trajectory differs from controls source`); that
provenance guard was not bypassed.

At 1057→1074, external reference is used **only after** the estimate was
frozen to diagnose the failed candidate. In the first camera's local frame,
the reference chord is 160.39 mm; baseline visual is 149.48 mm with 12.36 mm
vector error, while rescue visual is 160.30 mm with **25.73 mm vector error**.
The length is nearly correct but its direction is not. Onboard VINS gives
165.78 mm length and 9.84 mm vector error on that chord. Thus *length-only*
VINS/stereo comparisons are insufficient and blindly combining independently
optimized position/attitude tracks is not a valid repair. The new controlled
ablation removes only the rescue frontend's absolute backend position prior;
it retains the same stereo pointmap and short-term VINS translation inputs.

The first ablation (`../vins_backend_metric_probe_v1/fresh4/rescue_no_backend_position_frontend/`)
disables the backend *position plus log-scale factor together* (the code
enables both only when `vins_backend_position_sigma_m > 0`), while retaining
stereo pointmap and short-term VINS translation constraints. Its visual/VINS
attitude difference P95 returns to **3.533°** (baseline 3.523°; full rescue
8.098°). Newly computed short/medium/dense/multisecond stereo scales are
**1.040106 / 1.015550 / 1.052841 / 1.003520**; each stereo report, IMU
scale report, graph rotation gate (0.267° after P95), and input-quality gate
PASS. This strongly localizes the attitude degradation to the coupled backend
metric factor, but does not separate its position and log-scale subterms.

After the source-only estimate was frozen, the unchanged 1142-sample official
plain-fusion score is mean/P95/max **5.806/10.771/14.893 mm**, FAIL. The
estimated fused CSV SHA256 `8ad9d72f4bfa06e271817895b0fc43ac13fd087a11e0513ff2dc06c19f1a60ce`
is identical before and after scoring. It improves old *plain* max 16.181 mm
but misses the 10 mm gate and is worse than the current paired-seam max
13.801 mm. No generalization or promotion is claimed from this one failed
case. A second, already predeclared isolation retains the 4 mm backend
position factor and makes only the log-scale residual negligible, to identify
which subterm distorts attitude before any further full stereo work.
