# fast10 ORIGINAL factor peak-window census (2026-10-03)

Scope: read-only census over the fixed fast10 IDs in
`config/dual_ir_fast_regression_10_20261003.json`, using the ORIGINAL current
best artifact from
`.planning/dual_ir_regression_25_20261002/gauge_physical_combined_batch_v1/summary.json`
variant `physical_stereo_constant_gauge`.

This is a development diagnostic. ATE peak locations use saved scorer outputs
only to define post-output diagnostic windows; no solver/source selection is
changed and no GT/reference signal is proposed for production use.

## Inputs and conventions

- Config: `config/dual_ir_fast_regression_10_20261003.json`
- Baseline summary: `.planning/dual_ir_regression_25_20261002/gauge_physical_combined_batch_v1/summary.json`
- Variant: `physical_stereo_constant_gauge`
- Diagnostic window: factor interval crosses `ATE_peak_time ± 1.0s`.
- Physical stereo residual is computed with the current solver convention:
  `positions[j] - positions[i] - camera_rotations[i].apply(metric_displacement_camera_i_m)`.
  Code reference: `scripts/fuse_mast3r_stereo_imu.py:2400-2418`,
  `scripts/fuse_mast3r_stereo_imu.py:2504-2520`.
- Learned residual is computed from saved `local_motion_factors.json`:
  `positions[j] - positions[i] - metric_displacement_world_m`.
  Code reference: `scripts/fuse_mast3r_stereo_imu.py:2207-2221`.
- The final column reports existing native4 paired outputs when available; it is
  a separate result, not part of this ORIGINAL baseline census.

## Peak-window table

| id | cohort | result/failures | peak rel s / idx | max / p95 mm | physical crossing edges (recovered) | physical residual P95 mm / confidence P50 / duration P95 s | learned L/R residual P95 mm | R/L confidence ratio P50 | native4 max / physical P95 mm |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 20260927_ind2 | failure | FAIL `ate_translation_p95_over_limit,ate_translation_max_over_limit,within_10mm_ratio_below_limit` | 36.831 / #1105 | 14.40 / 11.53 | 151 (0) | 2.27 / 0.603 / 2.667 | 8.38 / 32.77 | 0.06 | 10.36 / 2.25 |
| 20260929_take02 | failure | FAIL `ate_translation_rmse_over_limit,ate_translation_p95_over_limit,ate_translation_max_over_limit,within_10mm_ratio_below_limit,ate_rotation_rmse_over_limit` | 37.264 / #1118 | 18.85 / 18.43 | 0 (0) | NA / NA / NA | NA / NA | NA | - |
| 20260929_take04 | failure | FAIL `ate_translation_max_over_limit,ate_rotation_rmse_over_limit` | 37.730 / #1132 | 10.90 / 6.75 | 54 (0) | 6.19 / 0.559 / 0.492 | 5.78 / 11.86 | 0.85 | - |
| 20260929_take07 | failure | FAIL `ate_rotation_rmse_over_limit` | 37.898 / #1137 | 9.86 / 7.75 | 77 (0) | 14.90 / 0.468 / 1.333 | 8.20 / 99.28 | 0.02 | - |
| 20260930_take06 | failure | FAIL `ate_translation_max_over_limit` | 34.330 / #1030 | 14.76 / 5.69 | 156 (0) | 3.60 / 0.505 / 2.667 | 21.90 / 32.66 | 0.07 | 13.48 / 3.30 |
| 20260927_heldout2 | passing | PASS | 37.264 / #1117 | 6.18 / 5.14 | 122 (0) | 2.53 / 0.508 / 2.667 | 7.19 / 152.77 | 0.02 | 5.57 / 2.00 |
| 20260927_heldout4 | passing | PASS | 38.033 / #1141 | 9.44 / 5.59 | 54 (0) | 2.39 / 0.413 / 0.833 | 4.01 / 1.94 | 1.15 | 8.32 / 1.09 |
| 20260929_take01 | passing | PASS | 24.796 / #744 | 9.24 / 7.60 | 112 (0) | 7.98 / 0.525 / 0.833 | 6.09 / 6.14 | 1.02 | - |
| 20260930_take03 | passing | PASS | 33.063 / #992 | 9.85 / 5.94 | 0 (0) | NA / NA / NA | NA / NA | NA | - |
| 20260930_take04 | passing | PASS | 12.295 / #369 | 5.90 / 4.41 | 7 (0) | 2.80 / 0.344 / 8.000 | 9.40 / 10.47 | 0.98 | - |

## Coverage/connectivity notes

