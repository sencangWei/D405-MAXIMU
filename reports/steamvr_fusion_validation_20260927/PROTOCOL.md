# Frozen two-take official SteamVR fusion validation

## Scope fixed before processing

Process two independent fresh task recordings with the same existing
`mast3r_slam_precision_workflow.sh fusion-guarded` entrypoint. Do not change
frontend, model, fusion parameters, runtime calibration or reference to suit
either take. VINS runs only as an onboard input to the fusion. No separate
VINS-only external precision comparison is requested.

Official Tracker is read only by `score_steamvr_slam.py` after the fusion has
finished. Reference is frozen September27 take3 with fixed take4 validation;
query domain is camera. No Tracker supervision, scale fit, time-offset fit,
manual warp, peak deletion or per-take parameter tuning.

Primary acceptance is SE3/no-scale ATE translation maximum <=10mm; retain
RMSE/P95, rotation and coverage gates unchanged. Preserve all failed stages
and their logs; a failed take must not prevent processing the other take.

## Captures

- take1: `reports/steamvr_umi_sessions/20260927_133525_steamvr_fusion_validation_take1`
  / D405 `d405_720p_rgb_stereo_ir_20260927_133529`.
  Raw acceptance PASS;1199 frames;4794 normal Tracker samples;
  exposure coverage100%; maximum valid Tracker gap10.604ms.
- take2: `reports/steamvr_umi_sessions/20260927_133741_steamvr_fusion_validation_take2`
  / D405 `d405_720p_rgb_stereo_ir_20260927_133744`.
  Raw acceptance PASS;1200 frames;4798 normal Tracker samples;
  exposure coverage100%; maximum valid Tracker gap9.792ms.

Formal VINS configuration SHA256:
`87338c341a7bfea71194528fc2c735a55ee85d1d1558141f096dd00a97059331`.
Use `estimate_td=0`, `td=-0.009109323s`, replay `--imu-shift-ms 0`,
`--rate 0.5`, `--expect-loop any`. No GT input to VINS or fusion.

Wait for benchmark environment PASS after recording I/O settles. The immediate
post-capture preflight failed only temporary I/O pressure17.11%>10%; subsequent
preflight passed at1.11%, without changing thresholds or killing other work.

Each take: internal VINS replay -> guarded fusion -> official read-only score.
All stdout/stderr retained in stage logs under a new run directory.
