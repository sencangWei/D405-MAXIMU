# Supported correspondence membership causal falsifier

Predeclared before solve, 2026-10-03. Diagnostic only, no external reference
input, no ATE claim and no production promotion. Seven frozen graphs represent
THREE independent recordings, not seven recording passes.

Hypothesis: dense matches inconsistent with independently accepted onboard
stereo PnP pull the native calibrated Sim3 objective away from physical motion.
Previous conditional statistics are not causal evidence; this intervention is.

Use every original unordered graph pair with both directed edges. Recompute
the original bidirectional PnP/cycle gate on frozen source stereo images.
Rejected pairs retain all original masks. On accepted pairs remove ONLY valid
matches with BOTH metric depths whose reprojection under the accepted transform
exceeds the original 2px PnP threshold. Unsupported and consistent matches stay;
all frames, edges, pointmaps, scales, confidences, weights, thresholds, iterations
and original production CUDA solver remain unchanged. Clone mutating gate masks.
This is not RANSAC-inlier membership nor an edge-deletion experiment.

Run the SAME seven original graphs, exact original-control replay against the
frozen poses, repeated variant, pinned pose0, source/image/config/hash guards.
First three RIGHT graphs, then LEFT contexts, then two passing controls.

Candidate criterion (NOT final precision acceptance): each of right587/right592/
right613 has max original accepted chronological PnP rotation disagreement <2deg;
each passing control's max regresses no more than .05deg. No accepted directed
edge may remove >50% of its stereo-supported matches or retain <100 supported
matches (existing PnP minimum). Missing/rejected execution cannot become PASS.
Report per-edge removal counts, geometry pre/control/variant and source hashes.

If rejected, stop this membership-pruning family; investigate a true metric
relative-pose objective rather than another threshold or conditioning sweep.
If geometry survives, regenerate affected full frontend outputs BEFORE fixed
failure5 + passing5 ATE; only after stable fixed10 run full25/new independent
capture. Failure samples and the max10mm precision requirement remain unchanged.
