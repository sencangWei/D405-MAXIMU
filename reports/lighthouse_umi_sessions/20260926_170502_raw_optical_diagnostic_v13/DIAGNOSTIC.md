# Raw optical diagnostic — 2026-09-26

Diagnostic recording only; NOT an accepted calibration or SLAM accuracy test.
No production time offset, external transform or SLAM parameters changed.

## Capture

- D405 session: `/home/robot/umi_ego_vio_data_device2_c48df736/recordings/d405_720p_rgb_stereo_ir_20260926_170504`.
- Camera/UMI capture passed; Tracker integrity failed with translation and angular pose jumps. The capture wrapper returned failure before generating its normal accepted manifest. Raw inputs remain preserved.
- AprilGrid: 1199/1199 poses, reprojection median 0.231 px / P95 0.304 px. Camera clock fit 29.999 fps / 0.038 ms.
- Tracker: 9387 samples, 70.005 s; maximum position step 125.555 mm; maximum angular step 34.061 degrees. Frozen/runtime world-pose comparison passed; this does not prove physical station geometry or absolute accuracy.
- `lighthouse_raw.rec`: 9,919,879 bytes, final newline present. WM0 raw event counts: IMU `i` 17616; optical `Y` 6974, `W` 72975, `B` 72975, `C` 4; recorded poses 9392.
- Raw SHA256: `a4c6749713615af08414c91463e484f02836adf323135dc3c7d7998a4a77f20f`.

## Independent corroboration

Camera times mapped to host monotonic using sensor-event wall/monotonic pairs. Raw UMI gyro integrated in its own body frame with no fitted bias and no extra time shift. This is diagnostic corroboration, not calibrated absolute ground truth. Camera spans below deliberately bracket the Tracker event and are longer than Tracker steps; they are not same-rate position errors. Camera and Tracker origins differ.

| Camera elapsed s | Tracker dt ms | Tracker step mm | Tracker angle deg | UMI gyro angle deg | Board span ms | Camera step mm | Camera angle deg |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 33.828 | 10.190 | 125.555 | 2.155 | 0.021 | 66.665 | 5.420 | 0.107 |
| 31.446 | 10.042 | 63.801 | 0.711 | 0.096 | 33.334 | 3.933 | 0.321 |
| 29.639 | 11.998 | 52.502 | 8.504 | 0.115 | 33.335 | 4.244 | 0.326 |
| 29.208 | 5.961 | 49.852 | 20.887 | 0.075 | 33.335 | 4.639 | 0.413 |
| 30.394 | 10.225 | 48.403 | 11.234 | 0.022 | 33.338 | 1.123 | 0.082 |

## Raw-observation recomputation

Replay exited 0 using a private copy of frozen v13, calibration disabled, `--playback-factor 0 --playback-replay-pose 0 --record ""`. Calculated poses were recomputed, not replayed from recorded POSE lines. Saved as `playback_tracker.csv` and `.log`.

- Recomputed WM0 output: 8402 samples; maximum position step 74.902 mm; maximum angular step 29.409 degrees. Thus severe discontinuities also occur offline without SLAM input.
- Fast replay emits fewer simple-interface samples; maximum sample gap 0.280 s. Its device times are relative replay times whereas live device times are epoch-based. Do NOT compare host reception times or directly subtract these device timestamps. This replay does not establish event-by-event equality, nor that the offline 74.902 mm step is the same physical instant as the online 125.555 mm step.
- Log reports MPFIT seed runs 1/6928 and error failures 0. Solver success does not mean its poses are accurate.

### Per-lighthouse diagnostic ablation

Same raw input and private frozen-v13 copies; add `--disable-lighthouse 0` or `1`, retaining recomputation and calibration-disabled flags. Both exited 0. No single-station setting applied to production.

| Replay | WM0 samples | Max sample gap s | Max position step mm (dt ≤20 ms) | Max angle step deg (dt ≤20 ms) | Steps >20 mm / >10 deg (dt ≤20 ms) |
| --- | --- | --- | --- | --- | --- |
| Both stations | 8402 | 0.280 | 74.902 | 29.409 | 23 / 10 |
| Disable LH0 | 3153 | 0.476 | 5.459 | 6.683 | 0 / 0 |
| Disable LH1 | 3514 | 0.364 | 4.984 | 7.630 | 0 / 0 |

Single-station outputs have fewer samples and larger gaps; smoothness does not prove correctness. Interpolating only second-stream gaps <30 ms gives 2876 shared replay-time samples: position disagreement median/P95/max 34.036/37.360/40.468 mm, orientation disagreement 173.707/173.792/174.796 degrees. These are differences between solver solutions, NOT absolute accuracy or a proven 174-degree base-placement error. Single-station observability/initialization ambiguity is unresolved. The ablation specifically prioritizes dual-station constraint conflict / alternative pose solutions for investigation; it does not establish which station is wrong or exclude occlusion/reflections.

## Conclusion and next boundary

### Follow-up: actual optical fit residuals

Diagnostic output `residual_replay.tluBUa/` uses the built-in `--mpfit-record-reprojection-error 1`, recording `WM0 RA sensor axis angular_residual_rad lighthouse`. Original capture unchanged; every replay uses private configuration copies and recomputes poses. Both-station run additionally logs verbose solver statistics. Three replays exited 0.

| Constraints enabled | LH0 abs angular residual median / P95 deg | LH1 abs angular residual median / P95 deg |
| --- | --- | --- |
| Both | 0.198 / 0.539 | 0.408 / 0.703 |
| LH0 only | 0.011 / 0.047 | — |
| LH1 only | — | 0.008 / 0.030 |

Dual-station residuals are already large in early low-motion observations, not just isolated movement spikes. Each individual station fits much more closely, while their combined constraints conflict under the current fixed world/model. Single-station constraints are weaker and sample subsets differ: this does not prove which physical station moved, guarantee single-station accuracy, or identify relative world calibration as the sole possible cause. Prioritize relative station geometry, identity/channel/disambiguation and alternative pose solutions before changing hand-eye or SLAM. The frozen file is libsurvive's custom format despite its `.json` suffix; ordinary `json.loads` is invalid. Existing config parser must be used.

Strong evidence of Tracker dynamic-reference inconsistency, independently corroborated by board/UMI observations, reproducible in raw-observation recomputation. This is not a MASt3R/VINS output defect. It is not evidence that the user moved a station, nor proof of reflection, occlusion, or a particular libsurvive code bug.

Next: inspect dual-station measurement consistency, pose-solution ambiguity, and optical visibility/inlier residuals around the recorded events using these saved observations. Do not recollect the same motion, fit away discontinuities with hand-eye calibration, or use this rejected reference to tune SLAM to 10 mm.

## Code verification

Optional raw recording now available with `LIGHTHOUSE_RAW_RECORD=1` in the capture wrapper and `record --raw-record PATH` in the Tracker helper. Existing raw files are refused rather than overwritten; default recording behavior unchanged.

`python3 -m pytest -q tests/test_lighthouse_reference_check.py tests/test_lighthouse_raw_record.py tests/test_lighthouse_capture_orchestration.py`: 27 passed. Python compile and shell syntax checks passed. Standalone `pytest` was not a valid launcher here (repository import path missing); module invocation above passed.
