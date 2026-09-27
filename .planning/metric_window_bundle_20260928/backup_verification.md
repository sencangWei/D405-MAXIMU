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
