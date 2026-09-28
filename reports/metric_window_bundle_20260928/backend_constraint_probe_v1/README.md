# Observation-only MASt3R backend edge/pose trace

2026-09-28. A `sitecustomize` hook in the spawned backend records each accepted
graph edge, its raw source-frame IDs and matching fractions, and the native
Sim(3) keyframe poses immediately before/after each graph solve. The hook
calls the original methods exactly once and never changes their arguments or
return values. Three full 1199-frame frozen replays (fresh4, fresh1,
heldout1) passed the existing geometry probe; both final and online trajectory
CSVs are **byte-identical** to their frozen sources in every case, with zero
capture errors. The [event census](backend_event_census.json) includes source
and input hashes. No Lighthouse/SteamVR information was used in the replay.

| Case | Official fused max ATE | Backend events / accepted edges | Accepted edges ending at raw frames 1000–1120 | Edges there spanning ≥150 source frames |
| --- | ---: | ---: | ---: | ---: |
| **fresh4** | **13.80 mm FAIL** | 223 / 299 | 84 | 3 |
| fresh1 | 8.58 mm PASS | 207 / 273 | 147 | 0 |
| heldout1 | 6.03 mm PASS | 95 / 131 | 3 | 1 |

Fresh4 has successive keyframes at raw frames 1042, 1057 and 1074, so the
frontend tracks 15 and then 17 frames from the same anchor before creating a
new keyframe. At frame 1057 it accepts links back to frames 1001/1006/1009;
at 1085 it accepts links back to 909/915. The calibrated backend pose change
at 1057 is 0.01493 in **MASt3R native, nonmetric units**, and at 1085 it is
0.02940 native units. Fresh1 also has a 15-frame gap and a 0.01902 native-unit
pose change near frame 1034; heldout1 passes despite a 53-frame gap over a
different, less-excited motion. Therefore neither a long keyframe gap nor a
large native pose step by itself identifies an erroneous edge. These native
values cannot be presented as millimetres or compared across recordings
without a justified per-window scale.

The existing [frontend log](fresh4_mast3r/frontend_log.csv) adds a concrete
local symptom: while fresh4 tracks against keyframe 1042, its relative Sim(3)
scale rises from 1.294 at raw frame 1043 to 2.720 at 1057. The same-index
controls have relative scales near one; however passing fresh1 and heldout1
also exceed two at **other** times (maxima 3.682 and 2.525), so a generic
scale clamp would make false positives. The relative translation and scale
are MASt3R-native tracking variables, not calibrated physical movement.

The frozen configuration has `metric_loop_gate` absent (its code default is
false); graph edge admission uses retrieval confirmation and visual matching.
The presence of long links is **not** proof they are false loops. Furthermore,
the final backend can retroactively move earlier keyframes, so comparing an
error's raw-frame timestamp with the order of online graph events alone does
not establish chronological causality. No edge was disabled, no threshold
changed, and the 10 mm result remains 9/10.

Next bounded test: for the actual accepted links around fresh4 frames
1042–1095, validate bidirectional D405 stereo geometry and temporal cycle
consistency in their matched pixels, then perform the same check on passing
control links **before** any graph change. Only a source-supported false-edge
mechanism should motivate a UMI-only acceptance rule; that rule must then pass
the unchanged all-ten evaluation. Do not repeat the 14 previously closed
weight/threshold families or use external-reference error to select edges.
