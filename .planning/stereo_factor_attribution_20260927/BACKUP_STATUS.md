# Stereo observation diagnostics — 2026-09-27

This is an experimental/reproducibility branch, NOT production rollout and NOT
a claim of stable all-frame10mm accuracy. Frozen production remains untouched.

Initial helper/harness backup e610973 was pushed to owned remote sencang on
codex/stereo-bidirectional-contract-20260927, fetched into a clean restore, and
verified by eight component hashes and41passing tests.

Concrete gap: accepted SIFT fallbacks bypass LK reverse acceptance; missing
reverse disagreement is credited aszero. The opt-in inverse-vector helper keeps
forward scale/vector intact. No existing function default or CLI was changed.
Experimental harness mirrors existing LK scalar reverse gate plus vector gate;
source scales/quality snapshots frozen, derived counts separately recorded.

The complete ten-case experiment is REJECTED for production:8PASS/2FAIL, same as
baseline. Fresh2 max16.151→14.382mm but average worsened4.164→4.648mm; fresh4
max16.181→16.643mm. All original score samples/SE3-no-scale alignment preserved.
No GT in optimization, no frame deletion, neural training, or closed-family sweep.

Raw-gyro PnP diagnostic completed ten cases/166time-stratified measurements,
using raw-calibrated-gyro delta, raw-MASt3R attitude control, and free-PnP control.
Formal fixed td applied once. All frame/input/inlier assertions passed. Closure
improves, but reprojection worsens and VINS disagreement is mixed; neither
closure nor VINS is truth. No gyro-fixed production mode was introduced.

41targeted tests freshly pass. The updated snapshot contains diagnostic sources
and small explicit evidence paths; original recordings, image datasets, and full reports tree
are intentionally NOT staged. These are required external inputs for replay.
The new commit must be verified by fresh remote fetch and component comparison
before reporting that this updated snapshot is backed up.
