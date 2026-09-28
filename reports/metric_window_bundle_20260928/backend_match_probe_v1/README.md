# Accepted MASt3R edge geometry, observation only

2026-09-28. The spawned frontend was observed at its native `FactorGraph.add_factors`
boundary. The hook saved deterministic, at-most-5000-pixel subsets of **already
accepted** Q-gated correspondences. A separate pass used only the same D405
left/right infrared images and their frozen 18.083254 mm factory baseline to
test bidirectional metric PnP (at least 100 inliers and 50% inlier ratio,
2 px RANSAC, at most 4 px inlier P95; cycle at most 10 mm and 2 degrees).
Lighthouse/robot poses were **not** consulted to accept or reject any edge.
This diagnostic did not edit the estimator or its inputs. All five replay
instances (fresh4 long, fresh1 long, heldout1 long+local, fresh4 local,
fresh1 local) produced 1199 final and online poses **byte-identical** to
their respective frozen trajectories, with zero geometry-capture errors.

| Frozen case | Official fused max ATE | Long edges checked / stereo pass | Short edges checked / stereo pass |
| --- | ---: | ---: | ---: |
| fresh4 **FAIL** | **13.801 mm** | 17 / 14 | 77 / 73 |
| fresh1 PASS | 8.58 mm | 12 / 8 | 142 / 142 |
| heldout1 PASS | 6.03 mm | 12 / 11 | none in raw-frame 1000–1120 interval |

The failed fresh4 26-frame ATE interval is raw frames 1053–1078. Its
older/longer accepted links at 1001/1006/1009→1057 and 995→1074 **pass**
independent stereo. Thus the tempting “false loop around frame 1057”
explanation is unsupported. Passing fresh1 has **more** rejected long links
than failed fresh4, so a generic loop gate is not an evidenced fix.

There is a sharper local asymmetry. Fresh4's 1042→1057 consecutive-keyframe
edge passes, but its 1057→1074 edge fails reverse PnP: 779 / 1933
valid-depth candidates = 40.3% inliers, below the 50% native gate criterion.
Three more short edges ending at 1095/1110 fail. All 70 short
edges ending before raw frame 1057 in fresh4's inspected interval pass; all 142 inspected short edges
of passing fresh1 pass. The 1057→1074 edge spans 17 raw frames; its
frontend anchor scale is 1.466 relative to independent stereo, while the
preceding 1042→1057 anchor span reaches 2.659. This supports **weakened
local metric correspondence / front-end scale observability** as a specific
candidate around the peak. It does not prove the one edge causes the final
13.8 mm ATE, because the global graph can adjust earlier poses and the
short-baseline stereo depth can itself be noisy or incomplete.

Crucially, the native `metric_loop_gate` in `global_opt.py` skips consecutive
keyframe edges even when enabled, and the frozen config has it disabled and
does not populate per-frame `metric_depth`. Turning that flag on alone would
not check this edge and would reject nonconsecutive edges for missing depth.
No production knob was changed. The next **causal, UMI-only** experiment is
to take these failing matches, preserve graph connectivity, and test a
general stereo-inlier-filtered edge formulation on fresh4 plus passing
controls, then on all ten frozen cases. Never select edges from Lighthouse
ATE or hand-edit the 1057→1074 trajectory.

Limitations: this offline diagnostic uniformly samples from visual Q-gated
matches *before* discarding pixels with invalid stereo depth. This differs
slightly from native `metric_loop_gate`'s depth-first candidate selection;
reported statuses are therefore diagnostic, not a drop-in production gate.
Stereo matching and MASt3R use the same infrared frames, so this is
cross-estimator geometry, not a completely independent physical sensor.
`sample_sha256` in each JSON row and `sha256` in each match manifest bind
the exact captured pixels. The checked edges cover long gaps with target raw
frame 900–1120 and short gaps with target 1000–1120, not the whole run.

Evidence: `fresh4_stereo_check.json`, `fresh1_stereo_check.json`,
`heldout1_stereo_check.json`, `fresh4_local_stereo_check.json`,
`fresh1_local_stereo_check.json`, and the corresponding `*_matches` folders.
