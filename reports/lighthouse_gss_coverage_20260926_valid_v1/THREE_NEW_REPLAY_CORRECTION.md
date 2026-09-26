# Three independent captures: corrected four-way replay

## Corrected replay bug

The original `three_new_validation/` is invalid. Its runner passed `--config`, but libsurvive's option is `configfile` (`-c`). Recorded `OPTION configfile` showed a fallback `validation.rec.json`, and no frozen station poses were loaded at startup. The third take's zero output was a replay configuration fault, not evidence that its sensor recording was unusable. The runner also omitted `mpfit-record-reprojection-error`, so its zero support statistics were missing diagnostics.

`validate_three_new.py` now uses `-c`, enables residual recording, and rejects comparisons if the recorded config option, startup station identities, calibration-disable flag, gate settings, optical solves, residual events, or accepted four-way support do not match expectations. The option recorder truncates strings to 127 characters; this logging limit is accounted for and startup station poses are checked independently. All 12 corrected replays passed these checks. The raw recordings were reused, without live hardware access or installing a candidate.

## Results on independent inputs

Worlds were held fixed. The joint candidate was fit before these three captures; these recordings did not fit its world geometry. Each row compares the same binary and world with gate disabled/enabled (`min-lighthouse-count=2`, minimum one observation per station-axis, original `required-meas=8`).

| Take | World | Raw optical solves, no gate / four-way | Raw jump flags, no gate / four-way | Final pose jump flags, no gate / four-way | Final callbacks, no gate / four-way |
|---|---|---:|---:|---:|---:|
| gP8kSJ | Formal | 6052 / 5917 | 230 / 106 | 4 / 2 | 7632 / 7632 |
| ucf7GS | Formal | 6045 / 5927 | 198 / 83 | 12 / 5 | 7557 / 7557 |
| rAt98l | Formal | 6052 / 6013 | 44 / 12 | 1 / 0 | 7517 / 7515 |
| gP8kSJ | Joint candidate | 6052 / 5917 | 42 / 1 | 0 / 0 | 7633 / 7633 |
| ucf7GS | Joint candidate | 6045 / 5927 | 85 / 19 | 0 / 0 | 7557 / 7557 |
| rAt98l | Joint candidate | 6052 / 6013 | 10 / 6 | 0 / 0 | 7517 / 7515 |

Every saved four-way optical solve had four station-axis groups and two stations. The gate therefore enforces the requested coverage and reduces discontinuities across all three inputs, but cannot eliminate jumps whose observations already satisfy coverage. Callback count retention is at least 99.97% relative to the same-world no-gate replay; it is not a claim about gap-free coverage.

The diagnostic flags an adjacent record-time pose step >20 mm or rotation >10 degrees when the interval is <=20 ms. It does not measure absolute accuracy, and natural fast motion can also trigger it. Original live integrity failures additionally use speed thresholds, so those counts are not directly comparable to this table.

## Independent optical residual evidence

P95 absolute reprojection-angle residuals in degrees, axes ordered LH0/X, LH0/Y, LH1/X, LH1/Y, both modes with four-way gate:

| Take | Formal world | Joint candidate |
|---|---|---|
| gP8kSJ | 0.2940, 0.5095, 0.7764, 0.3405 | 0.2209, 0.1741, 0.1391, 0.1387 |
| ucf7GS | 0.2252, 0.4636, 0.6747, 0.2511 | 0.1599, 0.1526, 0.1278, 0.1334 |
| rAt98l | 0.2036, 0.4455, 0.7464, 0.2084 | 0.1648, 0.1461, 0.1107, 0.1224 |

All four axes improve on all three independent captures. The formal world also has persistent signed bias (for example LH1/X median +0.5414 degrees in gP8kSJ, versus -0.0506 degrees for the candidate). This supports a world/optical model mismatch in addition to missing-axis solves. It does not identify whether physical station movement or model bias caused that mismatch, and object poses were still optimized for each input.

Remaining candidate raw flags (1/19/6) all have four-way support. In ucf7GS the largest flagged raw step is 25.289 mm / 15.798 degrees over 17.907 ms. Coverage alone cannot diagnose these residual flags. Zero final flags can also reflect the estimator filter and is not proof of a sub-10 mm reference.

## OOTX metadata comparison

The frozen file stores station 9d67e4bd accel `[1,127,34]`. All three capture-exit private configs store `[0,127,33]`; the other station remains `[-2,127,24]`. This yields the same 0.606486-degree frozen-to-current difference in every take, while station pose values remain frozen within serialization precision.

`lighthouse_reference_check.py` compares the saved config before process launch with the persisted config after exit. That comparison cannot date a change within the recording. The identical new metadata across three captures supports stable currently received metadata, but cannot prove the physical station has not shifted relative to the saved world. The existing quality failure was retained; no threshold was relaxed or faulty pose replaced.

## Outstanding acceptance

- The source candidate and installed library have different build hashes. Verify against the exact runtime build before deploying the patch.
- Validate the joint candidate's absolute geometry with independent stationary/multi-position or fixed-board observations; continuity and reprojection residuals alone do not establish 10 mm accuracy.
- No installed library, active world, Tracker–UMI external transform, or SLAM trajectory was changed.

Evidence: `three_new_validation_v2/summary.json` plus each child `audit.json`, `validation.rec`, and `replay.log`. Invalid first outputs are preserved separately.
