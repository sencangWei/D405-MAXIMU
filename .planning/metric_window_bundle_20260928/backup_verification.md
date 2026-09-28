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

- Read-only self-track diagnostic, invalid v1 retained with explicit supersession,
  corrected v2 evidence/tests/report and selected documents backed in commit
  `19b5c4fd088f64f432f146a97aa451b732d86f1e`. Eight exact changed paths,
  normal push to owned sencang branch; clean remote-only restore matched all8
  byte-for-byte against both main and backup. Restored62 targeted testsPASS1.88s.
  No foreground mask or estimator modification is claimed.

- Full all-ten seam control/local-evaluation evidence and analysis summary tool
  backed in `49301b32f198e351915871bd1232e24d21201580`, 22 exact named changed
  paths, normal push. Clean remote-only restoration compared all22 with main
  and isolated backup byte-for-byte; fresh restored28 targeted testsPASS0.31s.
  Native fusion, existing metric wrapper and complementary algorithm hashes
  also match the backup sources. This is still LOCAL evaluation, not new ATE.

- New isolated seam-pair wrapper, all-ten three-branch graph runner, tests and
  two proof/progress documents backed in `4a9d4b0a54dfb7a6bf07bdd8ca28faf8066f143f`.
  Six exact changed paths, same owned branch, normal push. Remote-only clean
  restore matched all6 byte-for-byte against main and backup; fresh324 tests
  PASS4.11s. Main326 includes two existing unselected imu-scale tests. No new
  full-trajectory result was included in this source-only backup commit.

## Full graph control evidence preserved

- Completed all-ten/three-variant graph evidence, all 180 logs, official scores,
  final trajectories, full report and pending-status guard backed in
  `afc8af6e30e7c8bba0bf697d26e9edfa87d07269`. 628 exact changed paths,
  including only the owned `seam_graph_full_ten_v1` subtree (622 files,
  26,849,477 bytes), never the reports root. Normal fast-forward push to the
  same owned sencang branch.
- Clean remote-only restore fetched that exact hash; all628 changed files
  byte-identical to current main and isolated backup. Fresh restored325 tests
  PASS4.00s; main327 PASS4.22s (two existing imu-scale tests not selected).
- A broad staged whitespace check stopped before commit because raw CSV files
  have their original CRLF line endings. No frozen evidence was normalized:
  code/docs whitespace checks and byte-identity checks passed instead. This
  operational check failure was not a solver/data failure.
- Numeric result remains joint9/10PASS/max13.801442mm, targetNOTmet; four
  official threshold-failure exit3 outcomes retained. This backup is not a
  production promotion or new learned model.

## Nine-center prototype source restoration

- Original diagnostic core, fixed-schedule adapter, tests and selected planning
  documents were normally pushed in `fd55c29db0efe9e417c4d4a9a31793171c1712c2`,
  nine exact changed paths on the same owned sencang branch. Remote-only clean
  restore matched all nine against main and isolated backup byte-for-byte.
- Fresh restored regression suite: 342 passed; main: 344 passed, including two
  existing unselected IMU-scale tests. No production or calibrated covariance
  claim. This source commit preceded the 50-pair computations.
- Later optional independent joint_pair aggregation fix and post-freeze scorer
  now pass 27 targeted tests; expanded main regression suite **354 passed in
  4.49s**. They and the completed raw/finalized diagnostic evidence still need
  their own remote restoration; the source-only commit does not back them.

## Completed sampled diagnostic and scorer restoration

- Raw and finalized 50-pair outputs, original producer source snapshots,
  aggregation fix, post-freeze scorer/tests and selected report/planning files
  were normally pushed in `271622a3e7957f7a6a7514a84f81e398003969b1` to the
  same owned sencang branch. Exactly 37 changed files, no reports-root staging.
- Clean remote-only restore fetched that exact commit and compared all 37
  against both main and backup byte-for-byte. Fresh restored regression suite:
  352 passed (main 354 includes two pre-existing unselected IMU-scale tests).
- Numeric diagnostic is local relative geometry only, not full ATE. Original
  production source, all-ten source hashes and full-trajectory results remain
  unchanged; the 10 mm accuracy target remains unmet.

## Pure grouped row prototype restoration

- Two new source/test files, local affine contract, all42 real-input algebra
  preflight and selected report/planning updates were normally pushed in
  `4ff4514241c4d41174dc7435da3ca91bf4f92547`, eight exact changed paths, to
  the same owned sencang branch. No native production algorithm file changed.
- Clean remote-only restoration compared all eight against main and backup
  byte-for-byte. Fresh restored expanded suite: 359 passed; main 361 includes
  two existing unselected IMU-scale tests.
- This backs up grouped-row construction, not a real graph solution, new ATE,
  covariance calibration or production admission. Full target remains unmet.

## Full-census runner source restoration before launch

- New full-shape adapter/test, prelaunch evidence and selected planning files
  were normally pushed in `c0e9c41b7636d1562c7c58e2e5abcbbce43730bb`, six exact
  changed paths, same owned sencang branch. Actual code location checked in
  humble/.planning; no origin push, reports-root staging or force push.
- Clean remote-only restoration compared all six byte-for-byte against main
  and backup; freshly restored regression suite 376 passed in4.54s, main378
  passed in4.65s (two existing unselected IMU-scale tests).
- Independent prelaunch APPROVE applies ONLY to the UMI-only full290 diagnostic
  capture. Sources14/inputs62 and all ten actual raw frame counts were verified
  before launch. This backup does not contain completed full290 outputs yet.

## Full-shape local evaluator source restoration

- NEW evaluator/test, row-replacement design contract and selected proof/progress
  documents were normally pushed in `2aeaebaea85436d2a2f0558588879807d1d051f3`,
  five exact changed paths on owned sencang branch. Existing production and
  live-census producer sources were not changed. No reports-root stage/force.
- Clean remote-only fetch/checkout into `/tmp/ego-metric-graph-restore-0Wz3x7`
  matched the exact commit; all five changed files byte-identical to main and
  backup. Fresh restored382testsPASS8.62s; main384PASS9.32s includes the two
  existing unselected IMU-scale tests. Restore working tree remained clean.
- Rotating-lever BODY/camera regression was corrected before any realGT use by
  this evaluator. Full290 capture remains running; this source backup does not
  claim completed census/evaluation/new ATE, real grouped graph or promotion.

## Pure native-system row-splice source restoration

- NEW row-splice helper/test plus selected contract/progress/proof documents
  normally pushed as `cc9d6c708199bd8186aaf92d8ba4ed41ae244205` on the same owned
  sencang branch, five exact changed paths. No native/producer edits, reports-root
  stage or force push. Source-only independent review APPROVE.
- Remote-only clean fetch/checkout matched the exact commit. All five changed
  files byte-identical to main and isolated backup. Restored396testsPASS6.75s;
  main398PASS6.86s includes the existing two unselected IMU-scale tests.
- Includes actual nine-state producer interop, explicitly stored sparse-zero
  nonmutation and no-op identity. This is NOT real native consumer execution,
  new graph trajectories/ATE, completed full290 evidence or production promotion.
