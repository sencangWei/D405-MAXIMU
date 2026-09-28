# Frozen ten-case graph-stage correction and stereo consistency census

2026-09-28. This is a read-only diagnostic of ten previously frozen runs. The
metric input, graph output, four stereo reports, and full-seam-pair manifest are
read with source/input SHA-256 hashes in
[graph_correction_stereo_census_v1.json](graph_correction_stereo_census_v1.json).
All ten input/output trajectories have exactly matching frame timestamps;
all ten stereo observation totals equal their graph report's accepted edge
count. No estimator, graph factors, model, td, parameters, trajectory, or
external evaluation was changed. The official product remains **9/10 PASS;
fresh4 maximum ATE 13.801442 mm**.

The first measure is the camera-frame position difference from the frozen
`trajectory_imu_metric.csv` input to `trajectory_graph.csv` output. It includes
the graph's full-rate refinement and therefore must not be called one
particular factor's correction. The second measure is camera-frame consistency
with the *same accepted stereo measurements* at the two saved stages. It is not
external ATE or an exact internal solver residual: the input orientation
precedes the solver's rotation fusion; stereo reports overlap and full-seam
pairs are correlated. All listed windows use raw input indices 1053–1078;
different recordings contain different physical motions at these indices.

| Case | Official max ATE (mm) | Stage correction max in index band (mm) | Dense stereo median before→after (mm) |
| --- | ---: | ---: | ---: |
| dev1 | 6.44 | 9.59 | 1.94→0.52 |
| dev2 | 8.25 | 26.28 | 2.28→0.68 |
| fresh1 | 8.58 | 16.34 | 2.75→0.41 |
| fresh2 | 8.27 | 7.75 | 2.43→0.83 |
| fresh3 | 6.29 | 40.70 | 10.40→3.08 |
| **fresh4** | **13.80 FAIL** | **17.54** | **3.62→1.06** |
| heldout1 | 6.03 | 5.76 | 1.34→0.41 |
| heldout2 | 5.99 | 7.55 | 2.57→0.63 |
| heldout3 | 6.69 | 11.71 | 0.90→0.36 |
| heldout4 | 8.91 | 7.10 | 1.99→0.68 |

The failed case does **not** have the largest stage correction; passing fresh3
is over twice as large in this index band. Nor is fresh4 the worst dense stereo
residual before or after refinement. The graph fits accepted stereo
measurements while the external absolute position is wrong for 26 frames;
good fit to these *internal* observations does not prove they are unbiased.
This rules out correction magnitude or dense-stereo residual magnitude as a
standalone UMI-only failure detector on these ten recordings. It does **not**
prove stereo correct or identify a faulty factor.

Next discriminating boundary: recover the signed relative-motion, IMU
preintegration, and visual-position factor residuals and the actual per-node
solver state, on the same fixed ten runs, without altering the solution. A
candidate repair is justified only if a UMI-only cross-sensor inconsistency
distinguishes fresh4 from passing controls, then survives an unchanged
all-ten official gate. No Lighthouse-driven weighting or per-case correction.
