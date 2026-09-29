# Frontend precision pivot — 2026-09-29

## Goal
Find and verify a source-only, recording-generalizable correction for the remaining >10 mm fusion error without using Lighthouse/SteamVR to construct the SLAM trajectory.

## Current phase
3 — opt-in single-frame stereo/IMU correction and simple short-hop replacement were falsified. Before another estimator change, separate bad MASt3R visual geometry from weak D405 stereo and assess an independently observable multi-view factor across the full bad block.

## Phases
1. [complete] Audit the fixed ten-recording result and reject the VINS absolute-position/backend-scale candidate on the predeclared controls.
2. [complete] Compare saved MASt3R frontend observations and independent D405/IMU geometry at the same frame/edge boundary in the failed recording and passing controls. Directional vector comparison disproves a simple consensus threshold: the passing control has a strong counterexample.
3. [complete, negative] Dense native correspondence capture and three byte-identical frontend replays isolated a 1054–1057 visual rotation discrepancy, absent from two passing controls. The opt-in correction failed to persist across the new keyframe: first exploratory maximum ATE worsened 13.801→14.015 mm, and a reviewed scale-corrected frontend replay left the peak frame essentially unchanged. Production remains untouched.
4. [in progress] Do not promote a persistent IMU/stereo factor from the current disagreement: a post-score directional check and a full exploratory replay both predict/measure regression. Motion-matched controls are now complete. Next compare independent multi-view reprojection/track consistency across the bad block and passing high-turn windows, then design at most one source-only factor with an explicit ≥8 mm pre-score effect and unchanged 10-case official acceptance. Diagnostic/experimental sources have already been backed to the writable remotes and clean-restored; the latest short-hop and control results still need backup.

## Decision / stop rules
- No more backend VINS-position or log-scale weight sweeps: one-cell PASS regressed two independent passing cases and dev2 failed numerically.
- No post-score GT-selected routing, per-recording tuning, discarded difficult recordings, or relabeling 10 mm maximum ATE as a weaker metric.
- Do not rerun the 14 closed families listed in the 2026-09-22 handoff; do not change the formal Docker2 td.
- If no cross-case discriminating sensor-only witness exists, report the uncertainty and the next finite experiment instead of guessing another correction.

## Verification target
All ten unchanged, valid recordings: `ate_translation_max <= 10 mm`; no previously passing recording may regress above the gate. Score only after trajectory hashes are frozen.

## Errors encountered
- Previous handoff skill lookup used the wrong directory. Correct skill path read; memory session store returned no matching sessions, so filesystem plan and handoff are authoritative.
- Initial report patch failed because the context order did not match this file; reapplied in separate patches. Unit test first exposed a missing `Rotation` import; repaired before generating final v2 reports.
