# Metric-stage localization after the native update hypothesis failed

2026-09-28, evaluation-only. No estimator, graph factor, model, td, calibration,
input data, trajectory, or 10 mm acceptance gate was changed. The frozen joint
product remains **9/10 PASS; fresh4 maximum ATE 13.801442 mm**. This report
localizes that remaining miss; it is not a new precision result.

## Correct time binding and evaluation contract

The product `trajectory_fused.csv` begins about 1.9 seconds (57 D405 frames)
after the raw MASt3R trajectory. Consequently product row 1053 is **not** raw
camera frame 1053. Every comparison below uses the raw D405 exposure timestamps
at frames 1000–1052, 1053–1078 and 1079–1120. All four stages contribute
exactly 53, 26 and 42 poses to these intervals in each of the three recordings.
The middle interval was identified by the prior official external evaluation
on fresh4; it is used only post hoc, never to train, admit, weight, select, or
interpolate an estimate. Equivalent frame numbers in different recordings are
different physical motions.

Each stage is scored against the *same recording's* frozen SteamVR body
reference with the official 0.05 s interpolation gap and one independent
whole-trajectory **SE(3), no-scale** alignment. Camera-stage poses use the fixed
Docker2 `body_T_cam0` transform; fused poses already refer to the body/IMU
origin. Thus stage ATE numbers have comparable body/time/units but are **not**
factor residuals, and their separately fitted global rigid transforms prohibit
interpreting their difference as the exact graph correction. The full source,
input hashes and time-bound per-stage values are in
[metric_boundary_comparison.json](metric_boundary_comparison.json); the
reproducible read-only calculation is `compare_metric_boundary.py` with its
timestamp-offset and body-frame tests.

## What changes across metric stages

Values are maximum ATE within the *same timestamp-matched 26-pose interval*,
millimetres. `stereo` and `IMU metric` are existing intermediate trajectories,
**not statistically independent**: the IMU scale estimate uses a stereo scale
report. `joint graph` adds visual/stereo/IMU constraints; `fused` is the
unchanged full-rate product. No case-specific method was selected.

| Recording | Stereo metric | IMU metric | Joint graph | Final fused |
| --- | ---: | ---: | ---: | ---: |
| fresh4, the failed case | 20.794 | 7.829 | 14.796 | **13.801** |
| fresh1, passing control | 14.652 | 18.758 | 5.329 | 6.004 |
| heldout1, passing control | 4.418 | 5.161 | 1.139 | 1.237 |

Across their *entire* matched trajectories, the joint graph lowers maximum ATE
from the IMU-metric stage's 33.642/29.964/16.632 mm to 14.796/8.366/5.254
mm for fresh4/fresh1/heldout1. Therefore dropping the graph or trusting only
IMU is **not** a general repair. But in fresh4's specific interval the IMU
intermediate is closer to the reference than the graph/product; that does not
occur in the two passing controls.

The fresh4 product's error is a smooth hump already rising before the 26 frames:
its error magnitude at raw frames 1000, 1040, 1052, 1053, 1065, 1078, 1100 is
4.77, 7.58, 9.97, 10.01, 12.78, 10.43, 5.39 mm respectively. The 10 mm
threshold merely cuts a 26-frame slice from this longer deformation. Consistent
with that, the body-local displacement from raw frame 1053 to 1078 has error
10.265 mm in the IMU intermediate but only 6.145/6.006 mm in the graph/fused
stage. The graph improves **local movement across the bad interval** while
leaving a larger **absolute location offset** there. The offset cannot be
repaired honestly by deleting/interpolating those 26 frames.

Existing raw stereo evidence also does not give a simple UMI-only gate: 26 of 27
fresh4 dense10Hz stereo observations beginning in that interval are accepted;
the median PnP inlier count is 153.5 and median visual-motion direction cosine
0.998. Its local/global scale ratio median is 1.044, whereas the passing fresh1
control's same-index median is 0.873 (a larger scale disagreement). The graph
already uses 1650 stereo edges plus a local-stereo-scale state in fresh4. A
universal confidence clamp, keyframe suppression, online-trajectory switch, or
global stereo-scale threshold would be unsupported by these controls.

## Inference boundary and next finite test

Direct observation: fresh4's remaining miss is associated with a low-frequency
graph/fusion position deformation, not a point-map update formula error or a
loss of local motion accuracy inside the 26-frame slice. It is **not yet proven**
which visual/stereo/IMU constraint or graph state first creates the hump;
separately aligned ATE alone cannot assign factor-level causality. The external
reference has not entered any algorithm run.

Next: instrument the *existing* graph on all ten frozen cases to emit per-node
pre/post position corrections and per-family signed residuals on a fixed
time-stratified schedule, with unchanged solver/output identity. First look for
an internal cross-sensor consistency signal that distinguishes fresh4's broad
hump from passing controls. Only if such a signal is independently supported
should a structural UMI-only estimator repair be proposed and judged on the
unchanged all-ten official gate. Do not re-run the 14 closed weight/threshold/
keyframe families or tune a correction from SteamVR error.
