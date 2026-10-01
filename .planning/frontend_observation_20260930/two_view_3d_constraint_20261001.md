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
coverage, not metric accuracy. Frozen downstream fusion and independent
SteamVR scoring are still pending; no 10 mm claim follows from this result.
