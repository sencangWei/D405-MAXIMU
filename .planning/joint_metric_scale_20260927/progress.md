# Progress

- Readhandoffclosedfamilies and currentphysicalscaleestimator.
- Chose newjointmetricstate hypothesis; plan/acceptancefrozenbeforecoding.
- Using systematic-debugging and karpathy minimum-change workflow; frozen
  sixcachedcases from .planning/static_motion_guard_20260927/run_cached_regression.py.
# Implementation and first probe

- Added optional `solve_metric_scale` to joint solver and experimental CLI flag;
  default remains off. Camera translation basis is exact at all frames and
  excludes rigid rotated camera-to-body lever. Same metric factors/IRLS sigmas.
- Added total-correction cap handling for exact full-frame scale contribution.
- 6 cap tests were RED (missing API), then GREEN; 4 solver tests were RED
  (missing API), now GREEN. Zero-motion LSQR numerical residual was 3.07nm;
  test uses 10nm tolerance AND exact equality to disabled solve.
- 125 focused graph/cap/scale/static/complementary/scale-attitude tests PASS.
- Heldout2 probe (all 1143 scored frames retained): max11.70744→6.061998mm,
  mean7.11819→3.528420mm, P9511.02366→5.187315mm, 100%<=10mm. Scoring only
  after GT-free solver. Pre-cap estimated ratio .95662969; 19 corrections
  bounded by existing25mm cap, no cap/gate relaxation.
- Disabled compatibility replay: graph/unsmoothed/final CSVs byte-identical
  to accepted static-guard heldout2, proving optional feature does not change
  its numeric baseline. Six-case common candidate regression and review running.

## Final replay

- Sixcandidate and finalverified passes use one identical recipe, all graph and
  quality gates PASS, all scored frames retained.18trajectory CSVs byte-identical.
- Final maxima mm:6.92723,8.76918,6.08897,6.06200,6.74554,9.02434.
- Dev1 max increases.04381mm and mean.03509mm; formalPASSretained. Other5improve.
-126focused testsPASS; shellsyntax/Pythoncompile/diffcheckPASS. Review found
  capreportmetadata low issue, reproducedRED, fixedGREEN, no numericoutputchange.
- Productionfusionworkflow now includes flag; adaptiveuses this sameworkflow.
  New independent recordings still required (these6are nowdevelopment).
