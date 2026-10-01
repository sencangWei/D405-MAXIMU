# Two-view 3D constraint: observation before integration

This is an **offline diagnostic**, not a new production SLAM result. It uses only
UMI left/right D405 infrared, the factory stereo calibration, and recorded IMU
rotation. Lighthouse and robot poses are reserved for final scoring.

The MASt3R input order is asymmetric. Reversing a weak pair raises its raw 3D
match fraction, but does not by itself establish a correct pose:

| Pair | Current→previous 3D gate | Reverse 3D gate | Reverse model pose vs independent stereo |
| --- | ---: | ---: | ---: |
| take3 807/806 | 2.69% | 43.22% | 22.27 mm / 1.61° |
| take1 959/958 | 4.57% | 25.62% | model PnP rejected (28.6% inliers) |

On take3 807/806, 933 reciprocal descriptor pairs have independent stereo
3D residual P50 3.26 mm / P95 16.26 mm; the two MASt3R pointmaps on those
same pairs have P50 12.83 mm / P95 20.38 mm. The stereo PnP has 1081 inliers
and agrees with the correctly composed IMU rotation to 0.070°. On take1
959/958, the corresponding stereo/model residual P50 are 4.38/18.28 mm,
while stereo-to-IMU rotation differs by 0.071°.

**Counterexamples matter.** Healthy take2 807/806 also has model pair P50
14.12 mm against stereo 2.39 mm, yet raw 3D match fraction remains 29.62%.
Across the 10-pair control censuses, independent stereo PnP sometimes rejects
otherwise high-match pairs. Neither raw model residual nor stereo PnP alone is
a validated failure detector. Fitting the weak take3 pointmap to stereo depth
at fixed IMU rotation still leaves 12.79–14.57 mm held-out P50 residual; a
single-frame depth translation is not a safe repair.

The existing backend-edge probe found a separate concrete case: in failing
fresh4, the accepted consecutive-keyframe edge 1057→1074 has only 779/1933
(40.3%) reverse stereo-PnP inliers, whereas its forward direction has
1433/2017 (71.0%). All 142 inspected short edges of passing fresh1 pass the
existing stereo check. See
`reports/metric_window_bundle_20260928/backend_match_probe_v1/README.md`.
It does not prove that edge causes the final ATE peak. In fact this exact
backend direction has already been causally tested: dropping four
stereo-rejected short edges worsened fresh4 max ATE 13.801→13.942 mm; keeping
the edges but masking stereo-PnP reprojection outliers changed 13.801→13.817
mm, essentially unchanged. See
`reports/metric_window_bundle_20260928/causal_drop_v1/README.md`. **Do not
repeat or promote the backend edge-mask experiment.**

The still-open direction is upstream: measure the MASt3R pairwise pointmap
and keyframe-state transition around weak frames against passing controls,
then use independent two-view stereo and IMU as constraints on the generated
3D structure itself. A pairwise threshold, reverse-order pose, single-frame
depth translation, or post-hoc position edit has not qualified. Success
requires improved fused ATE (including maximum) on failing cases without
regressions on passing controls, then all-ten validation. Matching rate,
internal residual, or one-frame pose agreement alone do not qualify.

## Spatial pointmap candidate (2026-10-01, default off)

`MAST3R_SPATIAL_POINTMAP_RECOVERY=1` now permits a **weak-frame-only**
experiment. It keeps the learned pointmaps and descriptors. Only when the
original 3D match fraction is below the existing 5% tracking gate, reciprocal
MASt3R descriptors establish a D405 two-view metric pose; that pose must pass
stereo reprojection/depth, robust model-to-metre scale, and UMI IMU rotation
checks. A smooth 3D residual field is then fit in alternating 16-pixel image
cells and tested in the complementary cells before the original 3D matcher
is retried. The new candidate is never selected by Lighthouse or robot data.

Pairwise diagnostics on frozen 09-30 recordings:

| Pair | Original 3D match | Spatial candidate | Source/target held-out depth P50 after | Stereo/IMU rotation |
| --- | ---: | ---: | ---: | ---: |
| take3 807→806 | 2.69% | 64.59% | 3.64 / 3.33 mm | 0.070° |
| take3 808→806 | 2.08% | 56.76% | 4.05 / 3.35 mm | 0.042° |
| take1 959→958 | 4.57% | 58.47% | 5.11 / 5.52 mm | 0.071° |
| passing take2 807→806 | 29.62% | 67.96% in isolated diagnostic | 5.13 / 4.83 mm | 0.118° |

