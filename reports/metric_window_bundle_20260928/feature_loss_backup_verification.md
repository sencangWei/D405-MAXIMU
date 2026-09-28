# Selected feature-loss diagnostic backup verification

- Repository: `/home/robot/ego_vio_humble`; original dirty work preserved.
- Isolated backup: `/home/robot/ego_vio_feature_loss_backup_20260928`.
- Branch: `codex/feature-loss-diagnostic-20260928`.
- Owned writable remote: `sencang`, `https://github.com/sencangWei/D405-MAXIMU.git`.
- Verified code/evidence commit: `2473057d275738c8d1772b9d45a275dff08aaebc`.
- Clean restore obtained by initializing an empty repository and fetching that
  remote branch, not by copying local Git objects:
  `/home/robot/ego-feature-loss-restore-J9l9eE`.
- All 27 selected files compared byte-for-byte between main, isolated backup,
  and remote-only restore. PASS.
- Restored diagnostic/geometry regression suite: **229 passed, 3.83 s**;
  isolated backup: **229 passed, 3.45 s**.
- Original CSV CRLF preserved. Diff whitespace check used a command-local
  `core.whitespace=cr-at-eol`; no global setting or evidence rewrite.

Selected scope: two new diagnostic source files, their two test files, three
planning documents, five diagnostic evidence files, three frontend replay
summaries, and four files from each of three replays (match log, frontend log,
run manifest, full-rate trajectory). No dataset symlink, raw recordings, model
weights, keyframe images, unrelated dirty source, or broad `reports/` staging.
Input provenance retains references to original raw recordings; this is not a
backup of those recordings or a new self-contained trained SLAM release.

This note and the subsequent report frame-index clarification are documentation
follow-up. The original 27-file verification and test result above refer to the
named code/evidence commit. No trajectory accuracy improvement is claimed;
full maximum ATE remains **13.801442 mm**, not below 10 mm.
