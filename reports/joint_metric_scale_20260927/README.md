# Joint camera metric-scale state — 2026-09-27

## Result and scope

The remaining fresh take2 error is repaired in the six-case cached regression:
all six unchanged SE3/no-scale precision gates PASS, all scored samples retained.
No Tracker/robot position supervision, GT-derived scale or model retraining.
These are development recordings used to investigate this change: previously
named heldout cases are no longer independent acceptance evidence. Fresh
recordings are needed to assess generalization; this is not a universal10mm claim.

| Case | Prior static-guard max mm | New mean mm | New median mm | New P95 mm | New max mm | <=10mm |
|---|---:|---:|---:|---:|---:|---:|
| dev1 |6.88342|2.85734|2.82458|5.52955|6.92723|100%|
| dev2 existing rescue |9.45862|3.77582|3.83350|6.38311|8.76918|100%|
| heldout1 |9.90944|2.50846|2.55474|4.27322|6.08897|100%|
| heldout2 |11.70744|3.52842|3.76602|5.18731|6.06200|100%|
| heldout3 |9.23629|3.86854|4.47730|6.08021|6.74554|100%|
| heldout4 |9.95174|3.43914|3.59146|5.15644|9.02434|100%|

Dev1 slightly worsens mean by0.03509mm/max by0.04381mm; it remains comfortably
PASS. Do not claim every individual metric monotonically improved. Other five
mean/max values improve. All graph and input-quality gates PASS; sample counts
1143 (dev2:1144), reference timestamp overlap100%, unchanged scoring thresholds.

## What caused the error and what changed

Raw captures pass rate/gap/frame/timestamp checks. Previous static-guard repair
removed graph-induced long-stop creep, but moving take2 retained11.707mm max.
IMU global scale .509988934 and stereo scale .486308615 differed4.87%; the graph
fixed their initial log-mean .498008044 and had only local position corrections.
Diagnostic-only GT Sim3 suggested scale mismatch, but was never used as a target.

The new graph adds one scalar epsilon to existing local corrections, velocities,
gravity and accelerometer bias. For original metric camera positions p:

`p_corrected(t) = p(t) + epsilon*(p(t)-p(0)) + local_correction(t)`.

Existing stereo displacement, validated VINS relative displacement and IMU
position-preintegration equations estimate epsilon jointly. Their factor sigmas,
IRLS kernels, initial scale mode, correction bounds and gates are unchanged.
IMU velocity factors still solve velocity/gravity/bias; they do not directly
scale the calibrated force or camera lever. Fixed rotated camera→body offset
is subtracted in metres and never multiplied by epsilon. Exact full-frame
camera motion is used, not linear interpolation of the scale basis at nodes.

The take2 solved pre-cap ratio is0.95662969, estimated without GT; it is not the
diagnostic GT ratio .96862158. Existing25mm cap bounds combined local/global
correction (19frames clipped). Report ratio is explicitly pre-cap, not a claim
that final trajectory is uniformly scaled. All original frames remain present.
The original force/attitude contribution to scale disagreement is not proven
to be an IMU hardware fault; do not infer that from improvement alone.

Scale estimation is inactive below1mm camera translation span; explicit ratio1
is reported. Nonfinite or >15% ratio change fails closed. Static protection is
retained. This is HANDOFF directionC (new physical state), not a repeat of closed
weight sweeps or a switch from joint to stereo metric scale mode.

## Deployment and verification

- `scripts/fuse_mast3r_stereo_imu.py`: new optional
  `--joint-metric-scale-optimization`; default API/CLI stays disabled for backward
  compatibility. `stereo_translation_fusion.joint_metric_scale` reports policy.
- `scripts/mast3r_slam_precision_workflow.sh`: current one-key fusion branch
  enables it; adaptive/rescue candidates using that branch inherit it.
- Synthetic RED→GREEN tests cover3%scale correction, rotating30mm lever,
  no-excitation inactive equivalence, defaultoff equivalence and all cap modes.
- Independent review caught incorrect per-node cap-scale metadata when clipping
  happens between nodes. RED test reproduces it; report now includes both caps.
-126focused/adjacent tests PASS; Python compile, shell syntax, diff checks PASS.
- Disabled replay graph/unsmoothed/final CSVs are byte-identical to accepted
  static-guard heldout2. No baseline numeric drift when option is omitted.
- Candidate outputs: `candidate_v1_six/<case>/official_score/precision.json`.
- Final `verified_v1_six` replay: all six PASS and all18 graph/unsmoothed/final
  CSVs byte-identical to trial. The report-only clipping metadata fix did not
  change numeric trajectories. Authoritative metrics are in
  `verified_v1_six/<case>/official_score/precision.json`.
- Recovery backup validation in progress at document creation.

Reproduce cached downstream stages7→9 (does not rerun neural frontend/stereo):

```sh
cd /home/robot/ego_vio_humble
rtk proxy python3 .planning/joint_metric_scale_20260927/run_cached_regression.py \
  --output reports/joint_metric_scale_20260927/new_unique_run
```

Output must not already exist. Exact commands, input/source SHA256 and frozen
scoring reference provenance are recorded for each case. GT scoring occurs only
after fusion output. Data/camera calibration/td/checkpoint/policy stay unchanged;
formaltd=-0.009109323s, replay shift0. New recordings must use same frozen
reference/calibration and be scored independently without per-case tuning.
