# Bidirectional left/right MASt3R shared graph (2026-10-02)

Status: **experimental, not promoted**. User requirement: symmetric local
complementarity, not a fixed right-to-left assistance path or whole-eye selector.

## What changed

`scripts/fuse_mast3r_dual_ir_symmetric.py` consumes cached independent learned
left/right trajectories, each eye's stereo/IMU scale reports and factory
extrinsic, calibrated UMI IMU, and cached VINS body poses. Neither eye is the
primary trajectory. Lighthouse/SteamVR is absent from its input API.

- Both eyes contribute relative-motion factors to the same body-position graph.
- Each eye is independently checked against its own metric stereo motion.
  The frozen 8 mm Huber scale is used identically for both eyes.
- Camera-origin motion is converted to body-origin motion with that eye's
  rotating lever arm. Local motion is expressed in a neutral VINS body world
  through body orientations, not a trajectory-shape fit to an external target.
- Duplicate reports from the same eye/pair cannot increase that eye's vote.
  Paired-eye total confidence is bounded by the maximum individual confidence,
  rather than doubling correlated D405 evidence. Stereo rows are deduplicated.
- VINS provides the common time/world gauge, body orientation and short relative
  motion. Raw calibrated IMU constrains velocity, gravity and accelerometer bias.
- In the opt-in body graph, absolute visual position and correction-smoothness
  priors are disabled. A first-node gauge remains; no eye is an absolute anchor.
  Every output frame is a position state. No new shared/global scale state is
  estimated: both eye products are independently metricized first.
- The old stationary guard needs two independent odometries. It is disabled in
  this experimental path rather than count the VINS initializer twice as a
  false two-source consensus. Production/default behavior is unchanged.

This is not averaging two finished trajectories. Missing/weak local eye factors
are absent/downweighted; both reliable eyes contribute. Both eyes can also be
wrong together, so there is no guarantee of improvement or 10 mm accuracy.

The output is explicitly `body_trajectory_fused.csv`; do not convert its lever
arm again. Manifest/report status always stays `EXPERIMENTAL_NOT_ACCEPTED`.
Score frozen output separately with `score_steamvr_slam.py` (body-frame contract).

## Time/calibration boundary repairs

The older left stereo report lacks `right_rotation_from_left`; the derived
right report adds it. All other factory fields must match exactly, and explicit
right rotations must be valid and agree when present in both reports. A schema
addition is not a changed physical calibration.

VINS times are bound to actual recorded camera frames within 10 ms, with no
extrapolation. The binding report preserves raw/bound counts and excluded times.
Take 2's 0.238 microsecond serialization discrepancy is snapped to the real
camera timestamp. Take 4 contains one VINS sample 33 ms after camera capture,
so that non-observed tail sample is explicitly excluded. No frame is selected
or rejected using its external-reference error. All three final comparisons
have 1,143 matched camera timestamps and the same frozen reference manifest.
Docker2 remains `td=-0.009109323`, `estimate_td=0`, no extra IMU replay shift.

## Frozen three-recording results

Cached inputs and policy are identical across the three runs; no VINS/frontend
reruns or per-take parameter selection. Translation ATE uses SE(3), no scale fit.

| Recording | Left-only full-correction max (mm) | Symmetric mean (mm) | Symmetric P95 (mm) | Symmetric max (mm) | Within 10 mm | Scorer |
| --- | --- | --- | --- | --- | --- | --- |
| Take 2 retry | 7.019 | 3.007 | 5.398 | 8.086 | 100% | PASS |
| Take 4 | 11.073 | 2.764 | 4.474 | 5.826 | 100% | PASS |
| Take 6 | 12.973 | 3.290 | 5.869 | 15.352 | 99.475% | FAIL: maximum |

Take 4 opens a previously failing result, but take 2 regresses within the limit
and take 6's maximum worsens. Do not claim all-frame 10 mm or production readiness.
This is an architectural comparison: the shared body graph also removes the
left shape prior and bypasses the old downstream scale/smoothing stages, so
these scores do not isolate the effect of adding a right-motion factor alone.

Evidence: `.planning/frontend_observation_20260930/dual_ir_symmetric_graph_20261002_v2/take{2,4,6}`:

- `candidate_manifest.json`: input/code hashes, gauge, time binding and status.
- `local_motion_factors.json`: eye, target, confidence and own stereo residual.
- `shared_stereo_observations.json`, `graph_report.json`: solve evidence.
- `diagnostic_score/precision.json`, `precision.md`, `precision.png`: separate scoring.

Earlier `dual_ir_symmetric_graph_20261002_v1` is diagnostic only: takes 2/4
failed timestamp binding before solve; take 6 predates the time/guard repairs.

## Remaining take-6 issue

Post-freeze diagnosis identifies six over-limit samples, indices 1025–1030;
peak index 1030 is 34.330 s after the output begins, at 15.352 mm.
At the peak, both eyes have 43 spanning motion proposals. Their own stereo
residual medians are 7.630 mm (left) and 19.001 mm (right); confidence medians
are 0.2533 and 0.0204. Right is therefore not a uniformly good rescue source
there, although some very short right windows are better than corresponding
left windows. These are post-hoc diagnostics, not supervision or selection.
They do not yet establish whether the remaining error is solely visual shape,
stereo measurement, orientation transport or relative-motion regularization.
Do not tune per-frame trust from external-reference errors.

## Verification

109 targeted tests pass, including the shared solver recovering alternating
eye-local gaps, swap invariance, arbitrary independent worlds and lever-arm
cancellation, same-eye duplicate immunity, confidence limits, no initializer
shape lock, no false stationary consensus, timestamp/schema contracts, and a
mocked wrapper fixture proving explicit body output and no automatic acceptance.
Python compilation and diff whitespace checks pass.

The production wrapper is unchanged. Default left-only graph regression is
replayed separately under `.../dual_ir_symmetric_graph_20261002_v2/control_left_only`.
It is not byte-identical to the saved earlier control: maximum position delta
is 3.606e-9 m and maximum rotation delta 6.237e-9 rad. This is numerical
agreement at the serialized floating-point precision, not a bitwise claim.
All numerical evidence above remains diagnostic; more independent recordings
and a full frontend-level eye-swap/ablation study are still needed before release.
