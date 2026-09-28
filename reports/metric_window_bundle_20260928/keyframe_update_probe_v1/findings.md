# Native keyframe point-map update: fixed three-case diagnostic

2026-09-28. This is observation-only. It does not modify the MASt3R model,
tracker, global optimizer, fusion graph, timestamps, calibration, evaluation,
or any trajectory. No external pose/ground truth was read by the capture or
comparison. The overall 10 mm accuracy goal remains unmet: the previously
frozen ten-case joint graph is 9/10 PASS and fresh4 maximum ATE is 13.801442 mm.

## Reproducibility and scope

- Frozen inputs: `fresh4`, `fresh1`, `heldout1`; each replayed all 1199 frames.
  The diagnostic captures frames 1000–1120 inclusive, 2048 uniformly sampled
  optimizer-valid keyframe pixels per frame. The same 121 keyframe assignments,
  stereo depths, calibrated rays, and original geometry arrays were verified
  against the prior probe in each case.
- All three replay traces report zero capture errors. Both 1199-row full and
  online trajectory CSVs are byte-identical to their frozen originals in every
  case. The 363 native weighted-update formula checks and 363 derived boundary
  pose/proposal checks all pass. All 363 frames have usable same-ray stereo
  depth. Source, producer, input, trace, and sample SHA-256 identities are in
  [comparison.json](comparison.json); per-frame NPZs are under each case's
  `samples/` directory. The source/test entrypoints are
  `probe_keyframe_update.py`, `keyframe_update_diagnostics.py`, and
  `summarize_keyframe_update.py` in `.planning/metric_window_bundle_20260928`.
- The 1053–1078 range was identified from the *previous* external evaluation
  as fresh4's 26-frame >10 mm block. It is used for post-hoc explanation only,
  not as a training, weighting, selection, or interpolation input. Equal frame
  numbers in separate recordings are not the same physical motion.

## What the native update actually shows

The native `weighted_pointmap` branch adds incoming confidence and takes a
confidence-weighted XYZ mean. In the failed fresh4 block, the median incoming
fraction across frame medians is 0.149; the per-frame median old-to-actual-new
XYZ update, converted using each frame's old-map/stereo depth ratio, is 2.101
mm. The new proposal's corresponding old-map difference is 14.570 mm. These
are same-ray map changes, **not trajectory ATE or uncertainty estimates**.

| Case, frames 1053–1078 | Incoming fraction | Actual XYZ update (mm) | Old / proposal / after centered log-depth-shape |
| --- | ---: | ---: | ---: |
| fresh4, failed block | 0.149 | 2.101 | 0.0365 / 0.0289 / 0.0324 |
| fresh1, passing control | 0.499 | 5.588 | 0.0892 / 0.0709 / 0.0865 |
| heldout1, passing control | 0.046 | 0.178 | 0.0470 / 0.0420 / 0.0468 |

These are medians of the 26 *per-frame sampled-ray medians*. Fresh1 passes
despite larger map updates and worse scale-free depth shape in the same index
range. In fresh4 the proposal depth shape is **better**, not worse, than the
old map during the failed block. Thus neither a broken update formula nor an
unusually large update/shape deterioration discriminates that failure. It would
be unjustified to clamp confidence, suppress keyframe updates, or interpolate
those 26 poses on this evidence.

Raw XYZ often projects several pixels away from the calibrated grid, but native
`tracker.get_points_poses` constrains calibrated point maps to pixel rays
before pose optimization. Raw lateral-ray disagreement therefore is not itself
an optimizer reprojection residual. This capture does not prove that the
learned depth or correspondence is correct; it only rules out the tested simple
weighted-update-corruption mechanism.

## Next discriminating boundary, not yet a repair

The native `evaluate.save_full_traj` reconstructs each final frame from its
tracked keyframe-relative pose and the **final backend-optimized keyframe**;
`save_online_traj` records frontend poses before backend re-anchoring. As a
read-only localization check, rigidly align online positions to final
positions over frames 1000–1052 in each case, then hold that transform fixed.
The final-versus-online median position difference over 1053–1078 is 15.051 mm
for fresh4, 3.929 mm for fresh1, and 0.421 mm for heldout1. The fresh4 pre-fit
median is already 4.033 mm. This is a *difference between two SLAM outputs*,
not their error against an external reference; it suggests examining the
backend re-anchoring/loop correction separately, not declaring it the cause.
Prior take1/take3 evidence found some distortions already in online poses, so
switching to online or disabling the backend is not an established fix.

After the UMI-only comparison was frozen, an **evaluation-only** check used the
same SteamVR body reference, fixed `body_T_cam0` calibration and 0.05 s
interpolation gap to compare raw MASt3R final/online poses. Raw MASt3R is not
metric on these recordings: the diagnostic GT/estimate Sim(3) scales are about
0.27–0.47, and its no-scale SE(3) ATE is hundreds of millimetres. Neither is
the fused product's ATE. As a shape-only diagnostic, final versus online
Sim(3)-aligned P95 is fresh4 18.37/18.87 mm, fresh1 26.57/29.39 mm, and
heldout1 11.93/13.63 mm. For fresh4 frames 1053–1078, the corresponding
median shape residual is 4.32/5.97 mm. Final is modestly *better* in all three
comparisons. Thus the large final–online difference alone does not justify
disabling the backend; the local metric/fusion error remains underidentified.
Evaluation outputs are in `backend_boundary_eval/` and were not used by the
capture, diagnostic comparison, or estimator.

Next finite test: isolate whether the 26-frame error enters the fused metric
trajectory through MASt3R local shape or the stereo/IMU-to-MASt3R scale
interface. Compare frozen full/online local relative motions to raw independent
stereo/IMU evidence in all three cases, without GT-based factor admission or
threshold fitting. If backend re-anchoring is revisited, capture exact
per-keyframe online/final Sim(3), relative tracked poses and accepted constraints
first; no direct online switch. Any repair must pass the unchanged all-ten gate
without losing the existing nine passes.

## Limitations

Uniform 2048-ray samples are spatially correlated. Old-map-to-stereo ratios
provide local unit conversion, not a certified global scale. Fixed frame
windows contain different motions across cases. This diagnostic cannot assign
the remaining 13.8 mm ATE to any single component; no estimator fix or new
10 mm pass is claimed.
