# Remote backup verification

Initial observation prototype backup:

- Actual code lives in `/home/robot/ego_vio_humble/scripts`, not the calibration
  kit. Production dirty work preserved; isolated staging repo
  `/tmp/ego_vio_fourway_backup_20260926` was clean before copying named files.
- Writable remote checked: `sencang`,
  `https://github.com/sencangWei/D405-MAXIMU.git`. Dead origin not used.
- Normal pushed branch: `codex/stereo-window-bundle-20260928`.
- Initial commit: `d725776f00d5ff65d6cdf69b27475e751295950c`.
- Fresh empty repo `/tmp/ego-stereo-window-restore-IlOcde` fetched the remote
  branch, checked out FETCH_HEAD. No local-copy restoration or force push.
- All58 commit files compared byte-for-byte against the clean remote checkout;
  local/remote commit IDs matched.
- Freshly restored old+new targeted stereo/fusion suite:140testsPASS.
- Full raw recording/GPU frontend replay not performed; only explicitly listed
  prototype code/tests/selected diagnostic evidence are backed here.

That initial commit did not include the later full-rate propagation / joint-
geometry repair. The completed follow-up is backed separately:

- Algorithm/evidence commit `8514a442d4877f7cf60c4a3e9f8e114101d47aa4`, same
  owned remote branch, normal fast-forward push (no force push).
- Remote fetched into the same initially-empty verification repo and checked
  out. All83 prototype code/test/evidence files since `c68b2efb` match both the
  backup commit and main workspace byte-for-byte. Remote/local IDs match.
- Restored targeted suite146PASS. Local new-only37PASS; syntax checksPASS.
- v6 observations46/50; local comparisons only, not whole-trajectory ATE.

Any following documentation-only commit records this proof; it does not change
estimator code, tests or recorded observation/evaluation data. Full raw-data/GPU
replay and production deployment are still not verified.

## Phase5 metric-window graph integration

- Commit `c5953e24f83aaf5a9d6acb8e236cb2ea31c1a48e`, normalfastforward push on
  sameowned `sencang/codex/stereo-window-bundle-20260928`.
- Fresh emptyremote-only restore `/tmp/ego-metric-graph-restore-0Wz3x7` fetched
  exactbranch. Commit IDs matched, all9newchangedcode/test/planfilesbyte-identical.
- Restored168old/newtestsPASS in1.50s. This is source/test restoration, not a
  recording/GPU replay. Completed allten graph evidence will be copied in a
  subsequent selected-evidence commit; do not infer it from this source commit.

- Completedgraph/evidence commit `aead1d995f0eb5361da65fedadda4a07f24c36df`,
  sameownedbranch, normalfastforwardpush. Restored170changedcode/evidencefiles
  byte-identical fromremote; restored171testsPASS in1.38s. Containsallten graph
  trajectories,precisionreports,command/hashmanifests androtatableplots.
  Laterdiagnostic additions and ongoingfullcoverageoutputs requirefollow-up.

## Phase6 fullcoverage evidence restoration

- Frozen all-ten controls/graph/evaluation/plots commit
  `18c37c4a76685a71798ba60a5d9eeecf68e3e9ee` normally pushed to owned
  `sencang/codex/stereo-window-bundle-20260928`.
- Exact178changed files only: selected fullcoverage evidence folders and three
  owned plan/report documents, not `git add reports/` or unrelated changes.
- Remote fetched into clean `/tmp/ego-metric-graph-restore-0Wz3x7`; restoredHEAD
  matches backupHEAD. All178files byte-identical with main and backup.
- Restored171targetedtestsPASS1.35s; main171PASS1.50s. This validates source
  restoration and tests, NOT production deployment, GPUfrontend rawrerun, or
  new independentcapture generalization.
- A subsequent documentation-only commit records this proof and clarifies the
  fullcoverage adapter. It does not change frozen estimator or evidence data.

## Phase7 all-ten observability diagnosis restoration

