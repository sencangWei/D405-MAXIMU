# Explicit four-way Lighthouse support gate — offline candidate

Status: candidate-only source change and isolated build. The installed `/home/robot/.local/lib/libsurvive.so.0.3`, active calibration, and hardware were not changed.

2026-09-26 correction: the first `three_new_validation/` runs used the unrecognized `--config` option and omitted residual recording. Those comparisons are invalid and are superseded by `three_new_validation_v2/` and `THREE_NEW_REPLAY_CORRECTION.md`. All three new inputs produce valid solves with the corrected runner.

## What changed in the candidate

`src/poser_mpfit.c` now has two opt-in MPFIT settings:

- `min-lighthouse-count` (default `0`, disabled): minimum count of already-positioned Lighthouse stations that qualify.
- `min-measurements-per-lighthouse-axis` (default `1`): each qualifying station must contribute at least this many measurements on **both** X and Y sweep axes.

The normal total `required-meas` check remains unchanged. Preferred candidate replay uses `min-lighthouse-count=2`, `min-measurements-per-lighthouse-axis=1`, and the existing `required-meas=8`. This explicitly requires all four station-axis groups for this two-station setup. The stricter two-observations-per-axis setting was also evaluated. Defaults preserve old behavior.

## Independent raw-record replay

Same candidate binary and frozen joint-optical world were used for no-gate and gate replays. “Jump” means the existing discontinuity diagnostic: adjacent record-time poses within 20 ms move >20 mm or rotate >10 degrees; it is not ATE or absolute accuracy.

| Recording | Mode | Raw solves | Minimum total / station-axis groups / stations per saved solve | Raw/final jump flags | Final pose callbacks | Same-time position change vs no-gate, P95 / max |
|---|---|---:|---:|---:|---:|---:|
| Diagnostic | No gate | 6,928 | 11 / 2 / 1 | 20 / 0 | 9,372 | — |
| Diagnostic | Explicit four-way, ≥1 per axis | 6,570 | 14 / 4 / 2 | 0 / 0 | 9,372 | 1.08 / 8.53 mm |
| Diagnostic | Explicit four-way, ≥2 per axis | 6,445 | 15 / 4 / 2 | 0 / 0 | 9,372 | 1.40 / 21.47 mm |
| Fresh | No gate | 6,396 | 16 / 3 / 2 | 2 / 0 | 8,087 | — |
| Fresh | Explicit four-way, ≥1 per axis | 6,307 | 16 / 4 / 2 | 0 / 0 | 8,084 | 5.16 / 7.09 mm |
| Fresh | Explicit four-way, ≥2 per axis | 6,278 | 16 / 4 / 2 | 0 / 0 | 8,084 | 5.26 / 7.73 mm |

The ≥1-per-axis gate is the preferred diagnostic candidate: it removes the flagged raw optical jumps on both captures, requires explicit four-way support, and retains 100% and 99.96% of the no-gate final pose callbacks. Raising support to ≥2 per axis removed no additional flagged jumps and reduced optical solves further, so that stricter setting is not justified yet; its 21.47 mm maximum same-time displacement change is a tail behavior change, not GT error.

For comparison, raising only total `required-meas` to 26 also happened to yield four-way support and zero flags on these recordings, but it is not a robust way to request four-way coverage: that option only checks total measurements. The explicit gate expresses the intended condition directly and with less output perturbation.

## Limits and next validation

- These are only two independent recordings from the current layout. No external motion-capture / surveyed GT was used, so this does **not** establish absolute Lighthouse accuracy or <10 mm world-position accuracy.
- The active installed library hash differs from the source-tree build. This source candidate was built in `/tmp/libsurvive_fourway_candidate_20260926` with Playback, MPFIT, BarycentricSVD seed poser, GSS, and StateBased plugins. Validate the patch against the exact product build before deployment.
- The changes are isolated to the existing source file `/home/robot/.local/src/libsurvive/src/poser_mpfit.c`; unrelated dirty source files and local udev rules in that repository were left untouched.
- Do not enable the settings in production until more independent same-layout recordings pass and the exact runtime library is rebuilt and replayed.
- Build/verification: isolated CMake build of `survive-cli` succeeded with required playback/solver/MPFIT/BarycentricSVD plugins; `git diff --check` and the focused Python diagnostics suite passed (`13 passed`).
- Recoverable backup: commit `ae8d953a` on `sencang/codex/lighthouse-explicit-four-way-20260926`. The patch was fetched from that remote branch, applied to a clean upstream worktree at `f1e6edd`, and reproduced the modified `poser_mpfit.c` SHA256 `0a85d89d1cd2e4472dd0a1cb7bd56387a3d3bc9b455fd1f2d1943a73f716d964`.

## Reproduction

Raw-record inputs:

- `reports/lighthouse_umi_sessions/20260926_170502_raw_optical_diagnostic_v13/lighthouse_raw.rec`
- `reports/lighthouse_world_repair_live_20260926.jBBJoQ/lighthouse_raw.rec`

Candidate rec outputs are in sibling directories `validate_no_gate_*`, `validate_explicit4_default_*_v2`, `validate_explicit4_min2_*`, and `validate_explicit4_26_*`. The failed first build without BarycentricSVD seed support is retained separately and was excluded from these results.
