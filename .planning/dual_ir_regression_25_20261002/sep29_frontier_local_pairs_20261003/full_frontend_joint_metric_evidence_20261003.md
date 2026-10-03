# Retained metric-joint candidate: integrated frontend evidence

## Decision rule (user correction, 2026-10-03)

An incomplete 10 mm result does not by itself reject the direction. Keep a
candidate with majority improvements on the fixed failure-five if all fixed
passing-five retain complete comparable coverage and max ATE <= 10 mm. Mixed
or incomplete results remain unresolved, not evidence that the whole direction
is useless. Production and final full25/new-capture acceptance are unchanged.

## Capability implemented and checked

- Default-off hook in the real MASt3R calibrated GN graph; no tracker admission,
  checkpoint, raw-frame clock, motion gate, objective weights or correction cap
  changes. Original native path remains unchanged when disabled.
- Raw-stereo metric relative factors participate in the native graph solve.
  RIGHT depth is measured on the RIGHT pixel grid, not relabelled LEFT depth.
- Explicit code/source hashes, zero-factor visible failure, exact pin1, no GT
  input, no VINS metric-objective conflict and no silent baseline fallback.
- Fresh combined regression suite: **65 passed** using the installed toolchain
  Python (2026-10-03). Independent code review approved bounded experimental
  execution after fixing the zero-factor/hash and RIGHT-clock issues.
- Adapter versus retained frozen source replay: RIGHT613 and LEFT877 both
  byte-exact, max absolute difference 0, pin exact. These are source-equivalence
  checks, not trajectory accuracy.

## First complete-input replay: coverage failure, NOT ATE rejection

Output: `full_metric_joint_frontend_v1/20260929_take02/right/`.
Input contains 1199 authoritative RIGHT frames paired with LEFT raw stereo.
The official checkpoint and full single-thread config were frozen. 24 graph
solves actually used the candidate. Main consumed the full recording and exited,
but tracking entered unsuccessful relocalization at frame588; only 588/1199
source poses were tracked, with an untracked tail of 611 frames. The converter's
strict complete-coverage gate correctly raised an error instead of treating
tail interpolation as real visual observations.

The original RIGHT source also has 588 tracked poses. The same count is an
inherited-coverage hypothesis, not proof that every graph state/reason is
identical. No complete trajectory ATE exists for this candidate yet. Retain
the candidate and investigate coverage separately; do not discard its local
geometry improvement or promote it to production from these proxy tests.

## Backup boundary

Actual toolchain code lives in a separate repository, not ROOT. Its hook and
RIGHT-depth changes were committed as
`58c9f619d930566e79b42b16fe4ef3ebe3a79bfd` on
`codex/relocalization-recovery-20261001`, pushed normally to the user's owned
`sencangWei/MASt3R-SLAM` fork. A fresh remote archive has byte-identical changed
files; 17 hook/RIGHT-depth tests passed against the restored source. Installed
native binaries, model weights and source recordings are external prerequisites,
not claimed to be included in that source backup.

ROOT adapter/runner/tests and these small explicit evidence files require a
separate owned-remote commit and clean-source restoration check. No broad
`reports/` staging, forced push, unrelated dirty changes or raw tensors/images.

## Independent RK3576 deployment status

Authoritative pre-QR source and historical 0.2.5 metadata are recovered. Fresh
offline tests: 34 source tests and 5 checksum-safety tests pass. Target SSH still
returns `No route to host`; the actual ARM candidate/native runtime is absent.
Live identity, release/rollback, recording-idle, deployment and no-motion HIL
checks are explicitly unverified/skipped. No board service/network/config/QR or
recording mutation has occurred. Source archives are not ARM release packages.
