# Findings

- Shared thread title: `查找采集转存预览设想`; it contains 163 exported turns.
- The latest exported state verified a ~30 minute recording: 56,809 packets for each of RGB/left IR/right IR, 757,498 STM32 packets, approximately 3.78 GB, complete SHA-256 verification, complete HEVC parser counts, and RGB keyframe decoding through 31:33 with return code 0.
- Source tests for Web timer/safe delete previously reported 34 passing tests. QR provisioning was explicitly deferred.
- The first `0.2.5` candidate was not uploaded or deployed because its `SHA256SUMS` file was generated inside the candidate tree and therefore listed itself; the planned correction was to generate the checksum list outside the candidate tree and rebuild.
- The shared-session source path `/home/robot/worktrees/d405-umi-rk3576` is absent in the current filesystem.
- `192.168.113.161` responds to ICMP and has TCP ports 22 and 8766 open; port 8766 did not produce an HTTP response within five seconds.
- Current local SSH key was not accepted for users `root`, `pi`, or `linaro`; exact prior target credential path is still being recovered.
- Existing local directories named `EGO-v025-*` and `ego-recorder-release-0.2.5-*` are July artifacts for a different recorder release and must not be reused for this September RK3576 Web task.
- Agentmemory returned no matching stored observations for the current RK3576 `.161` task.

## Fresh recovery correction (2026-10-02)

- The previously absent-worktree statement is stale: `/home/robot/worktrees/d405-umi-rk3576` now exists. Read-only recovery identifies branch `umi-rk3576-collector-adapter`, revision `0aba31df56b9157519aa5818a637259d520211ef`, a clean worktree, and user-owned `sencangWei/D405-MAXIMU.git` origin.
- Source is under `rk3576/collector`; the recovered controller identifies itself as `0.2.4-umi`. A verified authoritative Web `0.2.5` candidate/release manifest has not yet been recovered; July packages remain excluded.
- Target identity, active release, actual service units, rollback target, recording-idle state and accepted SSH authentication remain unverified. No service switch, upload, recording mutation, network change or motion command is authorized by the recovered evidence.
- Deployment remains blocked at read-only recovery; the independent offline SLAM optimization does not depend on this target and continues.
- Fresh recovered-source tests: `python3 -m pytest -q tests/test_rk3576_umi_collector_adapter.py` reports 24 PASS (not the historical 34-test timer/safe-delete candidate). The recovered `umi_preview_web.py` and that test file have no timer/delete/confirmation implementation. Monotonic controller timing exists separately. This strengthens the source-recovery gap: the clean recovered branch cannot be relabeled as the authoritative Web 0.2.5 candidate.

## Independent bounded recovery confirmation (2026-10-02)

- The recovered repo has only the collector-adapter local/remote branch, no stash and only a clone reflog entry. All-ref commit-message search for Web 0.2.5/timer/safe-delete/checksum packaging finds no candidate.
- One unreachable commit exists but changes only the sub-10mm SLAM README/images, not RK3576. Bounded candidate/manifest searches found no relevant 0.2.5 package.
- Source/controller, test and README independently identify 0.2.4-umi. An authoritative timer/safe-delete candidate, target service/release identity, idle state and accepted SSH access remain absent. Status BLOCKED; no upload, deployment, service transition, recording mutation or QR change occurred.

## Fresh remote recovery correction (2026-10-03)

- A fresh fetch of the user-owned collector-adapter remote recovered newer history. The earlier local/all-ref search was incomplete because its remote ref was stale; it does not prove the authoritative source was lost.
- Authoritative pre-QR Web timer/safe-delete source: `d17b2c2b6dca9ac967d7b9a0d02573e18b01ce0d`. Provenance commit `383cf1dfad30c6039e66ead7ae6796bcac50e1e3` records `rk3576-umi-0.2.5.tar.gz`, SHA-256 `d15445127fd8927da9ceba206dd74e0a683a326312a974c7626282e9e44b8850`, 11,177,964 bytes, aarch64, and inherited `0.2.3-fix1` native runtime. These are historical metadata, not fresh package or target acceptance.
- The newest remote tip is a later QR-bearing release and is excluded. The original worktree remains on its clean 0.2.4 revision; no checkout, reset or target downgrade occurred.
- The actual authoritative archive/native runtime remains to be located or reconstructed and freshly verified. Source recovery alone does not establish candidate integrity.
- Fresh bounded read-only SSH probe to `pi@192.168.113.161` failed with `No route to host`. Exact current target identity, release, units, rollback and idle state remain unverified. No upload, service mutation, recording change, QR change or motion occurred.
- Offline source recovery and tests can continue; live deployment is blocked until target reachability/access and candidate preflight are established.

### Offline verification completed

- Exact source exported without switching the original worktree: `/tmp/rk3576_v025_recovery.sh4wE2/repo/repo-d17`, source commit `d17b2c2b6dca9ac967d7b9a0d02573e18b01ce0d`. Fresh root rerun of adapter and Web source tests: **34 passed in 2.11 s**. This includes timer and safe-delete behavior, but is not board/HIL acceptance.
- Full repository source archive SHA-256: `2fbe1a3ba45cbdf7f8fceebeb0fcbe8a944941be00a8f979aa3df7d3b2947a54`; collector-only source archive SHA-256: `5a5f42d76bf6d13cc3842f382c14ae55d8d2ca17018af57171e97cb15282d765`. Neither source archive is the authoritative ARM deployment candidate.
- Synthetic checksum fixture reproduces self-listed `SHA256SUMS` strict-check failure and verifies external generation excluding itself succeeds. The manifest avoids self-hashing while its file is included in `SHA256SUMS`. Fresh fixture/safety tests: **5 passed in 0.04 s**; existing paths and symlink roots are refused before writes. This is a packaging invariant reproduction, not a replay of the absent historical failed archive.
- Actual `0.2.5` release archive, inherited `0.2.3-fix1` native executables and ARM Python/RealSense runtime remain absent locally. Candidate manifest/checksums, target identity/idle/rollback, service transition and no-motion recording smoke test are **not verified**. No version selection, service operation or deployment was performed.
