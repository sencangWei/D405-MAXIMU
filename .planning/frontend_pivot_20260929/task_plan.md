# Frontend precision pivot — 2026-09-29

## Goal
Find and verify a source-only, recording-generalizable correction for the remaining >10 mm fusion error without using Lighthouse/SteamVR to construct the SLAM trajectory.

## Current phase
3 — the first source-only disagreement gate was falsified; test an upstream multi-frame measurement rather than another weight or threshold.

## Phases
1. [complete] Audit the fixed ten-recording result and reject the VINS absolute-position/backend-scale candidate on the predeclared controls.
2. [complete] Compare saved MASt3R frontend observations and independent D405/IMU geometry at the same frame/edge boundary in the failed recording and passing controls. Directional vector comparison disproves a simple consensus threshold: the passing control has a strong counterexample.
3. [pending] Build only a causally distinct multi-frame right-IR/MASt3R metric observation, after a diagnostic feasibility test. Reject immediately on missing output, unstable solver, or a passing-case regression.
4. [pending] Run unchanged ten-case official scoring only after those checks; keep incumbent if not universally better. Back up algorithmic changes to the correct writable remote and verify from a clean restore.

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
