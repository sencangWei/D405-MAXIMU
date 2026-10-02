# RK3576 Web 0.2.5 recovery and deployment

## Goal

Resume the shared-session task, recover the authoritative source/candidate state, deploy the Web timer and safe-delete release to `192.168.113.161` without QR changes, preserve all recordings and the prior release, and collect no-motion acceptance evidence.

## Success criteria

- Authoritative source revision and release contents identified.
- Candidate integrity and release manifest pass fresh verification.
- Exact target identity, active release, service units, rollback target, and recording-idle state confirmed.
- Versioned release deployed without overwriting recordings or configuration.
- Web timer is monotonic, safe-delete flow is present, service health is clean, and a bounded no-motion recording smoke test passes.
- Rollback remains available and all skipped checks are explicit.

## Phases

1. **in_progress / live BLOCKED** — Authoritative pre-QR source recovered from the owned remote; target identity/access remain unverified (`No route to host` on fresh SSH probe).
2. **in_progress** — Historical 0.2.5 package metadata recovered and checksum self-list failure reproduced synthetically. Actual ARM candidate/native runtime still absent; source archives are not deployment packages.
3. **pending / partially verified** — Fresh authoritative-source tests 34 PASS and checksum safety tests 5 PASS. Real candidate preflight and current board rollback evidence cannot yet run.
4. **pending** — Present exact service/release mutations and execute the approved version switch.
5. **pending** — Run post-deploy Web and bounded no-motion HIL checks; accept or roll back.

## Constraints

- QR provisioning is excluded.
- Do not delete or modify existing recordings.
- Do not install packages, edit network/udev settings, reboot, or command motion.
- Preserve the current release and configuration until acceptance closes.

## Errors encountered

| Error | Attempt | Resolution |
|---|---:|---|
| Shared-session worktree `/home/robot/worktrees/d405-umi-rk3576` no longer exists | 1 | Recover state from local Codex records, artifacts, and read-only target inspection before rebuilding anything. |
| `http://192.168.113.161:8766/` accepted TCP but returned no bytes within 5 seconds | 1 | Treat the Web service as unverified; inspect service state through authenticated target access before drawing conclusions. |

## Current boundary (2026-10-03)

The old absent-worktree/network observations above are historical. The worktree
now exists; exact pre-QR source is `d17b2c2b6dca9ac967d7b9a0d02573e18b01ce0d`.
The fresh target SSH probe reports `No route to host`. No deployment, selector,
service, recording, configuration, package or hardware mutation has occurred.
Recovery tool/evidence is backed up to the owned `sencang` remote on
`codex/rk3576-v025-recovery-20261003` (commit `885ba10409247d1b5245bd0ddfb208c5c22655a3`),
with remote-restored files compared byte-for-byte. This is not a deployed release.
