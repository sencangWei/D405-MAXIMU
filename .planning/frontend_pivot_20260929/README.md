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

## 2026-09-29 follow-up: dense native-match witness

The observation-only replay now captures **all** valid native pixel correspondences
at raw frames 1053–1078. Fresh4, fresh1, and heldout1 each reproduced both
1199-row frozen frontend trajectories **byte-for-byte**. The current toolchain
diff checksum differs from the frozen manifest because later fail-fast/race fixes
are present; the check records that difference and accepts the replay only after
both complete trajectory files prove identical. No Lighthouse/SteamVR input was
used to build the diagnostic or change a trajectory.

| Frozen case | Same-anchor persistence in this window | IMU-rotation stereo translation disagreement, max | Visual-rotation disagreement, max | Independent PnP/IMU witness frames |
|---|---:|---:|---:|---|
| fresh4, ATE FAIL | 44,762 pixel IDs common to frames 1058–1074 | 11.249 mm | 2.561 mm | 1054, 1055, 1056, 1057 |
| fresh1, ATE PASS | frequent keyframe changes | 3.876 mm | 1.826 mm | none |
| heldout1, ATE PASS | 74,836 pixel IDs common to frames 1053–1078 | 4.027 mm | 0.796 mm | none |

At fresh4 frame 1057, visual vs preintegrated IMU relative rotation is
**1.588°**; stereo-depth PnP is **0.467°** from IMU and **1.242°** from visual,
with 4,013/5,000 PnP inliers and 1.33 px P95 reprojection. The independent
stereo 3D translation fitted at fixed IMU rotation differs from the learned
visual displacement by **11.249 mm** across 36,548 depth-valid matches; image
quadrants disagree by at most **1.71 mm**. At fixed *visual* rotation the
translation discrepancy is only **0.49 mm**. This is evidence for an upstream
relative-*orientation* discrepancy, not a naked 11 mm translation offset.
Frame 1057 then becomes the next keyframe anchor; subsequent comparisons to
that new anchor fall to ~0.4–5.3 mm. The causal hypothesis is that the visual
orientation error is inherited at this anchor transition. A passing heldout
recording uses one anchor for all 26 frames, proving anchor duration alone is
not the cause.

The diagnostic rotation witness uses only onboard IMU, D405 stereo depth and
MASt3R matches: visual–IMU >1°, stereo-PnP–IMU <0.6°,
stereo-PnP–visual >0.8°, >80% PnP inliers, <1.5 px P95 reprojection,
≥10,000 depth pairs, <2 mm cross-quadrant translation spread. It marks 4/26
failed-case frames and 0/26 in each passing control. This is **not yet** an
estimator, an ATE improvement, or proof that a correction generalizes to all
ten recordings. The next bounded experiment is an opt-in anchor-relative
IMU/stereo pose correction, followed by frozen multi-case replays and the
unchanged official 10 mm gate; reject it on any passing-case regression.

Reproduce the evidence with `probe_frontend_geometry.py --first 1053 --last
1078 --dense-ids --allow-dirty-diff-change` and the read-only
`summarize_dense_tracks.py` / `compare_dense_stereo_translation.py` under this
directory. Eleven targeted tests pass. No production estimator changed.

## 2026-09-29 correction experiment — rejected

An opt-in, source-only stereo/IMU correction was inserted after frontend pose
tracking, never enabled in the production config. The first exploratory fresh4
replay passed the processing pipeline but **worsened** the unchanged official
maximum ATE from 13.801 to 14.015 mm. Its seam replay also reused incumbent
stereo reports, so it was not a promotion-grade same-mouth comparison. The
external reference was read only after the candidate trajectory was frozen.

Review found the first version mixed a metric stereo translation/rotation with
an unchanged learned Sim3 scale. A second version computes both current and
keyframe stereo/pointmap scales, uses dataset-indexed depth, and requires PnP
and paired-depth translations to agree within 5 mm. Five local fork tests pass.
Its full 1199-frame **frontend-only** replay still changes raw 1054 and 1056
substantially, but changes new-anchor frame 1057 by just 0.18 mm / 0.002° and
peak-error frame 1071 by 0.13 mm / 0.002°. Running a second expensive fusion
and score would not test a changed error block; this candidate remains
unpromoted. The reported peak is a continuous block around raw 1053–1078,
not a few isolated outliers. Source-level inspection shows that a newly
selected keyframe is subsequently optimized in the backend while non-keyframe
poses are reconstructed relative to that keyframe. A transient per-frame
tracking correction therefore cannot by itself enforce a durable multi-frame
constraint across the new anchor. The next experiment must carry **independent
onboard stereo/IMU evidence into the keyframe graph or another persistent
multi-frame constraint**, with one failed and at least two passing controls.
No ATE improvement or 10 mm compliance is claimed here.

The online candidate differs from the incumbent by **0.654° at keyframe 1057**,
but the final keyframe graph differs by only **0.002°** there; at peak frame
1071 the final difference is 0.002°. This directly demonstrates that the
single-frame correction was largely erased before the scored trajectory. It
does not by itself prove that backend optimization caused the original error:
the backend also changes local orientation in a passing control. Native
frontend optimization still has 80–110k accepted matches per frame in the
error region, so a simple lost-feature explanation is not supported either.

Additional observation-only quality checks: the 1053–1078 interval has
median 1.028° and maximum 1.968° IMU rotation per 30 Hz frame, but its IR
image sharpness/brightness are similar to the surrounding frames. The official
Tracker stream has continuous ~120 Hz timestamps in the peak interval
(largest gap 8.9 ms) and no isolated position/orientation jump. At frame 1071,
independent stereo PnP reprojection P95 rises to 1.73 px and spatially split
stereo translations differ by 4.0 mm; by 1074 the split is 9.1 mm. Thus the
available stereo measurements weaken **inside** the error block. Neither a
clearly corrupt recording nor a proven external-reference glitch explains it.
This is still an algorithm/sensor-observability problem to investigate, not
grounds to discard the recording or edit the scoring threshold.

