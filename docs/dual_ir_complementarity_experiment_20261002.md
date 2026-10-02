# Dual-IR MASt3R local complementarity experiment (2026-10-02)

Status: experimental, **not promoted to production**. No claim that all frames
meet the 10 mm target. Lighthouse/SteamVR is used only by the separate scorer,
after each candidate is frozen; it is not an input to trajectory optimization.

## Implementation and boundary

Each eye has its own cached MASt3R trajectory. Right-eye positions are brought
to metric scale using its stereo/IMU reports. Its camera-origin motion is
converted to body-origin motion with the right-eye factory extrinsic, then
transported into the primary left-eye world using the body orientations.
The backend adds confidence-weighted right-eye relative-motion factors, with
8 mm robust residual weighting. Lever arms are not scaled as visual motion.

Version 1 admitted right factors based on right stereo consistency alone.
Version 2 additionally compares both eyes against the same local right stereo
measurement: a factor contributes only when the right eye has a local residual
advantage. Missing observations, timestamp gaps, rejected stereo measurements
and zero confidence contribute no factor. The same stereo measurements are not
added again as independent stereo rows.

This is currently **right-to-left local assistance**, not a complete two-way
frontend failover. The left trajectory remains the primary anchor. It cannot
recover a frame absent from both frontend products, and both eyes can share
model, scene and stereo-measurement errors.

Optional CLI inputs are isolated behind `--secondary-right-*`; without them
the original solver path is unchanged. The production wrapper is unchanged.
The experimental wrapper is `scripts/mast3r_dual_ir_fusion_postprocess.sh`:

```sh
bash scripts/mast3r_dual_ir_fusion_postprocess.sh \
  SESSION LEFT_MAST3R_DIR RIGHT_MAST3R_DIR VINS_DIR NEW_OUTPUT_DIR
```

It consumes cached inputs, never external reference poses. A candidate manifest
records input hashes and `EXPERIMENTAL_NOT_ACCEPTED`. Diagnostic graph output
may be emitted even when graph acceptance fails; a CSV or downstream fusion
PASS is **not** evidence of graph acceptance. Inspect the graph report and the
candidate manifest. Existing formal acceptance thresholds remain unchanged.

## Frozen three-recording comparison

Same cached recording, VINS output and scorer/reference contract for each row;
no per-recording parameter selection. Docker2 time calibration remains fixed
at `td=-0.009109323`, with no extra replay IMU shift. The left control permits
the full requested correction, as do both experimental variants. In particular,
take 6's approximately 92 mm request is not limited to 25 or 40 mm here.
All scores use 1,143 matched timestamps and SE(3), without scale alignment.

Translation ATE in mm (mean / P95 / maximum):

| Recording | Left-only full correction | Dual v1 | Dual v2 local advantage |
| --- | --- | --- | --- |
| Take 2 retry | 2.219 / 4.786 / 7.019 | 2.070 / 4.482 / 5.515 | 2.203 / 4.743 / 6.867 |
| Take 4 | 3.532 / 9.224 / 11.073 | 3.726 / 10.613 / 12.345 | 3.548 / 9.378 / 11.205 |
| Take 6 | 3.310 / 5.620 / 12.973 | 4.147 / 7.781 / 15.870 | 3.356 / 5.752 / 13.222 |

V2 admitted 529 / 724 / 848 right factors, respectively. Take 2 improves
slightly, but takes 4 and 6 regress slightly versus the fully corrected left
control. Therefore this experiment does not establish an overall precision
benefit. Stereo-based local trust alone is not a validated discriminator of
which frontend has the correct trajectory shape; that explanation is a working
inference, not proof of a specific model defect. Do not promote this candidate
or continue unbounded weight/cap sweeps based on these scores.

Evidence under `.planning/frontend_observation_20260930/`:

- `dual_ir_same_code_20261002/right_take{2,4,6}`: cached right frontend products.
- `dual_ir_joint_factors_20261002_v1/take{2,4,6}`: first experiment and scores.
- `dual_ir_joint_factors_20261002_v2/take{2,4,6}`: local-advantage experiment
  and `diagnostic_score/precision.json`.
- `dual_ir_joint_factors_20261002_v2/control_take2_retry`: fresh no-secondary
  replay. Its graph trajectory is byte-identical to the previous left-only
  full-correction control, confirmed with `cmp`.
- Left controls: `.planning/six_take_validation_20260930/ab_correction_cap1000_20261002_v1/`.

## Verification

The following targeted suite passed: **93 tests**.

```sh
pytest -q tests/test_dual_ir_factors.py tests/test_dual_ir_joint_fusion.py \
  tests/test_mast3r_stereo_imu_fusion.py tests/test_right_ir_stereo_scale.py \
  tests/test_mast3r_joint_metric_cap.py
```

Coverage includes independently transformed eye/world frames, pure-rotation
lever-arm cancellation, gaps/missing data, source-frame/time binding, local
advantage and zero-confidence behavior. Python compilation, shell syntax and
diff whitespace checks also pass. This does not replace the three-recording
precision evidence above or prove full two-way recovery.