| id | total physical edges | total learned edges | nearest physical midpoint to peak (s) | crossing physical edges | crossing learned edges |
|---|---:|---:|---:|---:|---:|
| 20260927_ind2 | 1353 | 2536 | 0.000 | 151 | 274 |
| 20260929_take02 | 681 | 1335 | 19.667 | 0 | 0 |
| 20260929_take04 | 883 | 1694 | 0.000 | 54 | 106 |
| 20260929_take07 | 2058 | 4067 | 0.017 | 77 | 147 |
| 20260930_take06 | 1494 | 2985 | 0.000 | 156 | 312 |
| 20260927_heldout2 | 1493 | 2980 | 0.017 | 122 | 244 |
| 20260927_heldout4 | 1675 | 3349 | 0.017 | 54 | 108 |
| 20260929_take01 | 1687 | 3370 | 0.000 | 112 | 224 |
| 20260930_take03 | 479 | 852 | 8.084 | 0 | 0 |
| 20260930_take04 | 1272 | 2462 | 3.667 | 7 | 14 |

The exact peak sample is often not itself a physical-edge endpoint, so endpoint
component size at the exact peak is not used as the primary support metric here.
Crossing edges and nearest midpoint are the less misleading diagnostics.

## Evidence-vs-inference summary

Evidence:

- `20260929_take02` has the strongest local coverage deficiency among the fast10
  failures: zero physical and zero learned edges crossing the ATE peak ±1s, with
  the nearest physical edge midpoint 19.667s away. This is not unique to all
  errors, because `20260930_take03` is a PASS and also has zero crossing edges at
  its own post-output peak.
- `20260929_take04` does not share the take02 zero-coverage pattern. It has 54
  physical crossing edges and 106 learned crossing edges near peak. Its physical
  residual P95 is 6.19mm, worse than heldout2/heldout4/take04 but below PASS
  `20260929_take01` physical residual P95 of 7.98mm.
- `20260929_take07` is an honest rotation-only failure in this baseline:
  translation max is 9.86mm, but score failure is `ate_rotation_rmse_over_limit`.
  It has high physical residual P95 (14.90mm) and high RIGHT learned residual P95
  (99.28mm), but using it as a translation-max failure would be wrong.
- High RIGHT learned residual is not a unique failure signal: PASS
  `20260927_heldout2` has RIGHT learned residual P95 152.77mm with very low
  right/left confidence ratio (0.02), yet passes.
- Both-eye learned conflict is more visible in `20260930_take06` than in the
  PASS controls in this table: LEFT/RIGHT learned P95 is 21.90/32.66mm. However,
  its physical coverage near peak is strong (156 crossing edges; physical
  residual P95 3.60mm), so "missing physical coverage" is not supported for
  take06.
- Root's raw reference census should be kept separate from this factor census:
  no GT jump explains ind2/take02/take04; take06 has a known abrupt Tracker
  position correction near peak, but ATE drops after that raw jump, so this file
  does not classify all take06 error as reference failure.

Inference:

- The remaining ordinary failures do not share one clean signature of
  "both-eye learned conflict plus low physical geometry quality absent in PASS
  controls." take02 is primarily a local factor-coverage hole; take04 is a small
  max/rotation failure with moderate physical residual; take06 is covered by
  physical stereo but has larger learned L/R conflict; take07 is rotation-only.
- Physical residual P95 alone is not a safe discriminator: PASS take01 is worse
  than failure take04 by this metric, while PASS heldout2 is worse than several
  failures by RIGHT learned residual.
- The next useful discriminator after full fast10/full25 is not a weight/cap
  sweep; it is the same fixed census across all newly completed outputs, checking
  whether failures cluster by (a) no local factor support, (b) high both-eye
  learned conflict, or (c) source/reference discontinuity caveats. Current
  evidence does not justify a production filter or GT-informed selector.

## Repro notes

The table was computed from saved CSV/JSON artifacts only:

1. Load fast10 IDs from `config/dual_ir_fast_regression_10_20261003.json`.
2. Resolve artifact dirs from `gauge_physical_combined_batch_v1/summary.json`
   variant `physical_stereo_constant_gauge`.
3. Recompute SE3(no-scale) aligned ATE from each artifact's
   `body_trajectory_fused.csv` and `score/steamvr_body_reference.csv` to locate
   the post-output peak.
4. Read `shared_stereo_observations.json` and `local_motion_factors.json`.
5. Count and summarize factors whose `[first_t_sec, second_t_sec]` interval
   crosses peak±1s using the solver residual conventions cited above.

## Addendum: fast10 raw frontend temporal coverage (take02 evidence)

Root-provided new evidence for `20260929_take02` changes the interpretation of
the zero-edge peak window: the tail is not stationary, and both MASt3R frontend
streams end at about half-record duration.

Read-only coverage census from the fast10 source manifests:

