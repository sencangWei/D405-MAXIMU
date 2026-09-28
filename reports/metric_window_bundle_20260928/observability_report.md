# Frozen all-ten endpoint observability diagnosis

This round is diagnostic only. No estimator equations, graph weights, time
offsets, frame selection, production pipeline, or trajectories were changed.
The previously frozen full-window graph remains 9/10 PASS; fresh4 maximum ATE
is **14.016 mm**, with 30/1142 scored samples above 10 mm. The goal is not met.

## Evidence and reproducibility boundary

- `observability_full_ten_v1/summary.json`: all 590 scheduled windows completed;
  567 accepted, 23 refused, all ten recordings retained. No GT read in estimation.
- `observability_full_ten_v1/diagnostic_summary_v2.json`: source/input/decode
  provenance, schedule, admission decisions and initial endpoints checked against
  the prior full-window controls. No accepted diagnostic is missing.
- `paired_system_numpy2_v1/summary.json` and `paired_venv_numpy1_v1/summary.json`:
  first uniform window of every recording, original and instrumented solver
  centers/landmarks/bias bit-identical WITHIN each runtime.
- Old cached controls used system NumPy 2.2.6; this census used toolchain NumPy
  1.26.4. Across runtimes, strict 1e-10 m endpoint identity FAILS and remains
  failed. Maximum endpoint component difference is 2.113874232e-7 m, or
  0.000211387 mm (maximum Euclidean difference 0.000277886 mm). Explicit optional
  numerical-reproducibility proof mode is bounded at 1e-6 m, or 0.001 mm; it
  reports `endpoint_exact_identity=false`. This does not relax the ATE gate.
- `current_local_evaluation.json` scores the CURRENT frozen census using the
  existing official body reference, fixed body-to-leftIR lever and time policy.
  537 windows scored; 30 accepted early windows lack reference coverage and
  remain explicitly unscored. No constraints are selected by external errors.
- `reference_association_v2.json` binds that score to the current census hash;
  results are descriptive local-displacement associations, NOT trajectory ATE,
  independent statistical samples, calibrated covariance, or proof of causation.

The earlier `diagnostic_summary.json` and `reference_association.json` are
superseded audit artifacts: they predate the stricter proof-input binding fix.
Their helper source hashes no longer match current code. Use the `_v2` files.
No raw census or paired-proof artifact was overwritten.

## What was found

Every one of the 567 accepted endpoints has conditional rank 3. Therefore
“strictly unobservable / rank-deficient endpoint” is NOT established as a cause.
Full rank does not mean well-conditioned or millimetrically accurate. Marginal
endpoint sensitivity projects away other pose, landmark and bias freedoms at
the optimized robust Jacobian; its noise assumptions are provisional.

All-factor weak response and local displacement error have pooled Spearman
rho **0.8029**, and positive association within every recording:

| Recording | Accepted | Scored | Sensitivity/error rho | Local error P95 mm |
| --- | ---: | ---: | ---: | ---: |
| dev1 | 57 | 54 | 0.790 | 3.111 |
| dev2 | 55 | 52 | 0.843 | 3.394 |
| heldout1 | 57 | 54 | 0.790 | 4.678 |
| heldout2 | 59 | 56 | 0.830 | 3.908 |
| heldout3 | 58 | 55 | 0.774 | 3.996 |
| heldout4 | 56 | 53 | 0.790 | 3.263 |
| fresh1 | 57 | 54 | 0.896 | 5.168 |
| fresh2 | 56 | 53 | 0.840 | 5.423 |
| fresh3 | 54 | 51 | 0.778 | 3.996 |
| fresh4 | 58 | 55 | 0.777 | 5.361 |

Pooled pixel-only sensitivity/error rho is 0.8093; endpoint-track count/error
rho is -0.5352 (fresh4 -0.7244). These are associations, not a fitted error
predictor or a ground-truth-derived selection rule.

The high-error windows illustrate loss of sustained metric support:

| Window | Tracks across five nodes | Pixel P95 px | Local displacement error mm | Error projection onto all-factor weak axis mm |
| --- | --- | ---: | ---: | ---: |
| fresh1 #42 | 55,54,27,16,10 | 1.722 | 18.578 | 17.955 |
| fresh4 #54 | 97,97,64,32,14 | 1.199 | 9.224 | 6.718 |

fresh4 #54 spans source indices 1060–1080, capture elapsed 35.327–35.994 s,
including the previously inspected peak region. A good fit to the surviving
pixels can coexist with weak depth/pose-motion separation and a biased metric
endpoint. This supports investigating persistent landmark support, not merely
optimizing pixel loss harder.

There are important counterexamples: fresh4 #53 has 59 endpoint tracks but
7.845 mm local error, only 0.938 mm along its weakest axis. fresh2 #58 has
5.532 mm error despite a modest 0.334 mm/unit sensitivity. The current
diagnostic cannot distinguish biased source depth, temporal matching bias,
unmodelled image effects, or reference uncertainty, and cannot explain all
graph-consensus errors. Do not claim a unique hardware/model-training cause.

## Repair direction and acceptance contract

The evidence supports a bounded next architecture experiment: preserve/reseed
landmarks across adjacent windows and jointly constrain their stereo pixels,
rather than compressing each independent window into one endpoint displacement.
This adds actual geometric information and tests depth/pose consistency; it is
not another closed-family weight sweep or interpolation onto GT.

Before a graph replay, require synthetic geometry/frame/gauge regression tests
and a uniform all-ten raw-observation comparison showing stronger sustained
endpoint support and improved independent pixel consistency without hidden
frame removal. Freeze one candidate and all UMI-only decisions, then score all
ten with the unchanged official contract. Accept only if all maximum ATEs are
below 10 mm with no lost prior passes; new recordings are still needed for
independent generalization. Do not deploy this diagnostic as a confidence gate.

Verification: fresh expanded suite 248 PASS; strict default rejection and
proof-backed numerical replay checked with real artifacts. Same-day owned
remote backup/restoration is recorded in the planning backup verification log.
