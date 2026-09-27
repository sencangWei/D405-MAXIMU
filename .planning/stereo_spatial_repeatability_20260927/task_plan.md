# Stereo spatial repeatability, common ten-case diagnostic

Target: identify a physically supported stereo observation repair, then frozen
ten-case regression. Goal remains max translation error<10mm without GT input.
Baseline and rejected SIFT candidate remain separate and immutable.

1. Complete: empirical spatial sensitivity on all166uniformly time-stratified measurements across ten cases. Existing replay fields identical, source report hashes unchanged. Reject universal sensitivity-based repair: failedfresh2max1.270mm versus passingfresh1max6.166mm.
2. Complete: held-out target-frame stereo-depth consistency on same166edges. Existing control fields exactly equal raw_gyro_pnp_v1, inputhashes unchanged. Failurefresh2 3.174mm and fresh4 4.981mm median depth discrepancy versus passingfresh1 5.293mm. No common failure discriminator; reject GT-free filtering/weighting based solely on these discrepancies.
3. Conditional: only implement a minimal observation repair if the diagnostic supports it across cases. Otherwise reject the hypothesis and report the evidence, not another scalar weight/smoothing sweep.
4. Conditional: failing-first tests, independent review, identical ten-case graph→fusion→score regression; promote only if failed cases improve and previous passing cases do not regress. No GT selection, frame deletion, neural retraining, or closed-family repetition.
5. Back up new code/evidence to owned sencang and verify clean remote restore.

Stop condition for this diagnostic branch: a measured instability explanation,
or evidence against it. Overall10mm target is NOT achieved by a diagnostic.
