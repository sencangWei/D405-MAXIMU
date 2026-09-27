# Independent board validation (take4)

Fixed before solving this take:

- Source transform: take3 `aprilgrid_composed_clock_diagnostic.json`,
  `tracker_T_body`; this is a candidate, not a production-frozen reference.
- Fixed camera-domain Tracker query offset: -12.786861933161965 ms,
  composed from formal camera/IMU td -9.109323 ms plus take3 independent
  IMU/Tracker offset -3.6775389331619657 ms.
- Evaluate relative-motion AX-XB residuals without fitting X or time offset
  on take4. Unknown board/world placement cancels in relative motions.
- Existing calibration thresholds: >=100 overlapped poses, overlap >=90%,
  excited translation P95 >=30mm and rotation P95 >=10deg,
  observability PASS, combined residual P95 <=10mm, rotation P95 <=3deg.
- Also report maximum residual, number of pairs, data integrity, and board
  detection rate. Motion-gated relative pairs use the existing horizons
  0.20/0.50/1.00/1.50s; this is not an all-frame SLAM ATE report.
- Independently estimated take4 IMU/Tracker offset is diagnostic only and
  must not replace the fixed offset to improve the validation result.
- No transform freezing, production application, or SLAM supervision.
