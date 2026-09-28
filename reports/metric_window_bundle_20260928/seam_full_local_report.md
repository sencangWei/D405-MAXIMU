# Full all-ten seam-window local control — not full-trajectory ATE

All ten UMI-only controls completed before reference scoring. Same41raw images,
nine BA poses, factory calibration, formal td and old solver/noise/gates as the
previous frozen seam experiment. Uniform40raw-frame intervals, stride40:
290pairs,580 endpoints. No frame/trajectory changes and no GT in estimation.

Joint253/290pairs accepted =506/580 endpoints;74 refused. Independent540/580
accepted;40 refused. For each method30 accepted endpoints lack existing
reference time coverage: joint476 scored, independent510, mutually scored470.
Refusals and reference-unscorable endpoints remain explicit. The final38/39
recording frames have no additional complete window factor; they are NOT cut
from input, SLAM output or later official trajectory evaluation.

On the same470 scored endpoints, local displacement error (mm):

| Statistic | Independent | Seam joint |
| --- | ---: | ---: |
| Mean | 1.393 | 1.348 |
| Median | 0.889 | 0.837 |
| P95 | 4.281 | 4.079 |
| Maximum | 18.578 | 13.287 |

261/470 improve; this is not a uniform improvement nor a SLAM pass claim.

| Case | Same endpoints | Independent max, mm | Seam joint max, mm |
| --- | ---: | ---: | ---: |
| dev1 | 47 | 5.206 | 4.844 |
| dev2 | 45 | 3.497 | 3.887 |
| heldout1 | 48 | 5.064 | 5.312 |
| heldout2 | 47 | 6.511 | 7.545 |
| heldout3 | 49 | 5.859 | 5.315 |
| heldout4 | 44 | 5.289 | 4.039 |
| fresh1 | 49 | 18.578 | 13.287 |
| fresh2 | 48 | 6.135 | 7.213 |
| fresh3 | 43 | 5.074 | 5.259 |
| fresh4 | 50 | 8.800 | 6.930 |

The largest remaining local error is fresh1 endpoint42. Several other cases
regress locally; no estimator selection/admission/weight is changed from these
scores. Small-sample local maxima cannot be extrapolated to the whole recording.

Two endpoints from a joint solve are correlated. No calibrated covariance is
claimed. Next isolated engineering control: allten baseline/joint/independent
full graphs with unchanged0.004m native fixed penalty/confidence1, after strict
input/schedule/time/source preflight; finish all30graphs before any GT scoring.
This control is non-promoted and does not model joint pair covariance.

Current best PREVIOUS full-trajectory ATE remains9/10PASS, fresh4max14.016mm.
No new full-trajectory result, production deployment, GPU rerun or fresh-capture
generalization is claimed by this report. Evidence: seam_full_ten_v1 summaries,
local_joint_evaluation.json, local_independent_evaluation.json and
full_seam_local_score_summary.json (includes full hash/provenance manifests).
