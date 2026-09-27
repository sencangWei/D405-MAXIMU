# Stage-aware metric rescue: results

Status: take2 candidate internal gates PASS and official precision PASS.
See PROTOCOL.md for the frozen experiment and unchanged gates.

## Change and regression evidence

The guarded selector previously read the dense-stereo report unconditionally.
Take2 stopped at the earlier short-hop scale gate, so the missing dense report
caused a secondary FileNotFoundError and prevented the existing rescue branch.
Selection now follows the four ordered stereo stages. Only a sole measured
scale-dispersion failure is eligible; missing reports, non-internal provenance,
other failures, and unverified visual gaps reject. All rescue gates remain.

- Six failing regression tests reproduced before the source fix.
- 32 targeted selector/workflow/prior/scoring tests PASS after the fix, including
  two additional scale+gap and multiple-failure rejection cases.
- Read-only source review: no blockers. Compile and shell syntax checks PASS.
- Take1 remains baseline. Selected CSV SHA256:
  `04a712c54a43feafbe97c41044ad07a203e41d7c958e1129289cb9259b583c70`,
  exactly equal to the previously scored trajectory (maximum 7.018488 mm).

## Take2 candidate

Uses original accepted VINS input and prepared IR images, the existing
`mast3r_slam_d405_vins_metric_rescue.yaml`, and the same pretrained checkpoint.
No Tracker or mechanical-arm input is passed to fusion. The preset enables
0.004 m VINS translation and keyframe-position residuals, visual normalization,
stereo pointmap scale priors, and the existing backend log-scale sigma 0.05.
This change repairs rescue routing; it does not introduce a new trained model.

Frontend completed in 291 seconds: 1200 actual observed frame poses, no missing
source-frame interpolation needed. Independent short-hop stereo check PASS:
scale 0.991706, dispersion 0.135804 (original 0.523 failed; same limit 0.5),
733 accepted observations, 704 robust inliers, no continuity jumps/gaps.
Medium/dense/multisecond stereo scales: 0.993912 / 0.986997 / 0.997496;
their dispersion: 0.088085 / 0.156200 / 0.093027. All PASS, with no gaps/jumps.
IMU, keyframe graph, complementary fusion, and input quality all PASS.
Input quality: stereo edge RMSE 2.096 mm, input disagreement P95 7.392 mm,
metric scale relative difference 0.074309, full-rate inertial requested
correction 5.049 mm. Gates unchanged.

## Official post-only precision

| Take / automatic branch | Mean mm | Median mm | RMSE mm | P95 mm | Max mm | <=10 mm |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Take1 / unchanged baseline | 2.922 | 2.673 | 3.233 | 5.611 | 7.018 | 100% |
| Take2 / existing metric rescue | 4.838 | 4.836 | 5.319 | 8.703 | 9.607 | 100% |

Take2 official result PASS, 1144 effective body/IMU-origin SLAM samples,
timestamp overlap 100%, rotation RMSE 1.415 degrees. Take1 has 1143 effective
samples. These cover SLAM's valid initialized output, not the first ~1.9 seconds
before VINS initialization. No selective error-frame removal. Scoring is SE(3)
rigid alignment without scale; Sim(3) is a separately labelled diagnostic only.
Estimate bytes unchanged by scoring (SHA256
`b891943c68b8168000660089f12734bcec51c50142ce9cfc49244b521a0475cf`).

Take2 report: `take2/official_score/precision.md`; plot:
`take2/official_score/precision.png`; selected trajectory:
`take2/trajectory_fused.csv`. Green = fusion SLAM; black = frozen official
SteamVR reference mapped to body origin. Coordinate panels are XY/XZ projections,
not guaranteed semantic top/side directions in the SteamVR standing frame.

This achieves the stated maximum-error gate on both fresh captures. Take2's
margin is only 0.393 mm; this is not a guarantee for every future recording.
The recipe was frozen before execution; no Tracker-dependent candidate tuning,
model training, threshold relaxation, or clock/extrinsic refit was done.

Diagnostic median VINS/visual displacement-length ratios for 5-frame increments,
using valid camera priors and displacement >5 mm (no GT, no fitted scale):

| Elapsed seconds | Original frontend | Metric rescue frontend |
| --- | ---: | ---: |
| 10–15 | 0.444337 | 1.021094 |
| 15–20 | 0.439651 | 1.036109 |
| 20–25 | 0.357932 | 1.001807 |
| 25–30 | 0.378235 | 0.985222 |
| 30–35 | 0.470001 | 0.953060 |
| 35–40 | 0.598499 | 0.981360 |

The rescue uses VINS, so this ratio is an optimizer-consistency diagnostic, not
independent precision evidence. Stereo gate and post-only Tracker score are
separate checks; a two-take result cannot establish general <=10 mm reliability.

## Backup

Selector, initial regression tests, and frozen protocol backed to
`sencang/codex/steamvr-reference-probe-20260927`, commit
`ab70f2b2d8e976d1b63f422d5cdf60eb92b4c551`. Fetched into fresh detached worktree
`/tmp/ego_vio_metric_rescue_restore_zm5Nzg`: all three files byte-identical,
14 selector+camera-prior tests PASS, clean tracked tree.

Final additional tests, control/candidate trajectories, all internal gate reports,
official reference and precision artifacts backed in commit
`c0a70fcfe697dff8a61851e25ce0bf4ac4ee7942` on the same writable remote branch.
Fresh fetched restore `/tmp/ego_vio_metric_rescue_result_restore_JzntVB`:
28 archived files byte-identical to active code/evidence; 16 selector+prior tests
PASS; independent re-score from restored estimate/reference reproduced all
30 numeric fields within 1e-12, identical PASS/thresholds; clean tracked tree.
CSV evidence retains original CRLF bytes (whitespace check uses cr-at-eol).
Only exact evidence paths were staged, never the reports root. Raw DB3/images
remain on the original host; this archive verifies scoring reproducibility,
not a self-contained full frontend rerun without source recordings/toolchain.
