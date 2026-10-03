# Onboard metric relative-pose joint objective

Predeclared before solve. Source-only diagnostic, no Tracker input, no ATE claim.
Mask-only v3 was actually rejected on all three RIGHT contexts. This experiment
changes objective structure rather than another threshold, weight or depth-shape
conditioning. No production code/.so is replaced.

For original pairs accepted by original bidirectional stereo PnP/cycle, define
measurement Z_ij mapping native camera-i to native camera-j as:
R_ij from raw stereo PnP, t_ij_native=t_ij_m/r_j, scale=r_i/r_j,
where r_k=median(D_stereo/Z_native). Residual LogSim3(Z_ij^-1 T_j^-1 T_i).
Metric pose information derives from the accepted source stereo PnP projection
Jacobian, original observation sigma_pixel floor, and measured log-ratio MAD
dispersion; no tuned factor multiplier or visual objective normalization.
The independent measured dispersion must remain documented, not treated as a
calibrated probabilistic guarantee; depth uncertainty/correlation is a risk.

Add these factor H/g to the original hash-pinned CUDA calibrated projection H/g.
Keep native dense factors/Xs/Q/C/all edges unchanged. CPU float64 small-system
Cholesky uses original lower-triangle semantics, no damping/clipping, negative
gradient solve; original CUDA left-Sim3 retraction, original iteration/termination
settings, pose0 fixed. Repeat solve and check original production control exact.

Run the same frozen seven contexts (THREE independent recordings) first. RIGHT
max accepted chronological PnP disagreement must be <2deg on all three; passing
controls must not regress by >.05deg. On controls reject if original common-mask
dense objective more than doubles or common support falls below99% of control.
These are candidate falsifier criteria, not altered trajectory acceptance gates.
Report metric and native objectives separately, full provenance and failures.
Technical failures are not negative scientific results. No silently discarded
record/pair; retain unavailable/rejected PnP status in measurement report.

If this candidate survives, regenerate full affected frontend results then
fixed failure5 + passing5 with maxATE10mm unchanged. Full25/new recordings after
stable10. If it fails, do not sweep metric weights; inspect source pointmap
consistency and actual onboard observability before any different source fix.

## User policy correction, before this trial executes

Failing a source geometry proxy is NOT proof that an entire direction is useless.
Keep partial improvements/version evidence; strict maxATE10mm remains the final
acceptance rule, not a condition for retaining incremental progress. Frozen graph
rotation changes are diagnostic signals, not a cross-record trajectory verdict.
The runner therefore reports geometry criteria separately from NOT_EVALUATED_ATE.
A real candidate's fixed10 comparison decides retention: majority failure-group
ATE improvement plus no passing-group crossing10mm can be retained as an
incremental baseline even with remaining failures. Preserve mixed results and
technical failures, do not invent a rollback result from missing ATE evidence.
