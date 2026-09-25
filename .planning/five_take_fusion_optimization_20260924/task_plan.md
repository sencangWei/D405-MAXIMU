# Five-take fusion optimization

## Goal
Use the five 2026-09-24 recordings to improve the MASt3R + stereo + IMU/VIO post-processing pipeline without robot/Lighthouse supervision in the estimator. Treat these five as development data, not proof of out-of-sample 10 mm accuracy.

## Current phase
Phase 3 — five-take window-factor and attitude-gauge experiments rejected by internal sensor/guard A/B; isolated learned-depth weight also rejected. Production code restored. Diagnostic traces on two bad and one good take now separate learned pointmap metric factor, tracker-relative pose, and keyframe anchoring: the bad metric distortion is already within same-keyframe tracking; no single pointmap-scale or match-count threshold discriminates the bad takes from the good control. Next intervention needs a residual-level front-end hypothesis and cross-take A/B before product promotion.

## Phases
1. Audit recording, tracker GT, VINS, MASt3R, stereo, graph and final score status across all five; reproduce take1/take3 first failures. Done.
2. Diagnose take1/take3 dense stereo dispersion at the source, compare with successful takes, test one minimal cause-directed change only if evidence supports it. Done: visual front-end time-varying scale confirmed; three existing switch combinations rejected for no effect, jumps, or NaN.
3. Profile take2 sustained tail drift and take4/take5 rotation failures separately; determine whether they arise from front-end, reference/extrinsics, or scoring. Avoid the 14 closed tuning families. Initial profiling done; attitude-vs-position gauge mismatch diagnosed, but no safe correction yet.
4. Run frozen-code five-take A/B (internal gates first, Lighthouse scoring only after trajectories fixed); report failures honestly. Done for the opt-in window and attitude-alignment graph candidates; both rejected before GT scoring. Future upstream candidate still pending.
5. Run targeted regressions and static checks. Rejected code was restored; 57 targeted tests pass and tracked worktree is clean. Future verified algorithmic improvement must be committed/pushed to `sencang` and remote-verified.

## Constraints
- Never feed Lighthouse/robot trajectories or ATE values to algorithm, parameter selector, or optimizer.
- Do not merely relax failure thresholds or bypass failed dense stereo edges to create a score.
- Do not rerun the 14 closed tuning families or revert to 09-14 as a presumed good production baseline.
- Preserve all original reports and recordings; isolate candidates under this plan directory.
- Never stage `reports/`, never push to `origin`, never force-push.

## Success criteria
- Each of five has an explicit status; no fabricated metric for a failed run.
- At least one verified, causal algorithm improvement on multiple takes without degrading trusted historical controls, or an evidence-backed finding that new data is required.
- Fresh unseen recordings remain necessary before claiming reliable all-run <10 mm.
- The tested window-factor prototype passed its synthetic activation/consensus test but **failed** the real five-take no-regression criterion; it is not in production.

## Errors
- None in this task yet.
