# Lighthouse prediction repair, 2026-09-27

## Goal
Diagnose and repair reference spikes using Tracker optical/IMU only; verify against independent fixed board and multiple raw records. No production deployment until validation, no UMI supervision, no deleted error samples.

## Phases
1. Reproduce same-point gate contrast: complete (876 common board points).
2. Test motion-deskew feedback on four records: complete, reject disable-deskew candidate.
3. Trace observation/device/filter timing and covariance: complete.
4. Implement only evidence-backed correction, multi-record validation: complete experiment, candidate rejected; precision goal NOT complete.
5. Back up exact code/evidence to sencang and restore/compare: in_progress.

## Acceptance
Maximum position residual <=10 mm on independent board without coverage reduction; report means, P95, max, coverage and calibration limitations. Raw jump flags on recordings without a board are diagnostics, not precision truth.

## Errors
- Native debugger agent failed with 403; continue source inspection locally.
- No CodeGraph index exists in either task repo; use rg.
- Matplotlib Axes3D import warning: 2D diagnostics unaffected.
- Initial delayed model build needed forward declaration; corrected, build passed. Rejected model archived and removed after independent residual regression.
- ctest found no tests; rely on measured replay invariance and fixed-board comparison, not an alleged unit-test pass.
