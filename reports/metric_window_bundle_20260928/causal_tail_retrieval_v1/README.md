# Fresh4 tail retrieval-edge causal test — negative

2026-09-29. This is an isolated **UMI-only diagnostic**, not a production
change or a new accuracy claim. The frozen current product remains **9/10
PASS**, with fresh4 failing the official 10 mm maximum-position gate.

The saved accepted MASt3R matches were checked against independent calibrated
D405 left/right depth at raw frames 1121–1199. The new stereo reports are
`../backend_match_probe_v1/{fresh4,fresh1,heldout1}_tail_stereo_check.json`.
Fresh4 passes 3/5 checked edges; passing fresh1 passes 8/8, and passing
heldout1 passes 2/4. A failed stereo edge is therefore not unique to a failed
SLAM recording.

At backend event 1135, the long 792→1135 retrieval has 67.3%/83.5% PnP
inliers and 0.583 mm/0.072° bidirectional cycle error, so it is **not** a
supported false-loop candidate. The nonconsecutive retrieval edge 1095→1135
has only 24.0%/44.6% PnP inliers and fails both directions. The original
stereo report is SHA-256 `a3097072deff226f2de43433823a42b07cd2788df918cd2746f4e2cef736c4ce`;
`../backend_match_probe_v1/fresh4_tail_retrieval_hypothesis.json` selects
exactly that one pair before external scoring.

The same frozen frontend config/model and 1199 input frames were replayed with
the existing isolated edge-drop hook. Its log confirms exactly one
`STEREO_EDGE_DROP 1095 1135`; both candidate final/online trajectories have
1199 rows. Candidate IMU scale was re-estimated from its frontend and onboard
IMU; the frozen seam stereo measurements and VINS input were unchanged.
`fresh4_current_chain/manifest.json` records every downstream command and
hashes the candidate fused trajectory **before** the unchanged SteamVR score.

| Fresh4, official same-reference 1142 samples | Max ATE | P95 | Mean | Rot RMSE |
| --- | ---: | ---: | ---: | ---: |
| Frozen current product | 13.801442 mm | 8.714560 mm | 5.554484 mm | 1.529006° |
| Drop only 1095→1135 retrieval | **13.811042 mm** | 8.707831 mm | 5.572793 mm | 1.579192° |

Both fail `ate_translation_max_over_limit`; the intervention did not remove
the broad low-frequency offset. A stereo-inconsistent retrieval is a quality
symptom, but deleting it is not a demonstrated accuracy repair. Do **not**
promote this edge rule, the prior four-short-edge deletion, or outlier masking.
The 792→1135 long loop passed the onboard stereo check and was retained.

The candidate is not a no-op: row-wise positions change by up to **1.140 mm**
in the 1199-row metric graph and **1.173 mm** in the 1142-row fused estimate
(no new alignment; timestamps match). This bounds what this isolated deletion
actually changed in the saved product and is well short of the **3.811 mm**
maximum-error reduction required to cross the 10 mm gate. The bound applies
to this intervention, not to all possible frontend repairs.

This result does not prove the remaining error is in the model, the raw video,
or the backend. The event's final keyframe correction can alter earlier poses,
but neither an event's correction magnitude nor an edge's failed PnP gate is
an accuracy oracle. The next investigation must find a *pre-offset* visual or
metric-state inconsistency with an independent UMI measurement, then test any
structural repair across all ten frozen recordings. No external pose may be
used to select or tune estimator factors.
