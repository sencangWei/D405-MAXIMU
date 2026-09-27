# Two fresh recordings — official SteamVR fusion validation

This is actual fusion SLAM scoring, not AprilGrid calibration residuals.
The identical frozen `fusion-guarded` workflow processes both recordings.
Tracker is consumed only after fusion finishes; no trajectory supervision,
per-take parameter adjustment or GT-based candidate selection.

| Recording | Mean mm | Median mm | RMSE mm | P95 mm | Max mm | <=10mm | Status |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| take1 |2.922|2.673|3.233|5.611|7.018|100%|PASS|
| take2 |—|—|—|—|—|—|FAIL_INPUT: local visual scale instability|

take1 uses the baseline frontend, not the rescue candidate. Rotation RMSE
0.963 degrees. Scoring retains1143 emitted poses;1199 source camera frames
were recorded. The100%within10mm statement is for evaluated output poses,
not a claim that startup/initialization produced poses for every camera frame.
SE3 rigid alignment only, no fitted scale. The estimate SHA256 before/after
scoring is unchanged. No code/model/configuration tuning was made for either
recording, so this is fresh validation of the existing algorithm/reference
combination, not evidence of a new algorithm improvement or a universal bound.

- [take1 report](run_1340/take1/official_score/precision.md)
- [take1 plot](run_1340/take1/official_score/precision.png)
- [take1 machine-readable score](run_1340/take1/official_score/precision.json)
- [take1 selection provenance](run_1340/take1/fusion/selection_report.json)
- [Predeclared protocol](PROTOCOL.md)

Initial take2 start was blocked before SLAM by transient CPU pressure11%>10%
immediately after take1 fusion. The failed preflight is retained in
run_1340/take2. After unchanged environment gates passed, the same recipe was
started in a new directory run_1340/take2_retry_environment. This was an
infrastructure wait, not a rescore/tuning attempt or rejected precision result.

## take2 stopped at unchanged input gate; no final fused accuracy available

VINS input PASS, coverage99.05%, no dropped loop input/keyframe queue events.
The first stereo stage rejected the MASt3R geometry:
`stereo scale dispersion too high: relative_p90_p10=0.523` (limit0.5).
No final fused trajectory was generated. Guarded selector subsequently tried
to read the dense report, which cannot exist after this earlier-stage stop;
its missing-file traceback is a secondary orchestration limitation, not the
source of the geometric dispersion. No gate bypass or new rescue rule was
introduced during this frozen validation.

Read-only onboard cross-check on accepted PnP observations (no Tracker):

| Camera time window | take1 stereo/visual scale median | take2 stereo/visual scale median | take2 stereo/VINS length ratio median |
| --- | ---: | ---: | ---: |
|15-20s|0.19187|0.43026|0.98590|
|20-25s|0.18863|0.35160|0.99558|
|25-30s|0.19526|0.38478|0.99981|
|30-35s|0.19600|0.47372|0.98648|
|35-40s|0.19630|0.59341|0.98427|

The metric stereo-to-VINS vector residual median/P95 is1.423/5.907mm in
take2 vs1.479/5.505mm in take1. These are onboard consistency residuals,
not external ATE. They support a time-varying MASt3R local displacement-scale
issue rather than a large stereo/VINS metric-length disagreement, but do not
prove a unique frontend mechanism. Calibration/Tracker cannot cause this
gate because it never reads them. No new model-training claim is justified.

Comparison method: accepted short-hop stereo observations; VINS body poses
converted to left-IR centers with the unchanged formal body_T_cam0; nearest
matching published camera stamps within20ms at both ends; relative camera-i
vectors. No time fitting, scale fitting or GT alignment used by this diagnostic.
685/685 and734/734 accepted observations have matching VINS poses respectively.

Both raw visual trajectories contain all1199/1200 camera frames respectively,
maximum timestamp gaps33.413/33.385ms. This is not a missing-frame block.
Frontend run manifests pin identical config/checkpoint/toolchain commit:
config SHA256 `ac2577063dce6f017307f2bbe45485cb272f84b152eec86d9cb31962f9d4d84a`,
checkpoint SHA256 `e28f91b488554653e2b46ddae9c78c1143e0bcb2e27d3e26cdb0b717f1568eb2`.

[take2 failed stereo report](run_1340/take2_retry_environment/fusion/baseline/mast3r/stereo_scale_bidirectional_report.json)
