# Progress — frontend precision pivot

## 2026-09-29
- Read workspace instructions, mandatory 2026-09-22 handoff, current plan and prior negative-result report.
- Pivot declared: stop rejected VINS absolute-backend factor; retain 9/10 incumbent. Now auditing source-level metric observation path and saved three-case contrast before any edit or rerun.
- No production algorithm or precision report has been changed in this continuation.
- Added a direction-preserving VINS camera-chord diagnostic to the existing read-only report script, with formal body-camera lever/rotation and stereo no-supervision guard. Tests were red first, then 3/3 PASS.
- Recomputed frozen fresh4/fresh1/heldout1 links. Accepted links with VINS coverage: 27/33/44. Median visual–stereo differences: 5.979/4.337/2.340 mm. A passing fresh1 link is an explicit counterexample to an onboard-consensus repair gate; exploratory gate fires 7 times in failed fresh4 but 4 times in passing fresh1, and the failed-case hits precede the actual final bad block.
- Independent saved-point image-row residual check at raw1053..1078 also did not discriminate the failure (median top/bottom deltas 0.232/0.224/0.276 px). Rejected further threshold tuning. README contains the bounded result and next structural test.
- Phase 2 closed without estimator changes. The unchanged production gate remains 9/10; no new 10 mm accuracy claim.
- Feasibility check of saved 2048-pixel samples: fresh4 same-keyframe 1058–1074 has median 36 adjacent shared pixel IDs and no ID across all 17 frames; heldout1 has median 49 adjacent and no ID across 26 frames. A multi-frame factor cannot be validated from the current subsample. Need a bounded dense correspondence capture, not a new threshold on these incomplete artifacts.
