# Stereo factor attribution and minimal repair

Goal: improve failed fresh take2/take4 without regressing the six earlier cases or fresh take1/take3. External reference is scoring-only. Preserve frozen baseline and all frames.

## Phases
1. Complete: byte-identical observational replay and fixed-final-IRLS RHS decomposition; bad take4 joint correction stereo dominated. Generic cycle filter not supported across both cases.
2. Complete experimental branch: discovered SIFT reverse-validation bypass, implemented opt-in inverse-vector helper with failing-first tests. Existing production behavior unchanged.
3. Complete, REJECTED for production: fixed SIFT reverse validation + identical cached stages7–9 on all ten.8PASS/2FAIL; fresh2 max16.151→14.382mm stillFAIL and mean worsened; fresh4 max16.181→16.643mm worsened. Production baseline preserved.
4. Complete for initial helper/harness: independent review,41testsPASS, owned-remote backup e610973 verified from fresh fetch. Later diagnostic/summary additions need another verified backup after results.
5. Complete: all-ten raw calibrated gyro vs free-PnP vs raw-MASt3R-fixed measurement probe,166edges. Internal reverse closure improves, reprojection worsens allten, paired VINS disagreement worsens8/10. Not sufficient to promote another candidate. No trajectory changed.
6. In progress: bounded read-only IR rectification/distortion contract audit; rule out a concrete geometry implementation mismatch before a more complex observation-model change. Back up final diagnostic sources/evidence and verify restored artifact. Goal NOT achieved; no claim of stable all-frame10mm.

## Constraints
No closed-family sweeps, GT-driven corrections, parameter selection from GT, deleted frames, model retraining, or production replacement before validation.

## Errors
Cycle summary initially failed numpyint64 serialization; explicit int cast fixed.
PnP probe report intrinsics use left_intrinsics/right_intrinsics; explicit APIadapter fixed KeyError.
Backup tree lacked one existing testmodule; included that component and reran41 testsPASS.
Candidate input loading temporarily I/O-bound, systemPSIio some94%; no kernel diskerror seen in recentlog. No unrelated process stopped.