| id | dataset frames/span | LEFT metric trajectory | RIGHT metric trajectory | note |
|---|---:|---:|---:|---|
| 20260927_ind2 | 1199 / 39.927s | 1199 / 39.927s | 1199 / 39.927s | full L/R |
| 20260929_take02 | 1199 / 39.926s | 587 / 19.525s | 588 / 19.559s | both frontends truncate halfway |
| 20260929_take04 | 1199 / 39.926s | 1199 / 39.926s | 1199 / 39.926s | full L/R |
| 20260929_take07 | 1199 / 39.927s | 1199 / 39.927s | 1199 / 39.927s | full L/R |
| 20260930_take06 | 1199 / 39.926s | 1199 / 39.926s | 1199 / 39.926s | full L/R |
| 20260927_heldout2 | 1199 / 39.926s | 1199 / 39.926s | 1199 / 39.926s | full L/R |
| 20260927_heldout4 | 1199 / 39.929s | 1199 / 39.929s | 1199 / 39.929s | full L/R |
| 20260929_take01 | 1199 / 39.926s | 1199 / 39.926s | 1199 / 39.926s | full L/R |
| 20260930_take03 | 1199 / 39.926s | 807 / 26.859s | 789 / 26.259s | truncated but PASS in baseline |
| 20260930_take04 | 1199 / 39.926s | 1199 / 39.926s | 1192 / 39.926s | near-full right |

Exact take02 source evidence:

- LEFT source:
  `reports/steamvr_umi_sessions/20260929_195055_slam_validation_take02/evaluation_20260929_v1/fusion_adaptive/sparse/mast3r/`
  - `dataset/dataset_manifest.json`: `frames=1199`, `first_t_sec=1790682660.5293646`,
    `last_t_sec=1790682700.4554837`, stream `infrared_left`.
  - `trajectory_frames.manifest.json`: `source_poses=587`, `dense_frames=587`,
    timestamp table `.../mast3r/dataset/frames.csv`.
  - `mast3r.log`: repeated `Failed to relocalize`, then `Skipped frame 587`,
    then `done`.
- RIGHT source:
  `.planning/dual_ir_regression_25_20261002/batch_adapters_v2/20260929_take02/right_cache/`
  - `dataset/dataset_manifest.json`: `frames=1199`, same source session,
    stream `infrared_right`.
  - `frontend_coverage_report.json`: `compatible_original_camera_frames=1199`,
    `raw_tracked_poses=588`, `raw_coverage_ratio=0.49040867389491244`,
    `dense_frames=588`, `partial_track_allowed=true`.
  - `dual_ir_eye_cache_manifest.json`: `trajectory_used_for_downstream` is the
    right cache `trajectory_frames.csv`; `interpolated_dense_used_for_downstream=false`;
    `frontend_reused_without_gpu=true` from
    `.planning/dual_ir_regression_25_20261002/batch_v1/20260929_take02/right_cache`.
  - Source right log under `batch_v1/.../right_cache/frontend.log`: repeated
    `Failed to relocalize`, then `Skipped frame 588`, then `done`.
- Existing independent-IR recovery appendix for take02:
  `.planning/dual_ir_regression_25_20261002/independent_ir_corpus_native_remaining21_v1/20260929_take02/independent_ir_recovery_appendix.json`
  has `observations=563`, `native_accepted_count=93`, and original endpoint
  range `40..586`. It therefore cannot propose constraints in the 19.6s--40s
  tail, regardless of full cached photos being present.

Evidence boundary:

- This is actual both-eye frontend loss, not an accidental RIGHT input crop:
  both LEFT and RIGHT dataset manifests bind 1199 frames from the full session,
  while both raw trajectories stop at 587/588 poses after relocalization failure.
- Root's motion evidence says the missing tail is not a benign stationary span:
  from 18s onward GT range is about 417mm, end drift about 171mm, and VINS range
  about 412mm.
- The current recovery producer inherits the truncated MASt3R time domain because
  it loops source report rejected endpoints. It is not designed to invent
  post-frontend-loss pair endpoints.

Minimal architecture candidate (not implemented here):

- Add a distinct source-only "timeline gap native geometry" stage for records
  whose MASt3R raw trajectory stops before the dataset tail. It should sample a
  fixed, predeclared set of full-session D405 frame pairs in the uncovered time
  interval and run the existing native SIFT/PnP bidirectional metric check on
  actual LEFT and RIGHT pixels. The output must use a new schema and lineage,
  not masquerade as MASt3R rejected-row recovery, because the pair set would come
  from dataset time coverage rather than existing source report rows.
- Keep it separate from weight/cap/filter families: it is a new source-coverage
  hypothesis for missing physical observations after frontend loss.

Falsifier:

- On take02 tail, if fixed full-session native pairs cannot produce accepted
  bidirectional metric observations in the missing 19.6s--40s region, then the
  source-coverage hypothesis fails.
- On truncated-but-PASS `20260930_take03`, the same stage should not introduce a
  false regression or hidden selector behavior. If it does, the architecture is
  not safe as a general corpus extension.
- Any follow-up must also prove source/hash/session/frame binding and keep GT out
  of solver/source selection.
