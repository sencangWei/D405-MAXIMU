# Independent Tracker–D405/body recheck, 2026-09-26

Decision: **do not apply either new external calibration**. No SLAM trajectory or Lighthouse ATE was used to fit these candidates. Existing production extrinsics remain unchanged.

Both captures used the frozen Lighthouse v13 world, original 6×6 AprilGrid (tagSize 35.2 mm), Docker2 formal body-camera calibration, IR preview and 40 s motion guidance. Both raw capture/integrity/overlap checks passed. Tracker↔UMI query offset is separate from the fixed Docker2 camera↔IMU td (-0.009109323 s).

| Measurement | Round 1 (16:37:31) | Round 2 (16:43:25) |
| --- | ---: | ---: |
| Accepted board poses / IR frames | 1116 / 1200 | 1199 / 1199 |
| Board reprojection P95, pixels | 0.390 | 0.353 |
| Independent IMU–Tracker query offset, ms | +4.269933 | +5.666260 |
| Angular-speed correlation | 0.981947 | 0.961297 |
| AX=XB combined residual median, mm | 1.838860 | 1.818200 |
| AX=XB combined residual P95, mm | 5.794231 | 6.355980 |
| AX=XB combined residual max, mm | 15.561776 | 16.691510 |
| Split-half translation difference, mm | 8.490186 | 7.825970 |
| Split-half rotation difference, degrees | 0.946535 | 3.227077 |
| Existing single-capture result | PASS_CANDIDATE | DIAGNOSTIC_CANDIDATE |

The two full-capture transforms differ by **2.667167 mm / 0.579664 degrees**. Existing split-half gates permit up to 10 mm / 2 degrees; round 2 fails the rotation gate. A single-capture PASS is not proof of accurate or repeatable external calibration.

The existing multi-capture timing script reports PASS_CANDIDATE: offset spread **1.396327 ms**, below its pre-existing 2 ms threshold. Median query offset is +4.968097 ms. This is a candidate, not an applied production setting. The single-capture repeat_spread_ms=0 field does not establish repeatability.

Diagnostic refit of both captures at that same independently estimated +4.968097 ms offset leaves split-half disagreement at **8.491964 mm / 0.972622 degrees** (round 1) and **8.068492 mm / 3.208104 degrees** (round 2). Thus the measured inter-capture timing difference alone does not explain the instability. No offset was chosen by external SLAM accuracy.

Both complete captures and both halves pass the solver's motion-observability tests. Round 2 board detection is complete with low image reprojection error. Neither missing board detections nor failure of those observability gates explains its rejection. Low reprojection error does not prove absolute camera pose accuracy; pose depth/calibration bias, Lighthouse continuous pose error, and solver/model sensitivity remain unresolved. Do not claim a physical base-station movement or loose rigid mount from this result alone.

Next diagnostic work should compare independently measured board rotational increments with IMU and Tracker, and examine time-local/board-depth residuals before requesting another identical capture or changing SLAM parameters. A production external update requires independent repeatability and held-out validation, not whichever candidate minimizes SLAM ATE.

## Evidence

- Round 1: `../../20260926_163714_independent_handeye_v13_round1/calibration/lighthouse_d405_aprilgrid_calibration.json`
- Round 2: `lighthouse_d405_aprilgrid_calibration.json`
- Two-capture timing: `two_round_time_offset.json`
- Capture manifests: each round's parent directory `capture_manifest.json`
- Raw D405 sessions: `/home/robot/umi_ego_vio_data_device2_c48df736/recordings/d405_720p_rgb_stereo_ir_20260926_163731` and `..._20260926_164325`

These are calibration-fit consistency measurements, **not SLAM ATE and not a direct absolute-accuracy uncertainty bound**.

## Continued cross-sensor diagnosis

The ordinary stream/integrity PASS did not establish dynamic accuracy. The pre-existing position-step gate detects no candidate steps in round 1 but **four candidates in round 2**, and rejects round 2 (maximum potentially affected camera-frame fraction 49.0%). These fractions are the gate's conservative suffix counts, not proof that every subsequent frame is corrupted.

| Relative time from first supported board sample, s | Tracker step, mm | Interval, ms | Tracker rotation, deg | Raw UMI gyro integrated rotation, deg |
| --- | ---: | ---: | ---: | ---: |
| 19.786 | 4.787 | 8.154 | 0.770 | 0.071 |
| 23.440 | 4.987 | 10.116 | 0.261 | 0.108 |
| 28.356 | 4.036 | 6.034 | 0.790 | 0.021 |
| 37.994 | 4.172 | 9.523 | 0.566 | 0.279 |

