# Official SteamVR scoring integration — NOT a fusion SLAM accuracy claim

The new scoring entrypoint consumes an already-generated body/IMU-origin
trajectory. It does not run, tune, warp, scale-fit or supervise SLAM.
GT construction reads only t_sec from the estimate; source bytes are hashed
before/after scoring. The numerical evaluator retains its SE3/no-scale metric
and10mm maximum gate. A failing precision result returns3 and keeps reports.

## Use after offline fusion finishes

```bash
cd /home/robot/ego_vio_humble
rtk proxy python3 scripts/score_steamvr_slam.py \
  --capture /absolute/path/to/official_steamvr_capture \
  --estimate /absolute/path/to/fusion/trajectory_fused.csv \
  --query-time-domain camera \
  --output /absolute/path/to/new_score_directory
```

The default reference is the September27 frozen, independently validated
Tracker-to-body calibration. Use only a body/IMU-origin estimate, not a raw
MASt3R camera-origin trajectory. The current complementary fusion's report
declares output_frame=body_imu_origin and its published timestamps are derived
from camera-frame stamps. For an actual IMU-timestamped body stream explicitly
choose --query-time-domain imu; this applies only the independent Tracker
offset, not camera/IMU td a second time. No legacy libsurvive script is launched.

Official mode rejects source/backend/frame/device/session/config/hash mismatch,
missing authoritative exposure clock pairs, >30ms raw Tracker interpolation
gaps and <98% reference timestamp coverage. It does not infer that physical
mounts or base stations have never moved from CSV hashes.

## Two recorded-data checks (independent AprilGrid body poses, NOT SLAM)

Camera board poses from take2/take4 were rigidly transformed to body using the
unchanged formal body_T_cam0, with no fit against Tracker. New scoring used
the frozen take3 extrinsic and offset unchanged on both recordings. These
checks validate reference application/clock/frame/report plumbing only.

| Board check | Mean mm | Median mm | RMSE mm | P95 mm | Max mm | Matched query coverage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| take2 |1.420|1.260|1.565|2.716|4.202|99.916%|
| take4 |1.417|1.124|1.728|3.507|5.821|99.911%|

Board detection itself was1197/1199 and1124/1199 respectively. The coverage
above is relative to detected board-pose timestamps, NOT all captured camera
frames. Input check trajectories remained byte-identical after scoring.

Earlier *_score_check reports are retained: they expose an evaluator issue
where exact observed timestamps were discarded if a neighboring reference
gap was large. Fixed only this gap rule: exact recorded matches are kept,
genuinely unobserved times still obey the interpolation limit. No tolerance,
error-based deletion or synthetic reference poses were added. Final evidence
is in *_exact_timestamp_check folders; original checks were not overwritten.

Formal future SLAM accuracy requires fresh independent task recordings and
their actual fusion output. Do not report this table as fusion performance.
