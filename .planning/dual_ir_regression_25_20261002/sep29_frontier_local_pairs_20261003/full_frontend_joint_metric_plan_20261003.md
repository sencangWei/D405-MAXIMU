# Retained joint metric candidate: complete frontend integration

Previous turn is PROGRESS, not precision acceptance: numerical repeat defect
fixed; actual seven-context replay complete/exact and local geometry improves.
The candidate stays retained. No new weights/caps or closed14-family reruns.

Smallest integration: an explicit default-off calibrated-GN adapter hook in
the actual MASt3R toolchain. The adapter consumes only the selected native
graph and source-bound raw stereo image datasets, using the already tested
eye-aware depth loader and joint factor math. It does not require enabling
stereo tracking/pointmap scaling or changing Frame/main/tracker admission.
Depths may be cached by immutable frame id, shape and K; changing canonical
pointmaps still requires preparing the current metric measurements/info.
Review correction: no accepted metric pair is a visible experimental
unavailability error, not a silent native-baseline fallback. Malformed/missing
source/code bindings or solver errors must fail visibly. Raw image cache reuse
requires stable file identities; frame/image geometry and factor math are bound.

Main ownership: global_opt.py hook, ROOT experimental adapter/context plumbing
and hook/adapter tests. Bounded executor ownership: stereo_depth.py explicit
RIGHT grid capability and ROOT right-depth tests. Do not touch user-dirty
toolchain config/base.yaml or unrelated ROOT changes.

Checks before full runs: default-off never imports/invokes the adapter; pin1,
calibrated K and no VINS metric-prior conflict; current source bindings valid;
correct RIGHT grid matches frozen raw stereo result; exact source replay
through adapter agrees with retained candidate; zero-factor solve cannot appear
as a successful candidate. Every objective/helper/native-source SHA is bound.
Both real repos require owned-remote backup/clean-restoration verification.

Then regenerate complete learned frontend trajectories, not cached scores.
Fixed failure5 FIRST + fixed passing5, same frame coverage/origin/SE3 ATE gate.
Retain majority failure improvements if every historical passing control
remains <=10mm. Full25 and new independent capture are still required for
completion. Frozen7 or prefix runtime smoke is not trajectory precision.
