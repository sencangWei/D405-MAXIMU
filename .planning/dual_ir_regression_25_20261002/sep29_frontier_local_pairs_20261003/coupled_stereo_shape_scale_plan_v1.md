# Coupled metric pointmap/gauge falsifier

Diagnostic only; no reference input, score, export, or production promotion.
Use the SAME seven source-bound jobs in depth_shape_GN_falsifier_plan_v1.json.
Five frozen graphs from the failing Sep29 take02, plus two independent passing
recording controls. Graphs are not seven independent recordings.

Hypothesis: learned relative pointmap depth shape and relative Sim3 pose gauge
are inconsistent together. Fixing only one state leaves the other free/wrong.
For supported pixel k in frame i, previous shape conditioning makes
Z'_ik = D_ik/r_i. With fixed s_i=s0*r_i/r0, s_i*Z'_ik=s0*D_ik/r0:
all supported depths share one global metric gauge. This is not a new weight.

Apply both previously isolated operations together. Unsupported points remain
byte-identical. All original edges, correspondence indices, confidence values,
validity masks, sigmas, thresholds, iteration budget, and pose0 stay unchanged.
Original native replay must match the frozen control exactly, fixed solve must
repeat exactly, scales stay exact, and scale increments stay zero.

Predeclared criterion before solving: RIGHT chronological accepted-PnP maximum
rotation disagreement must be <2 degrees on all three RIGHT graphs. Neither
passing control's maximum may increase by >0.05 degrees. Any technical failure,
input/invariant mismatch, or failed geometry criterion rejects this candidate
before frontend and fixed10 ATE. These geometry tests are NOT 10mm precision.
