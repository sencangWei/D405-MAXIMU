# Stereo descriptor recovery experiment, 2026-10-01

Status: experimental and default-off (`MAST3R_STEREO_DESCRIPTOR_RECOVERY=1`).
Only UMI left/right IR, 400 Hz IMU prior, and factory stereo calibration enter estimation;
Tracker/Lighthouse are not used by this recovery branch.

## What was isolated

- Take3 frame 806→807: original 3D pointmap matching is 2.69%, below the
  5% tracking threshold, despite continuous images. Reciprocal MASt3R 2D
  descriptors plus D405 stereo depth provide 1005 PnP inliers, 0.628 px
  reprojection P95, 13.75 mm two-view depth P95, and 0.068° IMU rotation
  disagreement. The first hard failure is the pointmap-first matching gate,
  not missing source images or absent 2D correspondences.
- The opt-in candidate uses reciprocal 2D descriptors and source stereo depth,
  then requires RANSAC PnP, target stereo depth, robust pointmap gauge, and
  IMU rotation agreement before composing a pose-only recovery. It neither
  lowers the original front-end match threshold nor creates an unverified
  keyframe. A second pass may consider low model confidence only when the
  same independent geometric checks still pass.

## Frozen-data A/B evidence

- Take3: original front end 807/1199 poses; candidate 1199/1199. Frames
  807–810 recovered, with no detected continuity jump. Stereo short,
  medium, and dense reports PASS with global scales 0.3729, 0.3811, and
  0.3848 m/trajectory-unit, respectively.
- Take3 **does not pass** full fusion: multisecond stereo scale dispersion
  1.182 FAIL. Accepted long-hop scale median changes from about 0.38 before
  the failure to 0.25 at frames 900–1000 and 0.68 at 1000–1100. Online and
  backend trajectories show nearly the same segment path lengths, so this
  is not fixed by pose-graph output. A diagnostic graph run excluding the
  failed long-hop report still blocks on
  `trajectory_frame_orientation_correction_too_large`; it is not a valid
  substitute for the production run. No <10 mm accuracy claim is made.
- Take2: candidate trajectory CSV SHA256 equals the original PASS baseline
  exactly (`10d907adc2bce19f231bb4ec19f534fe7cf08d700963dc876a3cf0444e443f9d`),
  with 1199/1199 poses and no recovery trigger.
- Take1: candidate recovers frame 959, producing 1199/1199 poses with no
  local seam jump. The short/mid/dense/multisecond stereo and full fusion
  internal checks PASS, but the frozen SteamVR reference scores 1143
  corresponding poses at mean 5.282 mm, P95 9.891 mm, max 10.963 mm,
  95.36% within 10 mm, rotation RMSE 3.083°. The external 10 mm/2°
  gate therefore FAILS on maximum ATE and rotation. The existing VINS
  baseline for this session scores mean 2.630 mm, max 8.454 mm, rotation
  RMSE 1.338°; the experimental fusion is worse and must not replace it.
  The largest candidate translation errors occur around 36.4–36.8 s,
  several seconds after the recovered frame at about 32.0 s; another
  >10 mm block begins around 30.7 s, before the recovery. The hard-loss
  event is not the only source of error.

## Why frame repair alone is insufficient

In take3 tail, MASt3R pointmap confidence often collapses near its 1.0
floor although descriptor matching remains plentiful. At frames 900/950/
1000/1150, reciprocal temporal descriptors number about 2000–2300 and
the D405 stereo depth map is valid on 40–53% of pixels. At 950/1000/1100/
1150, allowing low-confidence descriptors **only as candidates** still
passes >950/>1200/>1100/>900 PnP inliers (frame 950/1000/1100/1150
respectively) and stereo/IMU geometry; frames 900 and 1050 are rejected by
the independent target-depth P95 gate. The original MASt3R tracker does not
apply a 1.5 C gate (`C_conf` is 0): low C is evidence of an unreliable
pointmap, not the direct cause of its 3D-match rejection.

Learned left/right stereo was also probed at the model's C=1.0 floor. It
finds 1772–2209 rectified reciprocal matches per tail frame, but its depth
agreement with independent SGBM is inconsistent: P95 18.86–22.36 mm at
frames 1000/1100 versus 51.84–71.14 mm at 900/950/1050/1150. Thus raw
low-confidence learned stereo matches are candidates, not reliable metric
constraints; any continuous-window use must robustly cross-check two-view
depth, temporal reprojection, and IMU before graph insertion.

The next architecture test must use accepted descriptor/stereo relative
motions as **continuous multi-frame metric constraints**, with VINS/IMU
short-motion support for gaps and an onboard-only quality gate. Injecting
four corrected positions or relaxing the multisecond/rotation gates cannot
repair the subsequent nonrigid visual trajectory.

Code backups: MASt3R fork branch `codex/relocalization-recovery-20261001`
at `435b1bd`; main workflow provenance branch `codex/dual-ir-frontend-20261001`
at `108a1d27`. Both were pushed to `sencang`-owned remotes and fetched back;
file hashes matched. Existing production configuration remains unchanged.
