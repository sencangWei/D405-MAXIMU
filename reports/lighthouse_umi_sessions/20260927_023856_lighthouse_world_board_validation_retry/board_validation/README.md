# Fixed-board validation, 2026-09-27

Decision: joint-world/four-way candidate improves this independent observation, but is NOT ACCEPTED as a sub-10 mm reference. No production library, active world, external calibration, timing value or SLAM output was changed.

The prior attempt `20260927_010313_lighthouse_world_board_validation` failed because no Tracker poses arrived before readiness. Its camera-only recording was preserved. After the operator reported Tracker ready, a five-second private preflight received 630 poses without jump flags. The subsequent capture has 30 seconds of D405 stereo/RGB/400 Hz IMU and a 60-second Tracker tail-inclusive record. Camera/IMU acquisition and DB3 move passed. Tracker live integrity failed seven position-jump flags (max45.637mm) and the same frozen-to-received OOTX up delta0.606486deg. This combined session is diagnostic, not a capture-quality PASS.

## Independent fixed-board comparison

Board: fixed original6x6 AprilGrid, tagSize35.2mm, spacing ratio0.3; print-size correctness is assumed from the supplied board configuration. Stereo board extraction yields893/899 poses, reprojection RMSE median0.249px/P950.338px. Primary comparison uses876 common samples and a max30ms interpolation gap. No residual-based frame deletion or trajectory deformation was performed.

Frozen external mapping: September11 round4 `tracker_T_d405_left_camera`, SHA256382517ecf9255aa24bdd54b72b383632dc580ffd5a47c6d77aa7dee1dab76617; frozen camera-to-Tracker query offset+13.371281ms. Each mode fits only one global SE(3) frame alignment, without scale, hand-eye or timing optimization. The known multi-mm uncertainty in that frozen mapping limits absolute conclusions.

| Mode | Mean / median / RMSE mm | P95 mm | Maximum mm | Fraction below10mm |
|---|---|---:|---:|---:|
| Installed live, formal world | 18.53 /11.94 /25.60 |53.86|100.36|44.06%|
| Source candidate, formal world + four-way |20.18 /12.74 /27.46|63.19|111.36|42.58%|
| Source candidate, joint world + four-way |5.05 /3.94 /6.62|12.09|34.63|93.04%|

Fit only the first half's frame alignment and evaluate the second half: joint candidate P9513.51mm/max16.57mm. Whole-trajectory fit therefore must not be used to claim generalization or calibration acceptance.

Raw-record/host time mapping was recovered by7512 unambiguous original pose callbacks, with matched-clock absolute residual P99302.2us. Camera epoch/monotonic mapping span0.954us. Replay timestamps reuse the input record clock; the mapping is not fitted against board or SLAM position errors.

## Geometry, incomplete observations and filtering

Replays use the corrected calibration-loading/residual-recording runner. All accepted gated solves contain four station-axis groups and two stations. On this input formal-world raw jump flags608->164 and final flags5->2 with gate; joint-world raw flags175->11 and final flags0->0. These flags detect20mm/10deg adjacent-record discontinuities within20ms, not absolute accuracy.

The main joint filtered residual peak is at11.6259s. Its bracketing raw optical solutions are at11.5864/11.6464s (60.008ms gap). At the frozen query time the latest optical solution is52.891ms old. Six ungated solutions inside this interval all use only three station-axis groups. The gate rejects incomplete observations as intended; the filtered prediction during that gap is associated with the34.634mm residual peak. This identifies a prediction/optical-availability boundary needing further work, not proof that one filter setting is the sole cause.

On803 common points with raw optical interpolation available, raw optical and filtered poses use the same global SE(3) alignment fitted to the filtered positions. Raw EXTERNAL_POSE is first converted to the final POSE frame using the runtime floor-offset (-0.113739259541m), as required by libsurvive's separate recording hooks. Raw optical P95/max is9.923/20.245mm versus filtered10.238/31.934mm. These803 points must not replace the full876-point acceptance set, because gated optical gaps omit exactly some difficult prediction periods.

## Independent timing check

UMI gyro angular-speed correlations: live formal0.400, replay formal-four-way0.455, joint-four-way0.891, board-camera0.986. The original live/formal signals fail the existing correlation/outlier gates, so their apparent new offsets are unusable. Joint and board signals pass the single-record signal checks, but have no independent repeat yet.

The gyro-only difference between Tracker/IMU and board-camera/IMU offsets suggests camera-to-Tracker query+9.462879ms. With the same frozen external mapping and all876 points, this diagnostic gives mean5.032mm/P9511.777mm/max33.027mm/93.15% below10mm. It is not promoted to production. The small maximum improvement34.63->33.03mm rules out this constant timing adjustment as a sufficient remedy.

## Artifacts and next boundary

`fixed_board_comparison.json` contains metrics/provenance; `fixed_board_comparison.png` plots the primary residuals; `compare_fixed_board.py` reproduces the analysis using preserved raw inputs. `replay/` contains four A/B solves, support audits and logs. The board comparison uses no VINS/MASt3R positions or robot supervision.

Further offline work should diagnose prediction during verified optical gaps with Tracker's own sensors and retain independent board validation. Physical station movement is not established by frozen pose/config deltas. No acceptance/deployment and no new live recording is implied by this report.