Gyro queries in this table use the independently estimated +5.666 ms Tracker query offset. At the 28.356 s event, the board-vs-Tracker body-position residual changes by 5.305 mm in +/-0.10 s windows, and 5.460 mm in +/-0.25 s windows (exclude +/-0.02 s around the event). This provides cross-sensor evidence beyond the gate's step heuristic. Other events have different window-dependent changes and should not all be described as identical permanent offsets.

`audit_lighthouse_handeye_rotation.py` reproduces the existing solver's exact supported/active half boundaries rather than splitting at an arbitrary 25 s. At 0.2/0.5/1.0/1.5 s horizons the independently fitted half-to-half sensor-axis mappings differ:

| Capture | Board↔gyro mapping change, deg | Tracker↔gyro mapping change, deg |
| --- | --- | --- |
| Round 1 | 0.136 / 0.129 / 0.280 / 0.330 | 0.890 / 0.906 / 1.387 / 0.788 |
| Round 2 | 0.350 / 0.354 / 0.361 / 0.405 | 3.001 / 5.089 / 7.654 / 7.252 |

At 0.2 s, round 2 vector-residual P95 is 0.144 / 0.148 deg for board↔gyro versus 0.833 / 0.781 deg for Tracker↔gyro (first / second halves). These mapping changes are **fit consistency measures, not instantaneous absolute Tracker attitude error**. Sensor axes are fitted by one constant rotation per half; no per-frame pose correction or SLAM input is used. Gyro is uncalibrated raw angular rate integrated in the body frame, without bias fitting, so it is not absolute ground truth. Its agreement with the independent board measurement is the cross-check.

The same audit with fixed formal camera↔IMU shift -9.109323 ms still shows round 2 board mapping changes 0.479–0.745 deg versus Tracker 3.298–7.399 deg. This sensitivity check does **not** authorize modifying the formal VINS td. The single-capture camera↔gyro timing correlation in round 1 is below the timing acceptance threshold, so its visual timing estimate must not be applied.

Evidence supports **Tracker dynamic reference inconsistency contributing to the bad hand-eye fit**, not simply missing board detections, a global time offset, or SLAM training. Whether the Tracker disturbance originates in optical visibility/reflection, frozen world geometry, a physical mounting change, or the libsurvive pose solver remains unresolved. The two logs contain only aggregate MPFIT reseed counts (14/6918, 9/6907); no timestamped sweep/solver record exists for these hand-eye captures, so those totals cannot prove a reseeding cause.

### Confirmed code-level repair

The independent calibration entrypoint previously never invoked the existing Tracker step gate. It now runs that gate before board extraction and before freezing. `--tracker-csv` allows explicit input filenames without silently checking a different default file. A replay of round 2 returns code 3 and creates only `tracker_branch_gate.json`; it does not extract/refit or create a frozen candidate. Round 1 still passes the same step gate. Original captures, old candidate reports and production extrinsics are preserved.

This repairs a **reference-acceptance hole**, not Tracker accuracy itself. Further actual accuracy repair requires optical/pose-solver evidence. Do not keep recomputing rigid extrinsics on this rejected capture or interpolate reference errors away to make SLAM pass.

### Reproduction

From `/home/robot/ego_vio_humble`:

```bash
python3 scripts/audit_lighthouse_handeye_rotation.py \
  reports/lighthouse_umi_sessions/20260926_163714_independent_handeye_v13_round1 \
  reports/lighthouse_umi_sessions/20260926_164320_independent_handeye_v13_round2 \
  --output /tmp/rotation_audit.json
python3 scripts/lighthouse_tracker_branch_gate.py \
  reports/lighthouse_umi_sessions/20260926_164320_independent_handeye_v13_round2
```

Saved diagnostics: `rotation_audit.json`, `rotation_audit_formal_td_sensitivity.json`, `branch_gate.json` and `two_round_time_offset.json`.

Verification: 19 focused tests pass, including known-frame rotation recovery, body-frame gyro integration, explicit Tracker CLI path, synthetic persistent-step rejection, smooth-stream acceptance, hand-eye solver tests and timing tests. Python compilation, shell syntax and diff whitespace checks pass. Independent read-only review found no blocking correctness issue; its functional-test coverage recommendation was implemented. These checks verify the diagnostic/guard implementation, not physical Tracker accuracy.
