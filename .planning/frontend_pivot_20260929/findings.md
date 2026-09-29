# Findings — frontend precision pivot

- 2026-09-29 handoff: product chain is 9/10 PASS; fresh4 remains 13.801442 mm maximum ATE. The bad interval is a 26–27-frame gradual displacement block, not isolated frame spikes. Fourteen downstream/parameter families were already falsified.
- Saved long-loop geometry: fresh4 accepted long-link visual-vs-bidirectional-stereo displacement disagreement median 11.15 mm, versus fresh1 3.52 mm and heldout1 4.08 mm. But the stereo forward/reverse spread is 3.81–6.35 mm for several fresh4 links, so these are not precise ground-truth labels. Deleting three discrepant links worsened final max to 15.519 mm.
- Opt-in VINS absolute backend factor/log-scale candidate: fresh4 plain fusion max 8.828 mm; fresh1 and heldout4 max 14.684 and 14.552 mm, both regress from passing baselines. dev2 cannot be scored because the frontend keyframe solver became nonfinite. Candidate rejected.
- A generic independent stereo/PnP residual is not a usable selector: fresh1 passing median 12.36 mm exceeds fresh4 failed 6.43 mm on one broad check. No GT-selected switch is admissible.
- Upstream MASt3R fork relocalization race and backend-exit fail-fast were fixed and separately backed up/tested; these correct execution behavior, not accuracy.
- Baseline backend-edge probe is observation-only and preserves all three 1199-frame frontend CSVs byte-for-byte. Fresh4 has accepted 909/915→1085 loops, but edge removal worsened ATE; later backend corrections generally reduce online-vs-stereo discrepancy. Therefore the bad block cannot be assigned to a single false retrieval edge.
- At failed fresh4 995→1074, final visual/stereo/VINS camera chords are 117.93/127.88/132.87 mm; at nearby 1057→1074, final visual 149.48 mm versus VINS 165.78 mm, while online visual was 162.12 mm. This supports a *local backend shortening* hypothesis, but D405/VINS disagree by several mm and no single edge's deletion fixes it.
- The experimental `stereo_pointmap_scale_prior` path scales MASt3R pointmaps from D405 depth; its backend VINS prior is activated only when `vins_backend_position_sigma_m > 0`, simultaneously enabling a log-scale term. The observed opt-in numeric failure and control regressions belong to this *experimental* path, not the incumbent product.
- The existing `compare_metric_loop_chords.py` emits only vector norms for VINS, so it cannot distinguish the documented 1057→1074 case where length improves while direction worsens. A diagnostic-only extension will express the VINS camera chord in the first camera frame using the recording's calibrated `body_T_camera`, then compare vector residuals against the already frozen visual and stereo vectors on fresh4/fresh1/heldout1. A passing control counterexample rejects a simple global gate.

## Sources
- `reports/codex_handoff_20260922/HANDOFF.md`
- `reports/metric_window_bundle_20260928/causal_long_loop_deletion_v1/README.md`
- `.planning/metric_window_bundle_20260928/progress.md`