## Short-hop stereo chain check

The read-only `compare_short_hop_stereo_chain.py` intersects **full native
same-anchor MASt3R pixel IDs** in adjacent images, computes each D405 stereo
3D translation at the 400 Hz IMU relative rotation, and integrates those
translations from raw frame 1057 to 1074. It uses no external reference and
does not alter an estimator. Two pure-geometry tests pass.

| Recording | 17-hop chain vs direct endpoint | Median/max quadrant spread per short hop |
|---|---:|---:|
| failed fresh4 | 3.02 mm | 1.84 / 13.72 mm |
| passing heldout1 | 0.84 mm | 0.51 / 1.47 mm |
| passing fresh1 | direct endpoint unavailable across keyframe changes | 1.73 / 9.30 mm |

In fresh4, the late short hops ending at frames 1070–1074 have 3.4–13.7 mm
quadrant disagreement despite tens of thousands of matched stereo-depth
pairs. This rejects the simple proposal to replace the bad long-anchor edge
with a high-weight sum of short stereo edges: the new measurement is also
weak exactly where the peak grows. The existing self-occlusion diagnostic in
`metric_window_bundle_20260928` found no supported foreground root cause;
it is not appropriate to turn on a self-mask based on these screenshots alone.
The D405 stereo provider uses 1280×720 source images with the corresponding
649.207 px focal length, then resizes the computed depth for the frontend;
there is no 2.5× focal-length mismatch in this dataset.

**Hypothesis correction:** at fresh4 frame 1057, stereo PnP differs from the
visual relative translation by 8.55 mm, versus 0.64 mm in passing fresh1 and
4.44 mm in passing heldout1. This confirms a sensor disagreement, **not**
which side is closer to the external reference. In a strictly post-trajectory
diagnostic, projecting the onboard PnP-minus-visual direction into the
already aligned score frame gives a **positive** dot product with the peak
ATE vector (+2.8 mm per unit direction); the paired-stereo/IMU direction is
also positive (+4.1 mm). These are approximate frame conversions and not a
replacement for a real replay, but they agree with the measured 13.801→14.015
mm regression. Do **not** promote an IMU/stereo override or persistent graph
factor solely from this disagreement. The working hypothesis must be revised
before more expensive estimator changes.

## Motion-matched control correction

The original two passing controls at raw1053–1078 had only 7.1° and 1.7°
rotation over 17 frames, versus fresh4's 20.0°. Comparing equal frame numbers
was therefore **not motion matched**. Two additional observation-only full
replays targeted passing windows with similar or greater onboard IMU rotation:

| Case/window | IMU rotation / 17 frames | max visual–IMU rotation | max visual–stereo translation | strict witness | whole-run official max ATE |
|---|---:|---:|---:|---|---:|
| failed fresh4 / 1057–1074 | 20.0° | 1.59° | 11.25 mm | 1054–1057 | 13.80 mm |
| passing heldout1 / 345–362 | 19.6° | 1.37° | 9.58 mm | none | 6.03 mm |
| passing fresh1 / 820–837 | 33.3° | 0.88° | 5.46 mm | none | 8.58 mm |

All four trajectories (final and online for each passing replay) were
byte-identical to the frozen outputs. Only the toolchain Git commit changed;
the producer-revision exception is restricted to commit/diff provenance and
still requires exact full-trajectory reproduction. The heldout control comes
close to the failed case in sensor disagreement but passes the strict witness
because its PnP reprojection and stereo spatial-consistency gates are weaker.
Thus the witness distinguishes these measured windows but **does not identify
the correct direction of a pose update**. The failed opt-in replay and
post-score directional check are decisive against promoting it. Motion alone,
simple image blur, match count, or a one-off Tracker jump do not explain the
remaining 13.8 mm peak.

The scored trajectory's local displacement decomposition explains the peak
without declaring either onboard sensor uniquely right. In fresh4, the
aligned fused trajectory enters raw frame 1042 with **8.15 mm ATE**. Its
1042→1057 displacement is 111.04 mm versus 109.23 mm external, with 1.43°
direction difference and **3.29 mm vector error**; ATE rises to 11.27 mm.
The 1057→1071 displacement is 126.38 versus 124.92 mm, 1.73° apart, with
**4.06 mm vector error**; ATE reaches 13.80 mm. The small errors point in a
reinforcing direction. By contrast, passing fresh1 at its high-turn window
820→837 has **6.07 mm segment vector error**, larger than either fresh4
segment, but its ATE falls from 4.03 to 3.38 mm because errors cancel.
Passing heldout1 has 1.28 mm segment vector error in its 345→362 turn.
Consequently neither speed, one-frame stereo disagreement, nor segment-vector
magnitude alone can safely route or correct the trajectory. The official
10 mm maximum is a global, alignment-dependent trajectory requirement, not
an isolated local observation threshold.

VINS-only is not a simple drop-in rescue either: at fresh4 raw1071 its
separately aligned local ATE is about 6.82 mm (below the fused 13.80 mm), but
over raw1042→1071 its segment-vector error is **11.0 mm**, worse than the
fused **6.83 mm**. Selecting VINS just because it happens to have a lower
external ATE at one frame would leak the evaluation reference into production
and could break other recordings. A valid fallback needs a separately proven
onboard selector across all ten unchanged recordings.
