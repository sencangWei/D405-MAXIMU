# Dual-Lighthouse conflict localization — diagnostic only

User approved offline investigation. No live hardware capture, production library/configuration, Tracker–UMI external transform, time offset or SLAM changes. All candidates remain inactive.

## Evidence

- Channel3 consistently maps toID2640831677(9d67e4bd), channel2 toID1850292303(6e49384f) in diagnostic/fresh raw and recomputed outputs. Runtime indexes0/1 are not optical channels3/2. No identity interchange observed; upstream decoding errors absent from metadata are not excluded.
- Quaternion norms approximately0.9999992–1.0000008. Audit uses abs(normalized dot), so q/−q is not a rotation jump.
- `driver_playback.c:263–280` only emits saved LH_POSE as external reference when replay-pose enabled, not as installed world calibration. All tests use replay-pose0. Saved-pose world injection is not supported.
- Diagnostic old-world residuals have signed median>0.1deg on39/45 station-axis-sensor groups; fresh candidate leaves22/45. Four old-world station-axis signed medians .288/.153/.293/.512deg; bias already exists in0–2s. Not just a handful of bad frames. This does not uniquely distinguish geometry, optical model, initialization or visibility effects.

## Paired pre-/post-Kalman replay

Three bounded30s replays exited0, installed wrapper/calibration disabled, private configs, flags: `--lighthouse-gen 2 --playback RAW --playback-factor 0 --playback-replay-pose 0 --show-raw-obs 1 --mpfit-record-reprojection-error 1 --record NEW/recomputed.rec -v 10`.

| Input / fixed world | Pre-filter discontinuity flags | Final callback flags |
| --- | ---: | ---: |
| Diagnostic / frozenv13 | 644 | 28 |
| Diagnostic / fresh candidate | 199 | 3 |
| Fresh / fresh candidate | 2 | 0 |

Flag: adjacent record-time0≤dt≤20ms with position step>20mm OR quaternion geodesic step>10deg. These are diagnostic flags, NOT ATE, physical velocity, or SLAM accuracy gates. Multiple solves may share an input timestamp. Optical bad solutions exist before temporal fusion; Kalman attenuates but does not reliably remove them.

Raw old-world maximum flagged translation288.951mm; new-world108.201mm. New-world final events at record times27.015518s(14.081deg),35.473879s(24.174mm),36.211848s(23.009mm). Fresh raw events36.802/29.122mm around48.16s, final0flags. Continuity does not certify absolute accuracy.

Sources: raw solver observations `survive_kalman_tracker.c:1165–1177`; final conversion/recording `survive_process.c:24–49`; record clock `survive_recording.c:95`; playback clock `driver_playback.c:63`. Raw and final use head-frame orientation; final also subtracts constant floor translation, which cannot create adjacent-step jumps.

## Correction to prior single-station conclusion

### Constraint-support association

Each contiguous RA block is attributed to the following raw optical observation. For the diagnostic fresh-world replay, flagged solves have median19unique angular measurements and3station-axis combinations, versus29and4for other solves. Both median station counts are2: seeing two stations is not the same as having all four station-axis constraints. Baseline flagged/other medians20/29; fresh flagged/other18.5/31(two flagged samples only). Association does not prove causality or excuse the persistent signed bias. It motivates checking observability/conditioning before allowing a weak optical solve to override the Tracker's inertial prediction; merely discarding such updates is not an absolute-accuracy fix.

`cn_add_diag` third argument is a multiplicative scale(`libs/cnmatrix/include/cnmatrix/cn_matrix.h:313`), NOT a matrix offset. Therefore the previously unverified offset interpretation of calls with5/1 is unsupported and must not motivate a covariance patch.

Complete existing single-LH0 recording contains3flagged final callbacks(214.288/25.077/100.445mm) omitted by asynchronous CSV; single-LH1 recording0. Recorded POSE is final output, not raw solver. Prior statement that both single-station outputs are continuous was too strong. Neither station is certified as an accurate fallback. Their earlier~174deg solution disagreement is an ambiguity clue, not a measured station mounting rotation.

## Reproduction

`audit_optics.py` streams records, records input hashes/config payload hashes/channel-ID evidence, groups signed optical residuals by station/axis/sensor/2s bin, and separately audits raw/final callbacks. It does not fit, replace or delete poses. `optical_audit.json` covers seven source/recomputed records; original raw files intact. Device CONFIG payload hashes match across all seven.

`python3 -m pytest -q reports/lighthouse_dual_conflict_20260926/test_audit_optics.py`:6passed. Tests: channel/index distinction, quaternion sign, separate streams, long-gap exclusion, same-time discontinuity preservation and per-solve support attribution. Syntax compile passed.

## Next boundary

Focus on relative world geometry and branch/optical consistency: inspect constraints supporting alternative solutions; jointly solve only records demonstrated from the same layout and reserve independent validation records. No proof a station physically moved or covariance routing is broken. No gate relaxation, GT interpolation or Lighthouse fitting with UMI/robot supervision. Specify missing spatial/angular coverage before any supplemental operator-ready capture.

This slice localizes faulty observations before temporal fusion; world calibration is NOT repaired. Candidate remains FAIL_INDEPENDENT_VALIDATION. Remote backup must be verified separately; network was blocked in preceding turn.
