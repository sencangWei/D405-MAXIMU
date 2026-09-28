# Stereo-inconsistent backend edges: causal A/B (not promoted)

2026-09-28. This is an **offline diagnostic**, not the production algorithm.
It uses the D405-only stereo geometry report from
`../backend_match_probe_v1/fresh4_local_stereo_check.json` to flag four
already-accepted short graph edges in raw frames 1057–1110. No mechanical
arm, Tracker, or Lighthouse data selects an edge. The reference is read only
**after** each candidate has produced and hashed its full fused trajectory.
The frozen current joint/seam stereo measurements and VINS trajectory are
unchanged. Only the frontend edge intervention and its consequent IMU metric
scale and keyframe cache change. The [candidate manifests](fresh4_current_chain/manifest.json)
and [inlier manifests](fresh4_inlier_current_chain/manifest.json) record exact
commands and hashes. This is a controlled sensitivity experiment, **not** a
fresh all-ten product run; frozen seam controls were not recomputed.

| Current-chain fresh4 | Max ATE | P95 ATE | Mean ATE | Rotation RMSE | Verdict |
| --- | ---: | ---: | ---: | ---: | --- |
| Frozen current product | 13.801 mm | 8.715 mm | 5.554 mm | 1.529° | FAIL |
| Drop four stereo-rejected short edges | 13.942 mm | 10.237 mm | 6.223 mm | 2.018° | worse; FAIL |
| Keep edges, mask depth-supported pixels with >4 px PnP reprojection | 13.817 mm | 8.699 mm | 5.558 mm | 1.540° | essentially unchanged; FAIL |

The drop hook removed exactly 1057→1074, 1074→1095, 1085→1095 and
1085→1110. It kept a full 1199-frame trajectory. The same hook on the
passing fresh1 report removed **zero** edges and reproduced its frozen
frontend CSV byte-for-byte. The inlier hook retained the four edges and
masked 11,059 / 4,328+8,415 / 6,149 / 4,079+5,690 valid-depth pixel
matches respectively; it produced a different frontend trajectory, but no
meaningful change in current-chain accuracy. This inlier candidate was not
run on passing controls because it failed to improve the target case.

The drop candidate's onboard IMU scale changed 0.489327→0.470852 metres per
MASt3R unit; inlier filtering yielded 0.489279. The drop variant made the
IMU-vs-fixed-stereo scale disagreement smaller (3.73%→0.12%) while worsening
ATE, further illustrating why internal scale agreement is not an accuracy
oracle. These scales are estimated without the external reference.

Therefore the failed stereo check is a **symptom**, not sufficient evidence
that deleting or masking those graph factors fixes the 27-frame ATE block.
Neither candidate passes 10 mm. Do **not** promote either hook or use the
four frame IDs as a production rule. The next justified target is the
frontend keyframe pose/pointmap state update over 1042→1057→1074, including
its temporal stereo scale observability; first measure it against passing
controls, then test a source-supported structural change on all ten frozen
cases with GT reserved for scoring.

For audit: [frozen score](../seam_graph_full_ten_v1/joint/fresh4/official_score/precision.json),
[drop score](fresh4_current_chain/official_score/precision.json),
[inlier score](fresh4_inlier_current_chain/official_score/precision.json).
An older 2026-09-27 baseline score for this same recording was 16.181 mm;
it is **not** the comparator for this experiment. The attempted full legacy
stereo rerun was stopped after a byte-identical frontend because the current
product uses a different frozen stereo measurement set; its partial files
remain under `fresh4_fusion/` and are not evidence of a complete result.
