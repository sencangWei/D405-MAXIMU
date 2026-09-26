# Independent orientation-control capture, 2026-09-27

This is Lighthouse reference diagnosis, NOT a SLAM precision result or a
new hand-eye calibration. No production world/library/time/extrinsic changed.

## Capture validity

- D405: 40 s, 1199 stereo frames; camera/400 Hz IMU acceptance PASS.
- Fixed AprilGrid: all 1199 poses valid; reprojection P95 0.288 px.
- WM0/Tracker camera-time query coverage: 1187/1199 (98.999%).
- Tracker powered off in the post-camera tail (record time 66.068406 s).
  Capture completion and original Tracker integrity remain FAIL. Raw jump
  flags are preserved, not removed to make precision pass.
- Comparison uses 1181 common valid points (98.499% of camera frames),
  30 ms maximum interpolation gap; the remaining points are coverage failures,
  not zero-error points. See tracker_integrity_recovered_offline.json and
  tracker_d405_overlap.json in the parent directory.
- A T20 object also appeared; analysis selects WM0 and verifies tracker serial
  LHR-A2A59C7D. Replay global event counts must not be called WM0-only rates.

## Frozen inputs and results

External mapping and query offset are unchanged from the Sept 11 independent
AprilGrid calibration; its uncertainty remains material. Lever is 35.3655 mm.
No scale, local warp, hand-eye or time-offset fitting. Global SE3 is a frame
alignment only. The joint world candidate predates this validation capture.

| Joint-world candidate | Mean mm | P95 mm | Max mm | Below 10 mm |
|---|---:|---:|---:|---:|
| Four-axis gate | 4.104 | 9.960 | 55.532 | 95.343% |
| No gate, SAME SE3 | 6.197 | 12.662 | 17.760 | 84.674% |

The four-axis peak occurs 19.759 s after the first camera frame, with
546.569 ms since the last accepted optical solve (558 ms bracket).
Incomplete optical observations continue inside the gap; the strict gate
rejects those. The peak's orientation-dependent lever component is 1.186 mm,
while Tracker-origin residual is 56.675 mm under this frozen mapping.
Camera orientation at this point differs 38.775 degrees from the initial
orientation; this is measured total orientation change, not a claimed yaw.

Even fourway samples with optical age <=20 ms reach 14.928 mm. Therefore
eliminating the long gap alone cannot establish a <=10 mm reference.
No-gate improves the worst sample but worsens mean/P95/pass fraction on both
this capture and the previous independent capture. Deploying no-gate would
not meet acceptance. The fourway half-fit/second-half maximum is 16.517 mm;
the all-fit mean is not an independent held-out calibration claim.

Formal-world live/replay residuals are much worse; the near-180 degree
position-only replay alignment rotation is not a trustworthy absolute
orientation diagnosis (weak/near-planar position excitation). Do not use it
as proof of a 180-degree physical mounting error.

## Reproduce

Run analyze_orientation_control.py in this directory. It imports the previous
capture's compare_fixed_board.py only for parsing, clocks, frozen mapping,
interpolation and SE3 utilities; all raw/pose inputs are this new capture.
The replay configurations/commands/input hashes are saved in replay/.
fixed_board_comparison.json additionally reports the live/formal modes and
independent gyro timing checks. Tracker gyro correlation fails its signal gate;
do not replace the frozen time offset with this capture's inferred offset.

## Next boundary

Investigate geometry/covariance-aware partial-observation updates rather than
requiring four axes or accepting all observations equally. Any proposed
algorithm must be validated on BOTH independent fixed-board captures and
the three prior raw records; raw records alone cannot prove millimetre accuracy.
No production installation or replacement is authorized by this diagnostic
result. User physical station or device movement is not automatically required.
