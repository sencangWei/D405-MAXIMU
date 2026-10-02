# 2026-10-02: frozen 09-30 fusion failure audit

This is a read-only diagnosis of the existing MASt3R + D405 stereo + IMU +
Docker2 VINS pipeline, not a new SLAM candidate. Tracker/SteamVR poses were used
only after trajectory generation to evaluate error. The failing take4 and take6
and passing take2 were replayed with *logging only*; each replay's
`mast3r_logs/dataset_full.txt` has exactly the same SHA-256 as its production
counterpart. Thus the added logs describe the frozen production trajectories.

## Where the error first appears

All lengths below are measured over 1-second intervals in the same recording.
MASt3R positions are converted with the existing stereo/IMU global scale;
VINS body poses are converted to the left-IR camera centre using the Docker2
`body_T_cam0` calibration. No Tracker pose enters these ratios.

| Take | Frozen fusion ATE max | 33–35 s MASt3R / VINS displacement | Independent stereo / VINS displacement | 33–35 s median 3D match fraction |
| --- | ---: | ---: | ---: | ---: |
| 2, passing | 7.02 mm | 0.985 | 0.979 | 0.549 |
| 4, failing | 23.47 mm | 1.152 | 0.975 | 0.552 |
| 6, failing | 68.72 mm | 1.298 | 0.973 | 0.254 |

Take6's ATE is from `baseline_diagnostic_score`, not a selected/published
`fusion_score`: its candidate was produced and scored for diagnosis only.

For take6, VINS/Tracker 1-second displacement ratio is 1.02 at 33 s and the
per-second median VINS ATE is about 1.8–4.5 mm in the 31–35 s error growth window. The Tracker
trace has no large per-frame jump in that window. Take6 MASt3R's metric-stage
median error grows from 21.6 mm at 30 s to 87.5 mm at 34 s. The following
keyframe graph reduces that to 57.4 mm but does not remove it; final fusion is
59.2 mm there. The online MASt3R pose trajectory *and* its later graph output
show the excess motion, so the final renderer/alignment and final smoothing
did not create the defect.

This is a **local, non-rigid visual-motion length error**, not one fixed global
scale, hand-eye offset, or timestamp shift. Take6's 20–29 s MASt3R/VINS
1-second displacement ratio is 0.991, but it becomes 1.298 at 33–35 s.
Independent D405 stereo remains close to VINS over those windows. Global
stereo-versus-IMU scale consistency (take6: 2.61%) therefore does not certify
*local* trajectory scale.

## Why the pipeline published it

The take6 graph report says MASt3R/VINS position disagreement P95 is 102.33 mm,
yet `result=PASS`: the automatic policy relaxes the absolute visual sigma from
20 to 40 mm when disagreement exceeds 50 mm. The graph requested up to 92.45
mm position correction, but the per-node cap is 25 mm and 233 frames are
clipped. Stage `[8/9]` reports `local_weight=0`, so it cannot repair this
remaining local error. The graph still includes VINS relative-motion edges;
`local_weight=0` does **not** mean VINS was wholly absent.

Logging-only replay found no frame below the 5% 3D tracking gate in these
three complete-coverage recordings. Take6 match fraction drops to 25.4% in
the failure window, but take4 remains at 55.2% while also failing. Thus
"too few matches" is **not a common sufficient cause**; lowering the match
threshold is not justified. An isolated descriptor/stereo pair probe rejects
several pairs in *both* passing and failing recordings as
`inconsistent_stereo_geometry`, so that probe result alone is not a valid
failure detector either.

## Boundary of this conclusion

The first bad stage and the guard's failure to reject it are established.
What makes MASt3R's accepted 3D correspondences yield excessive translation
in take4/6 is **not yet distinguished** between biased pointmap geometry,
correspondence geometry, and pose optimisation. Do not train a model, change
matching thresholds, add global scale modes, or tune graph weights on this
evidence alone. The next experiment must inspect source/keyframe pointmap
geometry and independent two-view stereo at the actual accepted graph edges,
including passing take2, before proposing one isolated intervention. Acceptance
requires the predeclared on-device consistency check to improve *and* frozen
external ATE max to decrease without regressing take2; final <10 mm claims
require independent recordings. External poses remain scoring-only.

Logging artifacts (local, not production outputs):
`causal_trace_take{2,4,6}_20261002/{frontend.csv,match.csv,mast3r_logs/}`.
