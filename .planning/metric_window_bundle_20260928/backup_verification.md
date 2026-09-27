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

The subsequent full-rate feature-propagation experiment is pending and is not
included in that initial commit. Do not treat140tests or37windows as a formal
10mmSLAM acceptance.
