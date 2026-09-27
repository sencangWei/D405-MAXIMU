# Experimental bidirectional motion contract — 2026-09-27

This branch backs up a tested, opt-in helper and reproducible diagnostic tools.
It is NOT a production rollout or a claim that all trajectories meet10mm.

Concrete gap: accepted SIFT fallbacks bypass the reverse checks applied to LK;
missing reverse disagreement is credited as zero by graph confidence. Existing
bidirectional acceptance checks scalar scales, not inverse 3D displacement.

New validate_bidirectional_motion helper checks the inverse displacement in a
common camera_i frame. It keeps forward scale/vector intact and uses fixed
relative0.20 AND absolute8mm gates; existing functions/defaults unchanged.
The experimental harness additionally mirrors the existing LK scalar reverse
gate for SIFT; it does not replace or loosen that old scalar contract.

Fresh41 related unit tests pass on main and this isolated backup tree. New
tests were failing-first (helper absent), then passing. No neural training,
GT-guided correction, frame deletion, old weight sweeps, or baseline rollback.

All-ten candidate regression is in progress in the main worktree. First fresh
case remainsPASS but max rises7.900→8.307mm, so improvement is not assumed.
Original global scales and quality snapshots are intentionally frozen to
isolate validation. Post-check observation counts are separate diagnostics;
old robust-scale quality is NOT post-validation quality.

Attribution: observational stage7 fresh2/fresh4 replay byte-identical. Fixed
final-IRLS RHS decomposition shows take4 bad joint correction dominated by
stereo, with full-rate contribution only0.674mm at the peak. Sum reconstructed
position correction error <0.000026mm. This is not factor-removal ablation.

Existing production baseline and its owned-remote backup stay unchanged.
Independent fresh captures are needed after any future accepted optimization.
