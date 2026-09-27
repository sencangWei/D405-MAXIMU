# Stage-aware metric rescue experiment

Frozen before candidate execution on 2026-09-27. This is a new candidate,
not a replacement of the frozen two-take benchmark in run_1340.

- Change only selection: inspect sequential stereo stages, allowing the existing
  default-off VINS metric-prior rescue after a sole scale-dispersion failure.
  Missing reports, other failures, external supervision, or visual gaps fail closed.
- Do not change scale-dispersion threshold (0.5), rescue sigma (0.004 m), model,
  formal camera/IMU calibration, or final acceptance thresholds.
- Take1 control: replay selection against all existing baseline internal reports;
  selected trajectory must remain byte-identical. No expensive frontend rerun is
  needed because baseline computation is untouched.
- Take2: reuse original prepared IR images and accepted VINS trajectory, generate
  body-to-left-IR priors with the frozen formal calibration, and rerun the existing
  rescue frontend plus every downstream fusion stage in a new output directory.
- Tracker is never an optimizer input. Score with the frozen official reference
  only after internal gates PASS and a selected fused trajectory exists.
- Success requires internal PASS, official translation maximum <=10 mm, and no
  take1 regression. Failure is retained and reported; never increase thresholds,
  selectively remove error frames, or claim dataset-wide validation from two takes.

Original take2 failure: short-hop scale dispersion 0.523 > 0.5. Independent
stereo/VINS displacement agreement suggests unstable MASt3R local scale, but
does not prove a unique frontend cause. This experiment tests an existing rescue,
not a new trained model or ground-truth correction.
