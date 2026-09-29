# 2026-09-29 precision pivot: source-only direction check

The current fused trajectory stays at **9/10 PASS**. The only failing frozen recording, fresh4, retains its official SE(3)-aligned maximum position ATE of **13.801442 mm**; this diagnostic did not rerun or edit an estimator.

## Question

Can agreement between independently recorded Docker2 VINS camera motion and D405 stereo PnP, when MASt3R disagrees, identify a safe correction for the fresh4 26-frame error block? The earlier chord diagnostic retained only vector lengths, although a correct length can have the wrong direction.

`compare_metric_loop_chords.py` now also expresses the interpolated VINS camera-centre displacement in the first camera's axes, using the formal Docker2 `body_T_cam0` rotation and lever arm. It compares that vector with the existing, frozen MASt3R and bidirectional D405 stereo displacement vectors. It refuses stereo reports with failed input quality or external supervision. The three v2 JSON reports in this directory contain input hashes and every accepted link, including stereo forward/reverse spread. Neither Lighthouse nor SteamVR is loaded by this diagnostic.

| Frozen case | Accepted links with VINS coverage | Median MASt3R–stereo vector difference | Median VINS–stereo vector difference | Median stereo forward/reverse spread |
| --- | ---: | ---: | ---: | ---: |
| fresh4, **ATE FAIL** | 27 | 5.979 mm | 4.048 mm | 1.488 mm |
| fresh1, ATE PASS | 33 | 4.337 mm | 3.810 mm | 0.709 mm |
| heldout1, ATE PASS | 44 | 2.340 mm | 4.838 mm | 1.053 mm |

At fresh4 995→1074, MASt3R–stereo is **10.122 mm** but VINS–stereo is **8.304 mm**: even the two onboard comparators do not agree at the required millimetre level. At fresh4 909→1085, both disagreements exceed 10 mm and stereo's forward/reverse spread is 5.185 mm. The same-index fresh1 passing control 992→1043 has MASt3R–stereo **11.457 mm**, VINS–stereo **3.810 mm**, and stereo spread **0.889 mm**. Thus the apparently strong sensor-consensus condition has an explicit passing-recording counterexample.

For illustration only, the exploratory rule `VINS–stereo <5 mm`, stereo forward/reverse spread `<2 mm`, and MASt3R–stereo `>8 mm` fires on **7** fresh4 links and **4** fresh1 links. All seven fresh4 hits are at frames 584–767, *not* at the final 1053–1078 failure block; the four fresh1 hits are at 992–1145. It is neither a specific bad-block detector nor a safe production switch. Accepted retrieval links are correlated and unevenly distributed, so the counts are not independent-sample statistics.

An additional read-only fixed-frame check of the saved 2048-point frontend samples at raw frames 1053–1078 found median top-vs-bottom reprojection residual differences 0.232/0.224/0.276 px for fresh4/fresh1/heldout1. That simple image-row signature also does not distinguish the failure.

## Decision

**Reject** a global visual/stereo/VINS disagreement threshold, GT-selected routing, or interpolation of this gradual 26-frame error block. The previous opt-in VINS absolute backend prior is separately rejected because it turned two passing controls into ~14–15 mm failures and produced a nonfinite solver state on dev2. Do not continue sweeping its weights.

The next justified work is a different measurement model, not another threshold: test whether multi-frame MASt3R correspondences plus the recorded right-IR observations can produce a held-out, metrically consistent local constraint over the failing interval **and passing controls**. Keep that test diagnostic-only until it shows a sensor-only quality witness and does not regress any passing recording. If the D405's short stereo baseline and the existing VINS disagree at the same window, mark that window's correction unobservable from these measurements instead of forcing a 10 mm claim.

The saved 2048-pixel-per-frame *diagnostic subsample* cannot by itself support
that multi-frame test: on fresh4 raw frames 1058–1074 (one keyframe anchor),
adjacent saved samples share a median of only 36 keyframe pixel IDs and the
17-frame intersection is empty. Heldout1 has median 49 adjacent IDs and no
26-frame common ID. This is a limitation of the uniform diagnostic sampling,
**not** evidence that native dense MASt3R matches are absent. A finite next
step must capture dense same-keyframe correspondences for this exact window
and fixed passing controls, preserving the original trajectories byte-for-byte,
before attempting any multi-frame factor.

Verification in this continuation: three targeted tests pass, including a nontrivial rotated-camera/rotating-lever synthetic case; all three stereo inputs say `PASS`, `slam_supervision=false`, and `external_ground_truth_used=false`. No production trajectory changed.