The passing take2 row is **not** a candidate intervention: its original
match rate is above the 5% gate, so the optional frontend leaves it alone.
The first full take3 replay used an exploratory 30 mm whole-image holdout
P95 bound. It recovered raw frame 807 but rejected 808 (source P95 32.97 mm
despite a 4.05 mm P50 and independently good two-view stereo); final pose
coverage was only **808/1199**. This is a negative result, not an accuracy
improvement. The next revision evaluates the *matched* 3D geometry rather
than requiring every depth-visible region to satisfy a hard P95, while still
requiring spatially held-out median improvement in both images. Its first
replay (`v2`) exposed an implementation unit bug: the matched-pair distance
was computed in model units but reported as metres. It rejected a true
~1.25 mm pair as 18.69 mm. Both `v2` runs were terminated and are **invalid**;
the conversion is now covered by a nonzero-distance unit test. Corrected
`v3` full frontends completed on take3 and take1: both exported **1199/1199
source poses**. Take3 recovered frames 807–810 with 56.76–64.59% accepted
3D matches; take1 recovered frame 959 with 58.47%. This is only tracking
coverage, not metric accuracy.

Frozen downstream validation **rejects this candidate**:

| 09-30 take | Descriptor-only control | Spatial pointmap candidate | Decision |
| --- | --- | --- | --- |
| take1 | SteamVR mean/P95/max 5.282/9.891/10.963 mm | 5.284/9.879/10.964 mm, same 1143 stamps | No meaningful accuracy gain; both fail max and rotation gates |
| take3 | Long-hop stereo scale dispersion 1.182 (fails) | 0.535 (still fails); short-hop dispersion 0.288→0.393 | No qualified fusion output |

The take3 candidate and control have identical 103 keyframe image IDs, but
their online positions first differ by more than 0.01 model units at frame
1019 and diverge further near 1100. The weak-frame correction changed a
later trajectory state; improved local matching did **not** preserve stable
global scale. This does not yet identify whether a tracking transition or
graph factor causes the divergence. Do not enable the candidate or relax the
multisecond scale gate. Inspect the frame-1005 scale transition and the accepted
keyframe/loop constraints before another algorithm edit; any replacement must
pass independent take1 and take3 end-to-end and then a passing control.

Additional localization after that rejection: the diagnostic online Sim(3)
scale is nearly identical through frame 1004 (both ~0.128) and separates
abruptly at frame 1005 (control 0.11954, candidate 0.16575); the position
difference grows gradually afterward. The *same raw pair* 1005→1004 has a
34.46% MASt3R 3D match rate, so it does not trigger the weak-frame gate,
but a metric pose from those learned points is rejected at 36.3% PnP
inliers. Independent D405 two-view stereo has 1228 PnP inliers, 1.50 mm
paired-depth median / 11.14 mm P95, and agrees with IMU rotation to 0.115°;
the learned-point fit differs from stereo translation by ~16.19 mm. This
supports a 3D geometry / unconstrained Sim(3) branch issue even where raw
MASt3R match fraction is high. It is *not* evidence that the 1005 stereo
pose is externally accurate to 1.5 mm. A healthy-control counterexample
exists: take2 807→806 has 29.62% raw match and ~20 mm learned/stereo
anchor disagreement, but its stereo pair P95 is 25.13 mm. A single
learned-vs-stereo threshold would also fire on passing data. Existing
hard-scale, soft-scale, keyframe-PnP, and position-window prototypes were
rejected in `.planning/five_take_fusion_optimization_20260924/findings.md`;
do not retread them. The next candidate needs persistent multi-frame 3D
constraints that preserve keyframe gauge, not a per-pair match-rate rescue.

The 1005 anomaly is part of a *block*: take3 pairs 1001→1000 through
1008→1007 all have raw 3D match rates **30.6–42.3%**, yet stereo PnP has
**1199–1249 inliers**, stereo paired-depth P95 is **9.48–12.88 mm**, and
IMU rotation disagreement is **0.049–0.206°**. Their learned-point
translation anchors differ from stereo by **13.89–16.61 mm**; 7 of 8
learned-point PnP fits fail the existing 40% inlier gate. A same-index
take1 control has stereo pair P95 **25.9–27.4 mm** on the observable
pairs and learned/stereo anchor difference only **5.57–6.88 mm**;
take2's eight stereo estimates are all rejected for inconsistent stereo
geometry while its raw MASt3R match is **57.5–75.4%**. This is a possible
*multi-frame onboard observability gate*, not yet an accuracy improvement
or license to substitute stereo PnP poses directly. The source of the
Sim(3) branch jump still needs a causal intervention that does not create
the keyframe seam failures documented in the earlier v4 experiments.

## Calibrated graph replay and failed anchor-isolation tests (2026-10-02)

