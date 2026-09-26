# Progress

2026-09-26: Read current solver, stereo AprilGrid extraction, raw IMU timing estimator and historical Lighthouse branch-switch investigation. Production calibration and SLAM algorithms unchanged. Continuing offline cross-sensor audit of the two new independent captures. Preliminary rough split points not yet used as final evidence.

Exact-window multi-horizon audit saved. Added reusable diagnostic plus 2 synthetic coordinate/integration tests. Added failing then passing entrypoint guard regression. 17 targeted tests pass; bash syntax and diff whitespace checks pass. Real R2 calibration entrypoint replay rejects before board extraction, preserving data. Independent read-only code review and exact-path remote backup pending.

Independent read-only review found no blocking issue and requested functional coverage of the explicit Tracker path. Added synthetic persistent-step REJECT / smooth PASS CLI tests: 19 targeted tests now pass, Python compile and bash syntax checks pass. No LSP tool is available; runtime tests and compilation are the verification evidence. Preparing exact-path commit and remote restore verification, excluding unrelated dirty work and all raw recordings.

Backup staging: report evidence paths are ignored by repository policy; initial ordinary add declined those paths while staging code/planning. Re-stage only the eight named small evidence files with -f, never the report directory or raw capture data.
