# Experimental physical-unit MASt3R 3D match gate (2026-10-01)

Status: **REJECTED for production accuracy**. The source change is default-off;
only `metric_3d_match_gate_10mm.yaml` enables it. No Tracker, Lighthouse, or
robot motion was used in estimation; the frozen SteamVR trace was read only
after the complete fused trajectory existed.

## Hypothesis and isolated A/B

The original `matching.py` rejects a projected point when its MASt3R pointmap
distance exceeds a fixed 0.1 *model units*. This gate occurs before descriptor
refinement and before pose optimization. The accepted D405 left/right stereo
depth ratio shows that 0.1 units represents about 6.7 mm for take3 at source
frame 807, versus 10.6 mm for healthy take2 at the same source index. Thus
the original gate has unequal physical meaning across recordings.

Independent frame-pair checks (source→target; raw 3D gate versus a fixed 10 mm
physical gate) gave:

| Session/pair | Raw valid | Metric valid | Stereo scale m/unit | Two-view stereo |
|---|---:|---:|---:|---|
| take3 807→806 | 2.69% | 20.33% | 0.06696 | 1081 PnP inliers, depth P95 14.73 mm |
| take3 807→805 | 9.51% | 33.64% | 0.06794 | 1083 PnP inliers, depth P95 16.41 mm |
| take2 807→806 | 29.62% | 27.17% | 0.10621 | 1104 PnP inliers, depth P95 22.12 mm |
| take1 959→958 | 4.57% | 7.25% | 0.08307 | 845 PnP inliers, depth P95 19.46 mm |

The match hypothesis was confirmed **locally**, not as a trajectory-quality
fix. A separate holdout 3D fit at take3 807→806 gave 84.65% of held-out
descriptor pairs within 0.1 model units after a global Sim(3) adjustment,
versus 3.28% in the unadjusted model pointmaps. This diagnoses a substantial
coherent pointmap disagreement; it does not validate using that fit as a pose.

## Full product replay and falsification

The first take3 replay was stopped because providing stereo depth to the new
gate also unintentionally activated existing pointmap scaling. Its partial
artifacts remain under `metric_3d_match_take3_v1`; they are **not evidence**.
The code was corrected so depth for the metric match gate does not enable
`stereo_pointmap_scale_prior`, and the independent `v2` replay was repeated
as the actual single-variable experiment. Its frontend produced 1197/1199
poses (missing 853–854) in 326 s, compared with original hard loss at 807.
The official `fusion` frontend reproduced the same trajectory SHA256 exactly.

The official take3 `fusion` internal reports passed: short scale 0.35192,
medium 0.35737, dense 0.34696, multisecond 0.35612 m/model-unit; the
multisecond scale spread was 0.1676, compared with 1.182 FAIL in the earlier
2D rescue branch. IMU and graph reports also passed.

**External frozen-reference evaluation still failed** on 1141 paired poses:
mean ATE 9.862 mm, RMSE 10.937 mm, P95 18.336 mm, maximum 21.311 mm,
56.62% within 10 mm, rotation RMSE 2.106°. Peak error occurs at 27.4 s,
and the 5-second-bin mean had already risen from about 5.9 mm at 10–15 s
to 7.4 mm at 15–20 s, before the 807-frame hard loss near 26.9 s. The
original VINS-only take3 score had mean 4.218 mm and max 9.837 mm, although
it failed its rotation gate. The metric-gate fusion is therefore **worse**
than that baseline for position and must not ship.

## Root-cause boundary and next architecture

The fixed-unit gate is a real source of false rejection, but not the entire
precision failure. Changing the global threshold accepts extra 3D pairs
without checking each pair against both stereo views. For example, at take3
frame 850 the model scale rose to 0.17672 m/unit, so a fixed 10 mm gate
became stricter than the original 0.1-unit gate (39.95% versus 67.10%
passing). At frames 450, 600, 950, and 1050, independent two-view stereo
geometry rejected the candidate despite high raw 3D match fractions; a
source-only scale is insufficient to certify 3D pair quality.

Do not repeat static distance-threshold sweeps or infer accuracy from pose
coverage and internal scale PASS. The next design requires independent
two-view depth/reprojection and IMU consistency **per accepted multi-frame
3D constraint**, while preserving the MASt3R pointmap and graph as the main
representation. It must be tested on multiple independent sessions before
promotion. The default production path and prior negative-control outputs
remain unchanged.

Reproduction tools: `probe_pointmap_match_stages.py` (read-only diagnostics),
`metric_3d_match_gate_10mm.yaml` (experimental only), take3 outputs under
`metric_3d_match_fusion_take3_v1/`.
