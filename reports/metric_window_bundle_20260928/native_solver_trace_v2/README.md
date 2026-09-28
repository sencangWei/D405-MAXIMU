# Native factor-state trace: ten frozen joint runs

2026-09-28. A Python profile hook captures the actual return-frame locals of
`refine_positions_visual_inertial`; it does **not** replace factors, weights,
solver, observations, output poses, configuration, or source code. The frozen
full-seam wrapper command was replayed in each case with only its output and
report destination changed. Every one of the ten `trajectory_graph.csv` files
is **byte-identical** to its frozen original. Each trace saves node correction,
the signed final-solution IMU-position/velocity, relative-odometry and stereo
residuals, and their weights. The recorded IRLS weights are the values updated
*after* the fourth solve; they are not weights of an additional re-solve.
No Lighthouse/SteamVR poses were read by this replay or factor census.

The [factor census](../native_solver_factor_census_v1.json) applies the same
raw-index before/middle/after windows to all ten cases. The middle window is
1053–1078, identified previously by **post-freeze** official external
evaluation on fresh4; equivalent indices across recordings are different
physical motions. Values below are median residual norms in that window.

| Case | IMU position (mm) | VINS relative (mm) | Stereo (mm) | Official max ATE (mm) |
| --- | ---: | ---: | ---: | ---: |
| dev1 | 0.44 | 1.34 | 0.77 | 6.44 PASS |
| dev2 | 0.28 | 0.52 | 1.03 | 8.25 PASS |
| fresh1 | 0.27 | 0.56 | 0.62 | 8.58 PASS |
| fresh2 | 0.50 | 0.90 | 1.26 | 8.27 PASS |
| fresh3 | 1.02 | 1.66 | 2.66 | 6.29 PASS |
| **fresh4** | **0.66** | **2.47** | **1.54** | **13.80 FAIL** |
| heldout1 | 0.12 | 0.40 | 0.64 | 6.03 PASS |
| heldout2 | 0.37 | 0.69 | 0.73 | 5.99 PASS |
| heldout3 | 0.19 | 1.49 | 0.61 | 6.69 PASS |
| heldout4 | 0.32 | 1.11 | 0.78 | 8.91 PASS |

Fresh4's VINS-relative residual grows from 0.50 mm before the middle window
to 2.47 mm there (~5×) and remains 1.48 mm afterward. This is a real
within-recording conflict, but not proof that VINS is correct or that the graph
is wrong: passing fresh3 has larger IMU and stereo residuals at equivalent
indices. Moreover, the [fixed rolling-window scan](../native_relative_residual_scan_v1.json)
shows the 5× rise is **not unique**. Fresh4's maximum scheduled ratio is 5.00;
passing dev1, dev2, fresh1, fresh2, fresh3, heldout1, heldout3 and heldout4 all
have larger ratios somewhere in their own recordings (6.69–21.24). Using this
ratio as a universal failure switch would create false positives. The scan
uses every 5th raw frame start from 100 through 1120, prior 53/current 26
frames with minimum factor coverage; the schedule was fixed before comparing
outcomes. Ratios also grow when the baseline residual is small.

Combined with the [saved-stage census](../graph_correction_stereo_census_v1.md),
the failed case is neither largest in graph-stage correction nor stereo
residual, and residual amplitude/relative-ratio gates are not defensible from
these ten runs. All of this is *internal consistency*, not absolute accuracy;
the sensors may share a slowly varying bias. A per-case GT-aligned correction,
frame interpolation or hindsight threshold would violate the intended
Lighthouse-independent SLAM pipeline.

The remaining bounded path is upstream: capture which MASt3R visual
correspondences and loop/re-anchoring constraints actually moved the local
trajectory before metric fusion, with non-GT geometry/temporal checks and
passing controls. Prior pointmap-update magnitude and independent temporal-LK
checks already gave counterexamples, so do not repeat those or the 14 closed
weight/threshold families. No algorithm repair or new <10 mm claim is made.
