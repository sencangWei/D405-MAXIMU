# Nine-center stereo geometry diagnostic — 2026-09-28

## Result and limits

The accuracy target is **not achieved**. The last full-trajectory control remains
9/10 PASS, worst translation ATE **13.801442 mm**. No production trajectory,
selector, frontend model, calibration or native fusion implementation changed
in this diagnostic.

All ten fixed recordings were tested, five time-stratified pairs per recording:
50 pairs, of which 42 passed the unchanged local solver gates. All 42 accepted
pairs expose one correlated nine-center diagnostic of rank 24; no accepted
diagnostic is missing or unavailable. Rank is numerical sensitivity, **not** an
accuracy certificate or calibrated covariance. Eight refused pairs remain in
the evidence and are not turned into estimator factors.

The frozen local bundle states were compared with the unchanged official
camera reference only AFTER all UMI-only computations finished. This compares
eight non-anchor relative centers per accepted pair, mapped through the first
camera orientation. It is **not full-trajectory ATE**, does not fit a local
transform, and must not be used to select windows or estimator weights.

| Local relative geometry, 336 non-anchor centers | Mean mm | Median mm | P95 mm | Max mm |
| --- | ---: | ---: | ---: | ---: |
| Nine-center local stereo bundle | 1.182284 | 0.622824 | 3.544572 | 9.167750 |
| Existing paired-endpoint joint graph, same samples | 1.214525 | 0.547247 | 3.797679 | 12.542609 |

Only **146/336** centers improve; six of ten case means regress. The reduced
worst local error is therefore not evidence of uniformly improved SLAM.

| Recording | Accepted pairs | Local bundle mean / max mm | Current graph mean / max mm |
| --- | ---: | ---: | ---: |
| dev1 | 4 | 0.6404 / 1.6644 | 0.5651 / 1.7581 |
| dev2 | 5 | 1.2935 / 3.7675 | 1.0986 / 3.6822 |
| heldout1 | 3 | 1.2059 / 4.2383 | 1.0882 / 3.1377 |
| heldout2 | 5 | 0.9393 / 3.1501 | 1.5542 / 5.6434 |
| heldout3 | 5 | 1.4229 / 3.9660 | 1.3071 / 3.6361 |
| heldout4 | 4 | 1.1511 / 4.7534 | 0.8410 / 2.4495 |
| fresh1 | 4 | 1.2666 / 4.4618 | 1.3521 / 4.5439 |
| fresh2 | 4 | 1.1707 / 3.8300 | 1.4277 / 8.5057 |
| fresh3 | 3 | 0.3599 / 1.9995 | 0.2968 / 1.2314 |
| fresh4 | 5 | 1.9529 / 9.1678 | 2.0625 / 12.5426 |

For fresh4's last fixed pair (raw frames 1058–1098), the final non-anchor
relative error is 9.167750 versus 12.542609 mm. Improvement is mixed within that
pair: raw frame 1073 is 6.051289 versus 5.281908 mm, i.e. worse locally. Its
raw frame 1098 local maximum is NOT the full ATE maximum at raw frame 1071.
The global 26-frame mostly-Z failure block remains unresolved. No isolated
spike interpolation or GT-driven correction was performed.

## Reproducibility and aggregation bug

The original producer finished all 50 computations but exited 1 in its final
annotation step: normal independent success rows omit `joint_pair`, and the
new adapter had incorrectly required that optional field. The fix accepts its
absence while still rejecting a wrong value when present. No solver reran.

- `shape_pairs_ten_v1/`: untouched raw case and summary artifacts.
- `shape_pairs_ten_v1/producer_snapshot/`: exact pre-fix adapter and shape core,
  backed originally in commit `fd55c29db0efe9e417c4d4a9a31793171c1712c2`.
- `shape_pairs_ten_v1_finalized/`: new finalized clone with explicit raw hashes,
  original producer snapshots and finalizer source hashes. The ten raw case
  JSON files remain byte-identical; summary estimator case fields are unchanged.
- Cached v2 comparison: all 100 original endpoint acceptance/reason entries,
  input and decoded-image hashes match; accepted endpoint maximum component
  delta is exactly **0.0 m**. Adding diagnostic capture did not change the
  estimator's existing accepted outputs.
- `shape_pairs_ten_v1_finalized/geometry_evaluation.json`: evaluation-only
  result, with 141 source/input/reference hash bindings verified after scoring.

Targeted regression tests: **27 passed**. Expanded regression tests and remote
restoration are recorded separately in the planning proof log.

## Next step

Review a structurally valid graph interface retaining the nine correlated
center states. It must preserve frame/gauge and body-to-camera lever semantics,
avoid replacing the grouped factor with independent edges, declare gyro reuse,
and account for a local affine linearization rather than claiming an exact
nonlinear or covariance-calibrated factor. Do not append it alongside duplicate
endpoint factors or search weights/densities based on Lighthouse scores.

A sampled local diagnostic alone does not authorize graph integration or
production promotion. The native solver, frozen source closure and current
full-trajectory evidence remain unchanged pending that design review.

## Grouped row prototype and real-input algebra check

Separate review approved a pure sparse row constructor only. It enforces all
nine exact indices, rejects negative indices/aliased columns/scale overlap,
retains full cross-node sensitivity, and reports LOCAL displacement from the
BA linearization. Nonzero affine, common translation, rigid world transforms,
rank-zero groups and an independent physical derivative oracle are tested.

All 42 accepted groups from all ten cases passed a read-only construction
check against existing camera-frame joint graph poses. The affine row and
physical evaluator maximum component difference was **1.167955e-12**; the
common-translation residual norm maximum was **2.807906e-14**. These are
normalized residual algebra checks, not millimeter accuracy. All 111 hash
bindings were verified after the check. Evidence:
`shape_rows_ten_v1_preflight.json`.

The diagnostic probe applies a seeded synthetic state ONLY to evaluator arrays;
there was no graph solve, GT read or estimator pose write. Expanded main
regression suite: **361 passed in 4.59s**. This does not change the global ATE
result or authorize production admission.

Before any real consumer control, capture the same profile on the existing
uniform full-coverage schedule (29 pairs per case, all ten cases), rather than
assuming these five time-stratified pairs generalize to the remaining time.
The next adapter must not alter the underlying solves or tail handling and
must freeze the full census before external evaluation.
