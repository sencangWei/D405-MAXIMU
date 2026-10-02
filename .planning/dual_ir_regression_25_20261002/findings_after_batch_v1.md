# Evidence-driven continuation (2026-10-02)

User authorization: automatically continue optimization after all 25 finish;
do not wait for another permission handoff. Existing v1 dependencies stay frozen
until its systemd job finishes. No sensor recording or service manipulation is
needed for the following offline work.

## Verified reference contamination: Sep29 take03

- All eight candidates peak near 222–224 mm; stereo-only also fails.
- Historical VINS-only score already peaked at 216.86 mm.
- Peak reference index 715, camera time 1790682778.9639378.
- Body reference consecutive steps at 715/716/717: 232.22/173.48/106.09 mm.
- Raw Tracker at host_monotonic ~94706.7575–94706.8157 already has steps
  210.50/97.32/109.45/102.07 mm, BEFORE the body extrinsic or clock mapping.
- Camera intervals remain ~33.338 ms; estimate steps remain ~3–6 mm.
- Source Tracker hash and frozen reference provenance match historical scoring.

Conclusion: the huge common peak is contaminated external reference, not a
learned-motion error to optimize against. Preserve the recording, original
precision report and 25-source denominator. Do NOT substitute estimate-derived
GT, trim the peak to make it pass, or report all-25 success. Separate the remaining
ordinary centimetric residual from this invalid-reference spike.

## Isolated cache/adapter failures (not raw-video rejection)

1. Sep27 ind2: right long-hop scale 0.413151 vs primary 0.389777 differs 5.82%;
   each report is PASS, but merge rejects the whole eye at the 5% agreement check.
   Left four scales differ only ~1.16%. Candidate fix: keep primary, reject only
   inconsistent optional reports, retain explicit rejected-report diagnostics.
2. Sep29 take02: right dataset 1199 frames, tracked/dense manifest 588. Left
   metric cache also partial (~587 rows). All four left stereo reports PASS.
   Partial raw tracking is not an input-session mismatch. Candidate fix: permit
   raw tracked coverage <= dataset count with explicit coverage; no interpolated
   artificial observations. Evaluate the full common VINS camera timeline.
3. Sep29 take04: right primary/long/dense PASS, 328/84/399 accepted observations;
   multisecond report FAIL right_stereo_scale_unobservable, relative_p90_p10=.681.
   The derive rc=2 aborts the whole eye. Candidate fix: retain failed optional
   artifact but don't consume its factors; keep the three valid reports.
4. Sep29 take05: all 1190 candidate pairs rejected as translation_excitation_low,
   zero metric-scale observations. Missing downstream reports are consequences.
   Don't fabricate scale. Explicitly label scale unobservable; consider a
   validated VINS-only degraded output, never claim a full learned-fusion solve.

## Accuracy observations—not a promoted parameter choice

- 10 mm correction cap decreases passing-record count, not a safe default.
- Per-edge 10/15/25 mm learned consistency gates have not improved total passes.
- Large learned self-residual alone does NOT mean SLAM failure: Sep27 heldout2
  own-residual P95 ~71.98 mm still yields both max 6.28 mm.
- Conversely heldout4 own-residual P95 ~8.54 mm gives both max 10.46 mm.
  Do not extrapolate a single absolute residual threshold from take6.

## Next safe branch

After v1 finishes: targeted regression tests first, opt-in optional-report
degradation and raw-coverage correction, reuse already computed right frontends,
rerun every affected record and several prior passes, then run the full corpus.
Keep source failure labels, same timing/extrinsics, and no GT graph inputs.
Only then pursue accuracy changes with multi-record internal evidence.

Goal workflow: `.omx/goals/performance/dual-ir-25-10mm-20261002/`.
No goal completion or all-25/generalization claim is currently justified.

## Frozen baseline closed at 18:48 local

Systemd exited with code 3 as expected for retained preparation failures.
All 25 sources processed, 19 both-policy scores, 15 precision PASS, four scored
failures, six unscored. The 13 frozen dependencies had zero hash changes at close.
`baseline_acceptance_v1.json` preserves the fresh audit performed BEFORE edits.

Next experiment repairs adapters only (not graph equations or cap settings).
`config/dual_ir_regression_25_20261002_adapters.json` retains all 25 identities
and frozen scoring references and links the unabridged baseline by content hash.
It reuses 21 complete right caches and three raw frontends chosen only by file
completeness; one primary-scale-unobservable item remains explicit failure.
No source recordings or historical reports are overwritten.

Independent case comparison separated three precision failures:
- Sep30 take06: both 15.352 mm vs stereo-only 8.398 mm; internally weighted
  learned-vs-stereo residual remains high when both eyes are simultaneously bad.
- Sep29 take07: both 13.038 mm vs stereo-only 13.105 mm; not learned override.
- Sep27 heldout4: both 10.463 mm vs stereo-only 11.273 mm; learned helps here.
A future temporal learned-reliability hypothesis must be defined on the same
onboard windows for ALL recordings; localization using GT is diagnosis only,
not a deployable gating rule. It cannot be assumed to fix stereo-only failures.
