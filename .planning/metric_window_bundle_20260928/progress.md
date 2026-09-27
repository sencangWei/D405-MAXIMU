# Progress

2026-09-28: user approved proposed window-metric candidate. Read repository
AGENTS, memory index, karpathy-guidelines, systematic-debugging, and complete
planning-with-files instructions. Started bounded read-only existing-code lookup
with installed executor fallback after explorer's unsupported-model failure.
Created scoped plan before implementation. No production source edits, no new
trajectory run, no GPU/model training, no claimed accuracy gain.

Inventory complete: no shared-landmark pixel-level stereo BA exists; pair-PnP
and displacement-graph scaffolds reusable but not substitutes. Drafted minimal
contract with estimated bias and fixed formal noise inputs for independent
review. One malformed apply_patch failed verification with no edits; corrected.

Implemented isolated shared-landmark BA and image-only observation extraction.
Synthetic extraction initially failed (1/6): forward stereo was exactly -18px,
but unseeded reverse LK jumped a 25px texture period, rejecting all60 tracks.
Inverse initialization on the stereo backward pass gives60 valid tracks and
~0.000085px median closure; no gate relaxation. This seeded closure is NOT an
independent matcher. SGBM LR/depth/epipolar checks remain. Finite disparity seed
guard prevents NaNs passed to OpenCV. New core/control/extractor tests23PASS.

First frozen observation batch controls_ten_v1 completed allten, five fixed
windows each.36/50 solver-accepted; heldout reprojection improves26/36. Median
withheld-pixel RMSE0.546→0.452px, mean2.792→2.648px. These are NOT ATE errors.
14 rejections:6 insufficienttracks,3 visualinit,3 training-onlyPnP,2 max-iteration.
Several accepted windows remain bad: dev1window4 withheld0.326→1.876px,
endpoint changes21.09mm, biasnorm~0.072rad/s; dev2window4 withheld49.77→51.87px.
Therefore do NOT integrate this prototype into formal graph or claim10mm.
Diagnose raw correspondences/visual initialization and review first. Known
diagnostic bug: caller-supplied bias Jacobian was mislabeled exact (solver
unchanged); correcting separately after batch to preserve sourcehash provenance.

Review fixed: bias derivative always first-order/not exact; fixed2px stereo
P95/support,5deg gyro,0.01rad/s percomponent bias guards with diagnostic states
retained on rejection. Holdout extraction no longer performs all-track PnP.
Short-window bounds test caught count96 lastindex96: reject96, accept97;
no wrapping negative/out-of-range NumPyindices. Targeted27testsPASS; extended
existing stereo/fusion+new tests136PASS. controls_ten_v2 complete:31accepted,
25heldoutimprove, median0.463→0.397px. No graph replay/production promotion.

Second review identified exact boundary error: training-only PnP rejected gross
pixel observations but BA reinstated them. Fixed per-observation admission,
source stereo retained, no blanket track blacklist or frame deletion. Fixed
existing2px threshold (no sweep). Tests added holdout sentinel,10% grossoutliers,
per-view preservation and support collapse.30newtestsPASS before next depth-
bias test. controls_ten_v3 completed fixedallten/50windows:37accepted,27heldout
improvements; withheld median0.552→0.432px. Separate evaluation-only scorer,
run AFTER estimation with unchanged official reference/time/extrinsics:37 local
endpoint comparisons,26improve, median0.721→0.392mm,max10.425→3.332mm. NOT full
trajectory ATE;13windows refused, no productionpromotion or claimed10mmSLAM.

Final independent review:0HIGH, safe as observation-only prototype. Repaired
scoring script selfhash and input provenance. controls_ten_v4_manifest records
graph/stereo/calibration/session/IMU hashes and selected decoded grayscale
frame hashes.37 accepted endpoints equalv3 bit-for-bit, same13 rejections.
New-only31tests and extended140testsPASS; syntaxPASS. Algorithm code remains
isolated; plan phase5 (full graph integration/ten-case ATE) still pending.