- Code, tests, both real runtime-paired proofs, all590 raw diagnostics, current
  local evaluation, currentv2 association and diagnosis report were normally
  pushed to owned `sencang/codex/stereo-window-bundle-20260928` in commit
  `112452548721035561bdbb0d9bfa4c609609c0b3`. Only53 explicitly named selected
  files copied;49 changed in this commit. Never staged the reports root.
- Actual code lives in humble scripts and the isolated planning directory;
  calib-kit is not the algorithm repo. Backup repo checked clean on intended
  branch; sencang URL is `https://github.com/sencangWei/D405-MAXIMU.git`.
  Dead origin and unrelated dirty production work were untouched.
- Clean remote-only restore `/tmp/ego-metric-graph-restore-0Wz3x7` fetched that
  branch. Remote/local commitIDs matched; all49 changed files byte-identical
  to both main workspace and isolated backup. No local-copy restore.
- Fresh restored source/regression suite246PASS2.06s. Main expanded248PASS2.33s
  includes two pre-existing `test_mast3r_imu_scale.py` tests absent from this
  selected backup; do not claim those two were remotely restored.
- First backup-suite invocation mistakenly named that absent test and exited4
  without running tests. Corrected selected suite246PASS before commit and
  again from remote. No test or algorithm gate was weakened.
- Independent final reviewAPPROVE/no blockers. Strict identity staysfailed
  across NumPy versions; opt-in numerical reproducibility explicitly reports
  false exactidentity. No production deployment, rawGPU rerun or new recording
  generalization is claimed. Accuracy target still9/10PASS/fresh4max14.016mm.

A subsequent documentation-only commit records this completed restoration;
it does not alter hashed census, paired proofs, helper code or trajectories.

## Phase8 adjacent-window and seam-born observations

- First six changed join/control/test/plan files backed in commit
  `52de61983363a0657d18c1b1db31732e26ee0307`, normally pushed to the same
  owned `sencang/codex/stereo-window-bundle-20260928` branch. Clean remote-only
  restore compared all six against both main and isolated backup byte-for-byte;
  freshly restored263testsPASS3.02s (main265includes the existing two tests not
  selected for this backup).
- New seam-born helper/harness/tests and frozen v1 all-ten census/evaluation,
  plus selected documentation, backed in commit
  `a55962f3e659daf8d3a835a6832548632f3b1bcd`. Exactly22changed named paths,
  not reports root. Normalpush, no force. Remote-only clean restore compared all
  22 against main and isolated backup byte-for-byte; restored278PASS3.82s.
- Algorithm files actually live in humble/scripts and humble/.planning;
  calib-kit is not the algorithm repo. Targetbranch and writable remote checked
  before copying/staging. Initial `.git` directory guard failed cleanly because
  backup is a git worktree with a `.git` FILE; corrected to git rev-parse before
  any write. No unrelated working tree changes were staged or reset.
- V1 refused promotion; v2 census is still running at this proof snapshot.
  This backup does NOT claim completed v2 evidence, full-trajectory accuracy,
  production deployment, GPUfrontend rerun or independent fresh capture.

- Completed v2 all-ten evidence and analysis helper normally pushed in commit
  `093d44d65f01868a4ecaf7f11ac5f9835bd29085` (22changed exact paths). Remote-only
  restore matches all22 backup snapshot files;21 remain identical to current
  main workspace, while progress.md was intentionally appended afterwards.
  Restored285testsPASS3.73s. This backs v2 LOCAL comparisons, not full-trajectory
  ATE or the still-running full-window census.

- Full-window adapter/test and two selected progress/proof documents backed in
  commit `c4fc7c95db8defc1230b1c87bb9526f86a81f4de`, same owned branch, normal
  fast-forward push. Remote-only clean restore compared all4 changed files
  byte-for-byte to main and isolated backup; fresh restored50 targeted tests
  PASS1.79s. The running full census is not complete/backed by this commit.

- Post-freeze local scoring helper/tests and selected progress/proof documents
  backed in `fe0e98534d92db663bcfc71a8b56ae07088a72e3`, normal push to the same
  owned branch. Remote-only restore compared all4 changed files byte-for-byte
  with main and isolated backup; freshly restored56 targeted testsPASS2.35s.
  This does not claim the running full census has completed or scored yet.
