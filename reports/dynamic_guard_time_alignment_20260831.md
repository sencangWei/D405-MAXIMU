# Dynamic guard / time-alignment verification (2026-08-31)

## Changes under test

- Robot–UMI evaluation uses a fixed device-level query offset from the
  multi-run aggregate: `+16.579654 ms` (MAD `0.232371 ms`).
- Robot endpoint interpolation uses the same offset as the start point and
  every relative-motion horizon.
- Robot TCP association is linear interpolation (position) and SLERP
  (orientation); nearest raw sample distance is diagnostic only.
- VINS failure detection rejects only non-finite state, exploded IMU bias, and
  meter/50-degree catastrophic jumps. It does not flatten Z or reject normal
  lifts/turns.

## Full replay evidence (Docker2 formal configuration)

Session: `d405_720p_rgb_stereo_ir_20260830_035548`

Acceptance: `robot_eval/diagnostic/dynamic_guard_smoke_20260831_docker2/run_acceptance.json`

- Result: `PASS`; health: `SLAM_HEALTHY`, `product_usable=true`
- Config SHA-256: `3f47e90f838aff2e4770eecccc5bebe29b29fd07833576ab8568cf6bd693db36`
- Docker2 `td`: `-0.009109323 s`
- Raw/corrected odometry: `1742 / 1742`
- Pose coverage: `0.993158`
- Automatic loop accepts: `21`; post-geometry rejects: `6445`
- Loop input drops: `0`; estimator keyframe queue drops: `0`
- Pose-graph unusable solutions/inconsistent candidates: `0 / 6`
- VINS failure-detector triggers: `0`

Rebuilt binaries:

- VINS SHA-256: `948b2ad4f60959c2566f77f7b73afa41f6109491365531df26938be9fa6ae05c`
- Loop-fusion SHA-256: `678530051ce0bf0874637a069249a790400ee7488ab05dcfafb220ac799be60d`

## Interpretation

The run proves the guard and timing fixes are operational and do not break a
full replay. It does **not** prove absolute TCP error below 1 cm: the current
hand-eye residual is still about 9.319 mm and the remaining long-horizon error
must be measured on held-out dynamic data. The aggregate offset is a robust
evaluation alignment, not a replacement for Docker2 VINS camera–IMU `td`.
