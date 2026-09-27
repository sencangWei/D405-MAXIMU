# Stereo spatial repeatability, common ten-case diagnostic

Target: identify a physically supported stereo observation repair, then frozen
ten-case regression. Goal remains max translation error<10mm without GT input.
Baseline and rejected SIFT candidate remain separate and immutable.

1. Complete: empirical spatial sensitivity on all166uniformly time-stratified measurements across ten cases. Existing replay fields identical, source report hashes unchanged. Reject universal sensitivity-based repair: failedfresh2max1.270mm versus passingfresh1max6.166mm.
2. Complete: held-out target-frame stereo-depth consistency on same166edges. Existing control fields exactly equal raw_gyro_pnp_v1, inputhashes unchanged. Failurefresh2 3.174mm and fresh4 4.981mm median depth discrepancy versus passingfresh1 5.293mm. No common failure discriminator; reject GT-free filtering/weighting based solely on these discrepancies.
3. Complete diagnostic: paired world-gauge survey on all166edges. fresh4 constantpositionfit-vs-attitudefit2.764deg, localprojectiondifference max14.697mm with positionfit versus2.291mm with attitudefit. Passingfresh1 also3.544degconstantoffset; not a sufficient cause. Coordinatecontract review confirms frame/origin conventions.
4. Complete, REJECT candidate: only VINS body-position constant worldalignment changed. Allten8PASS2FAIL unchanged. fresh2max16.151→16.153mm; fresh4max16.181→16.256mm. Original samplecounts, selectedsigma0.020 and inputhashes unchanged. Direction-basis differences are not sufficient evidence of a causal precision repair. Preserve production.
5. Complete: correct diagnostic depth-contract mismatch. Original probe used
   max_depth1.5m, production uses0.6m. Prior spatial/target/rawgyro results are NOT
   production-equivalent; withdraw their production-level negative conclusions.
   Reproduced166/166cachedmotions using explicit recorded0.6m replay parameters.
6. In progress: uniform free-PnP LM and rawgyrorotation-validation diagnostics;
   only if supported run alltenactualgraphcandidate, withnoGToptimizer/selection.
7. Back up new code/evidence to owned sencang and verify clean remote restore.

Stop condition for this diagnostic branch: a measured instability explanation,
or evidence against it. Overall10mm target is NOT achieved by a diagnostic.
