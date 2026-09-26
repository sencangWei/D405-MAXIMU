# Offline Lighthouse world repair — candidate NOT ACTIVE

User authorized candidate-only offline recalibration on 2026-09-26. No live USB capture, production configuration, camera/IMU time offset, hand-eye transform or SLAM algorithm changed.

## Inputs and identity

- Baseline frozen world: `../lighthouse_recalibration_world_v13_20260926_consensus/libsurvive_config_frozen_v13.json`, SHA256 `8b2f50ed5bf51ee4366d155dd99983a7a2791b1de4b7995d375d5036b94668d0`.
- Current diagnostic raw: `../lighthouse_umi_sessions/20260926_170502_raw_optical_diagnostic_v13/lighthouse_raw.rec`, SHA256 `a4c6749713615af08414c91463e484f02836adf323135dc3c7d7998a4a77f20f`.
- Earlier coverage raw: `../lighthouse_recalibration_v13_20260926.MOdFGn/coverage_round2/lighthouse_raw.rec`, SHA256 `d45dfa1440731a850d439b34470a52f9272dfdb5833b0b633a5a4c5492a549f3`.
- Tracker LHR-A2A59C7D; station IDs 2640831677 / 1850292303. WM0 CONFIG payload identical in both raw inputs: SHA256 `0aac20cd6a36626b216979dcbdfcbe7487ebdbf8bdef6e57c318f718589650c8`, 8059 bytes. This rules out differing recorded Tracker geometry/IMU device descriptions, not physical mount/base movement or optical interference.

## Reproduction

Copy baseline into a fresh private `candidate.json`. Run binary directly because the usual wrapper ALWAYS adds `--disable-calibrate`:

```bash
env LD_LIBRARY_PATH=/home/robot/.local/lib:/usr/lib/x86_64-linux-gnu \
 /home/robot/.local/bin/vive_pose_stream.bin -c /ABS/PRIVATE/candidate.json \
 --force-calibrate --lighthouse-gen 2 --playback /ABS/diagnostic/lighthouse_raw.rec \
 --playback-factor 0 --playback-replay-pose 0 --record "" -v 10
```

Calibration exited 0, genuinely enabled (log confirms force clears positions and GSS solves). Four scenes, 124 measurements, final reported optical fit RMS 0.0002627 rad. Coverage bins concentrated; scenes mostly repeat two static positions. Candidate relative station geometry differs from baseline by 25.022 mm / 1.330 degrees; station separation 1773.172 mm versus 1759.141 mm. These are model differences, not proof of physical station displacement.

For frozen baseline/candidate evaluation, always copy into a NEW private runtime config and use wrapper with `--playback-replay-pose 0 --playback-factor 0 --mpfit-record-reprojection-error 1 --record /ABS/NEW/recomputed.rec`. All four evaluations exited 0; measurements use replay device times, not fast wall reception times.

## A/B results

| Raw input / fixed world | LH0 optical P95 deg | LH1 optical P95 deg | Max position step mm, dt ≤20ms | Max angle step deg, dt ≤20ms | >20mm / >10deg short-step counts |
| --- | --- | --- | --- | --- | --- |
| Current diagnostic / baseline | 0.539 | 0.703 | 72.048 | 29.409 | 23 / 10 |
| Current diagnostic / candidate | 0.065 | 0.057 | 2.738 | 0.788 | 0 / 0 |
| Earlier coverage / baseline | 0.186 | 0.191 | 7.636 | 1.630 | 0 / 0 |
| Earlier coverage / candidate | 0.462 | 0.482 | 92.613 | 22.130 | 15 / 14 |

See `offline_metrics.json`. Fast simple-interface output sample counts/gaps differ between runs; maxima are diagnostic selected samples, NOT same-rate guaranteed maxima, absolute ATE, or loss rates. All optical residual samples come directly from recomputed solver observations, independent of output reader sampling.

The candidate resolves the current record's dual-station conflicts but DOES NOT generalize to the earlier record. Therefore do not combine these two as a proved fixed-layout evaluation corpus. The old world fits old observations better and the new world fits new observations better. This does NOT distinguish physical station changes from calibration/model/observation bias.

## Temporal split check

Recalibrate early-only with `--playback-time 18`; late-only with `--playback-start-time 40`. Two early scenes versus three late scenes. Relative geometry difference 6.319 mm / 0.1405 degrees; separation 1773.172 vs 1768.925 mm. Full candidate's serialized relative geometry is exactly the early candidate's geometry, even though full log reports four-scene refined solutions. Source routes global observations through a Lighthouse Kalman/filter and a separate serialized state, so final `Solved scene` log text must NOT be assumed identical to frozen config. Further investigation needed; no library patch made on this unproven mechanism.

Current record is not an independent test of its own calibration, though its moving interval is outside the stationary calibration scenes. Only two complete raw optical recordings were found; the two earlier independent hand-eye rounds lack raw optics and cannot be recomputed through libsurvive. The promised two independent same-layout raw validation rounds are therefore NOT yet available.

## Decision and next action

Status: FAIL_CROSS_RECORD_VALIDATION / CANDIDATE_INACTIVE; current diagnostic-only improvement demonstrated.

Need operator-ready fresh broad-coverage calibration under the CURRENT physical arrangement: several distinct positions, orientations, and brief stationary dwells to let the scene collector acquire independent observations. Then two independent motion validations; no repeat hand-eye fit unless rigid mounting changed. Inspect optical residual consistency before accepting static jitter or reporting SLAM accuracy. If optical conflict persists with adequately covered same-layout captures, debug solver/model/disambiguation; do not keep recalibrating blindly.

Raw inputs and large replay event outputs remain local, not included in code/evidence git backup. Candidate remains a historical, inactive diagnostic artifact.
