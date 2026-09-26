# Lighthouse prediction/optical audit — 2026-09-27

## Result: NOT FIXED / no production change

Four independent raw captures were replayed for each new candidate. Only the fixed-board capture has independent metric reference; jump counts in the other three recordings are diagnostics, not ATE. All board comparisons use876commonpoints, the same frozen hand-eye/time offset and one shared rigid alignment fitted to the fourway baseline. No scale/local deformation/error-threshold point removal.

| Mode | Mean mm | P95 mm | Max mm | Below10mm |
|---|---:|---:|---:|---:|
| Joint world, fourway baseline |5.046|12.090|34.634|93.04%|
| Joint world, no gate |6.606|18.499|25.464|79.68%|
| No motion deskew (rejected) |5.053|11.580|31.853|92.81%|
| Delayed observation model (rejected) |5.146|12.383|36.610|92.69%|

## Corrections to prior explanation

The60.008ms optical gap at the maximum is real, but NOT sufficient to explain the whole local error block: at11.4926s filtered error is31.323mm with only7.530ms optical age, and error persists after reacquisition. The earlier explanation attributing the spike simply to prediction through that gap was incomplete.

Same-sample exact origin/lever decomposition: frozen Tracker->left-camera lever35.3655mm. At camera residual peak34.634mm, Tracker-origin residual norm35.5518mm; orientation-dependent lever vector[-0.8616,-0.3425,+0.3953]mm (norm approximately1.007mm). Across876points lever component max1.9878mm. This does not prove the frozen hand-eye translation perfect; it excludes the measured orientation/lever contribution as the main34mm term.

At the peak,35.5398mm of the35.5518mm Tracker-origin error projects onto the last raw optical covariance's weakest position direction. Raw principal position standard deviations are[1.5696,2.3058,8.4982]mm. These are solver uncertainty estimates at its IMU origin, NOT a validated bound, and head lever covariance is not propagated. This strongly supports weak optical geometry/biased optical observations as the next target, without distinguishing sensor-layout/calibration/model bias yet.

At the preceding optical solve, LH0 both axes see only sensors4/18/19, while LH1 sees8sensors per axis. LH0 three-sensor centered spatial singular values[49.67,7.52,0]mm vs LH1 first-axis[65.32,60.97,25.62]mm: LH0 visible points are nearly collinear and spatially much less diverse. Four axes present is not equivalent to strong3Dgeometry. This is a concrete coverage limitation, not proof the base physically moved; next controlled sample should change Tracker orientation toward both bases, not silently move bases or tighten an arbitrary sensor-count gate.

## Timing candidate and rollback

Added OBS_TIMING diagnostic when show-raw-obs is enabled. All4raw/final pose streams remain numerically byte-identical to the baseline with instrumentation only. Observations arrive behind the filter state with median lag5.51/5.33/5.35/5.80ms; boardP9511.28ms. Existing code clamps observation time to model time and computes but does not apply Raug.

Tested an opt-in short-delay measurement function h(f^-dt(x)) with error-state chain-rule Jacobian. This is NOT full stochastic fixed-lag rewind/replay. It worsened board max to36.61mm, so source changes for the model were removed. The rejected patch is archived as delayed_measurement_REJECTED.patch only; do not deploy it. The only retained source change is diagnostic logging. No production library/calibration installed.

No-deskew ablation also rejected: rAt98l raw jump flags6->51 (board11->14); original fourway solve/frame counts retained. No-gate worsens board mean/P95 despite lowering max. Do not select a method solely by one maximum.

## Reproduction / evidence

- board_validation/diagnose_prediction_gap.py in reports/lighthouse_umi_sessions/20260927_023856_lighthouse_world_board_validation_retry produces prediction_gap_diagnosis.json and prediction_gap_samples.csv.
- analyze_optical_timing.py verifies4-stream invariance and generates optical_timing_audit.json.
- no_motion_deskew/, timing_instrumented/, delayed_measurement/ hold4replays each and summaries; runner validate_three_new.py accepts explicit source and extra_args, validates configfile, loadedstationIDs, fourway support and nonzero solves.
- Build /tmp/libsurvive_prediction_diagnostic_20260927 succeeds. Existing optimizer array-parameter warnings unchanged. ctest found NO tests: do not claim unit-test pass.
- Python py_compile succeeded. Core evidence is replay invariance and independent fixed-board numerical comparison.
- Installed library SHAe09e6fb27b30dfc1b15a4a61c0eb4ae69ffb8e691dff222f534afcff7be89882 unchanged; formal world SHA8b2f50ed5bf51ee4366d155dd99983a7a2791b1de4b7995d375d5036b94668d0 unchanged.

## Next boundary

Do not launch another arbitrary filter-parameter sweep: tested candidates cannot repair the underlying weak optical direction. Next causal experiment should hold the board/mount/world fixed while changing Tracker viewing orientation/coverage, or compare per-station optical residual/model bias on this fixed-board segment. No base relocation is automatically authorized. New physical samples are needed before accepting a repaired reference; no10mm guarantee can be made from these results.
