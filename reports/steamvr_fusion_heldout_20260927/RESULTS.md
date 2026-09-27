# Fresh four-take heldout evaluation, 2026-09-27

Status: all four runs finished16:20:11;3/4precisionPASS,1/4precisionFAIL.
Capture PASS is not SLAM precision PASS. Broad reliable10mm not achieved.

## Cohort and immutable recipe

All four recordings passed raw camera/IMU/official Tracker checks before SLAM.
User-requested three additional recordings follow the first moving take.
The operator-reported stationary capture150314 is not a dynamic test and is
retained with an explicit exclusion note. No precision-based exclusions.

| Batch | Capture ID | D405 recording ID | Raw acceptance |
| --- | --- | --- | --- |
| 1 | 150519 | 150523 | PASS |
| 2 | 150812 | 150816 | PASS |
| 3 | 150958 | 151002 | PASS |
| 4 | 151138 | 151141 | PASS |

Exact commands: [RUN_BATCH.sh](RUN_BATCH.sh). Existing internal VINS input,
then fusion-guarded, then official SteamVR scoring; no per-take changes.
All stage exit codes retained in [events.log](run_frozen_four/events.log).
Frozen source/config hashes: [PROTOCOL.md](PROTOCOL.md).
Build executables are explicitly pinned to the same versions as development
validation, not default install executables. Replayrate0.5, skip1.5s,
IMUshift0, formaltd-0.009109323s, estimate_td0. No new training/checkpoint.

Tracker is post-SLAM scoring only. Body/IMU origin, fixed frozen reference,
camera-domain query offset-12.786861933ms, SE(3) without scale fitting.
Every valid initialized output is scored; initialization coverage separately
reported, no high-error samples removed. PASS requires translationRMSE/P95/max
<=10mm, rotationRMSE<=2deg, overlap>=98% (see exact scorer thresholds).

## Actual results (mm)

| Batch | Mean | Median | RMSE | P95 | Max | Within10mm | Result |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| [1](run_frozen_four/take1/official_score/precision.md) | 3.730457 | 3.528519 | 4.135168 | 7.067570 | 9.956313 | 100% | PASS |
| [2](run_frozen_four/take2/official_score/precision.md) | 7.519798 | 7.334709 | 8.458478 | 13.038880 | 13.265557 | 67.629% | FAIL precision |
| [3](run_frozen_four/take3/official_score/precision.md) | 5.547009 | 5.662648 | 5.853062 | 8.721355 | 9.259687 | 100% | PASS |
| [4](run_frozen_four/take4/official_score/precision.md) | 6.715049 | 7.378482 | 7.067117 | 9.343433 | 9.960184 | 100% | PASS |

Take1 selects baseline;1143 valid initialized samples from1199camera sets,
rotationRMSE1.288240deg, timestamp overlap100%. Internal input coverage98.960%
is against1154expected poses after the configured startup skip, not all1199
rawcamera frames. Maxmargin0.043687mm; no robust universal10mm claim.
Scoring manifest confirms estimateunchanged,SHA2511baf10e234d40aed3aa0e1393779d37a3eeb0203678eacd5f7d0faffa60b8.
Official Tracker reference has calibration uncertainty; this is relative
trajectory evaluation, not a laser-tracker absolute metrology certification.

Take2 also selects baseline;1143samples,rotationRMSE0.891847deg,overlap100%.
Input/fusion successful; scoringrc3 is completed precisionFAIL, not a runtime
crash. P95,max,and within10mm ratio gates fail. No precision-driven rerun.

Take3 selects baseline;1143samples,rotationRMSE1.458174deg,overlap100%.
All input/fusion/scoring stages complete normally. Take4 started16:04:39.

Take4 selects baseline;1143samples,rotationRMSE1.189431deg,overlap100%.
Maxmargin0.039816mm. All four select baseline autonomously from onboard
quality metrics; none required rescue, per-take tuning, GTfitting, or rerun.
All four estimates unchanged by scoring, all four rawcaptures/input/fusionPASS.
There are56fewer output samples than1199rawcamera sets per take; configured
startupskip and estimator initialization are reported, not counted as scored
zero-error frames. Rawcamera-count output coverage95.329%; reference overlap
is100% of valid initialized outputs. InternalVINS coverage98.873%–99.047%
uses the post-skip expected denominator, not the fullrawcamera denominator.

Batch elapsed61m15s, existing orchestration serial. Serial execution is not an
algorithm requirement. Potential stage pipelining (CPUstereo while nextcaseGPU
frontend) is not implemented/validated here; ROSdomain/output/resource isolation
required. Runtime observation duringCPUstereo:GPU0%/622MiB; duringfrontend:
9559MiB/3% snapshot. These snapshots are not a sustained performance profile.

## Conclusion

Fresh heldout evidence rejects a claim of uniformly stable maximum<=10mm:
take2max13.266mm andP9513.039mm,32.371% of its samples exceed10mm.
Two of the three passing takes have only~0.04mm maximummargin. Keep the
failed case as useful diagnostic data; raw acceptance alone does not establish
whether its residual comes entirely from SLAM or includes reference uncertainty.
No causal root-cause claim or algorithm optimization was made from this batch.