The first causal branch is the **keyframe-1004 backend solve**, not the
1005 pairwise matcher. The online trajectory is recorded before each backend
update, so the changed 1004 graph anchor first appears in the online stream
at 1005. Exact, default-off snapshots of the calibrated graph contain the
same 80 keyframe IDs, camera matrix, 448 directed edges and all five
correspondence tensors in the descriptor-only control and spatial candidate.
The current graph scale changes from 0.127870 to 0.117152 in the control,
versus 0.127934 to 0.162434 with spatial recovery. These are dimensionless
Sim(3) scales, **not position errors in millimetres**.

Crossing the saved pose state and pointmap geometry under the *same solver*
gives final current-keyframe scales: control/control 0.117152,
control/spatial 0.143252, spatial/control 0.134589, spatial/spatial
0.162434. The recovered weak pair had rewritten old keyframe 806's
persistent 3D pointmap: its mean per-pixel 3D change is 0.291 model units;
the next-largest keyframe change is only 0.000067. Replacing **only**
keyframe 806's pointmap in the spatial snapshot moves the result from
0.162434 to 0.146588. Replacing only its confidence has no effect.
This is evidence for an upstream 3D-map/graph-state interaction, not proof
that either snapshot's absolute scale is correct. The replay tool is
`MASt3R-SLAM/scripts/replay_graph_snapshot.py`; it cannot generate a
production trajectory and is not externally scored.

Two default-off repairs were tested on all 1199 take3 input frames. Both
retain tracking coverage and avoid the abrupt 1004/1005 scale branch:

| Candidate | Short-hop dispersion | 8/12/16-hop | 10 Hz | 0.8–1.6 s | Decision |
| --- | ---: | ---: | ---: | ---: | --- |
| Original spatial pointmap recovery | 0.393 PASS | 0.376 PASS | 0.430 PASS | 0.535 FAIL | Rejected |
| Skip persistent pointmap write-back on weak recovery | 0.597 FAIL | 0.610 FAIL | 0.636 FAIL | 1.018 FAIL | Rejected |
| Separate temporary local tracking map from persistent graph map | 0.598 FAIL | 0.610 FAIL | 0.635 FAIL | 1.018 FAIL | Rejected |

The local-map separation avoids mutating the old graph anchor during a weak
pair but does **not** preserve multi-span metric consistency. It is not a
qualified accuracy fix and must remain disabled. No Lighthouse/robot data
entered these SLAM candidates or their internal scale gates. Because all
four independent stereo gates reject the two isolation variants, running
their downstream fusion or choosing one by external ATE would be misleading.
The next change must put a persistent, independently observable 3D/metric
constraint into the *keyframe graph* while preserving the useful short-hop
geometry; further position smoothing or a match-rate threshold is not a
substitute.

### Depth-support localization and global log-depth control

At frame 806, independent D405 stereo depth is valid at 73,083 / 147,456
pointmap pixels. Compared with the descriptor-only replay, the spatial
candidate changes the saved 3D pointmap by >0.5 model units at only 1.33%
of depth-valid pixels but 21.88% of depth-invalid pixels. The mean change
is 0.103 versus 0.476 model units, respectively. Thus the large map change
is concentrated where the stereo correction has no local depth support;
the current `spatial_depth_correction` extrapolates its cell residual field
to the whole image. On valid stereo pixels, fitting each saved map to depth
with its own single median scale gives absolute-depth P50/P95 of
2.94/23.80 mm (control) versus 2.75/18.76 mm (spatial). This does not
validate the extrapolated regions or turn either map into ground truth.

The graph is too nonlinear for a simple pixel/distance threshold to be a
safe repair: in the frozen frame-1004 snapshot, replacing spatial map-806
pixels farther than 2, 4, 8, 16 or 32 pixels from valid stereo depth with
their control values yields current Sim(3) scales 0.1353, 0.1624, 0.0498,
0.1032 and 0.1424. The response is non-monotonic and sometimes extreme;
these hybrid snapshots are causal probes, not candidate trajectories.

The untouched global calibrated graph uses `local_opt.sigma_depth=10`
(learned log-depth, not D405 depth). On the frame-1004 snapshot, changing
only this solver value to 3 made the two branches nearly equal, but values
1 and 0.3 changed them differently. A full 1199-frame take3 replay with
only `local_opt.sigma_depth=3` and the same spatial recovery proved the
snapshot inference did not generalize: all poses were produced, the online
scale near frame 1005 remained ~0.166, short-hop stereo PASS, and the
0.8–1.6 s stereo dispersion only moved 0.535→0.525, still FAIL. The exact
experimental config is `graph_depth_sigma3_20261002.yaml`; it is **not**
a production config. No external reference was used to choose or evaluate
this internal candidate, and no downstream fusion/ATE should be claimed.
