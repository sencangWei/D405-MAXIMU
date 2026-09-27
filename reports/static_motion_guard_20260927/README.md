# Long-stop graph motion guard — 2026-09-27

## Outcome and limits

The missing static relative-displacement constraint is repaired without
Tracker/GT supervision or hard zeroing the measured motion. All five previously
passing recordings remain passing; all six means and maxima improve. This is
not a claim that the overall10mm goal is complete: heldout2 still fails max.

Failed fresh take2, evaluated5–10s interval:

- Final body position 3D bounding-box range:10.89249→0.24131mm.
- External reference range:.76910mm; independent VINS was.810mm.
- Raw pregraph metric visual positions were nearly stationary; the original
  joint graph introduced the false movement, not raw capture/frame loss.
- Guarded5–10s ATE maximum7.871mm, under unchanged10mm precision gate.
- Remaining peak11.707mm is at23.063s, frame692, in the moving portion,
 560.95mm from reference start.104of1143frames remain above10mm,16.929–24.997s.
  These are retained. The original370badframes are not deleted or interpolated
  against GT. Persistent global scale/shape error remains to investigate.
- Diagnostic-only Sim3 scale .968622 suggests residual scale mismatch; no
  GT-derived scaling or per-recording correction is applied to production.

## Frozen six-case comparison

SE3 only, no scale alignment; same calibration/time composition, raw recordings,
precomputed frontend/stereo/IMU products and candidate policy. Development2
retains its existing internally selected metric-rescue candidate. All other
five retain baseline. No retraining, new threshold sweep or per-take tuning.

| Case | Old max mm | Guard max mm | Guard mean mm | Guard P95 mm | <=10mm | Result |
|---|---:|---:|---:|---:|---:|---|
| development1 |7.01849|6.88342|2.82226|5.47140|100%|PASS|
| development2 rescue |9.60652|9.45862|4.67513|8.494|100%|PASS|
| heldout1 |9.95631|9.90944|3.66537|7.025|100%|PASS|
| heldout2 |13.26556|11.70744|7.11819|11.024|90.901%|FAIL|
| heldout3 |9.25969|9.23629|5.51190|8.668|100%|PASS|
| heldout4 |9.96018|9.95174|6.68062|9.295|100%|PASS|

Exact authoritative values are in `verified_v1_six/<case>/official_score/precision.json`.
Take2 has389 protected frames. All1143/1144 pose timestamps and sample counts
are retained; reference overlap100%. Algorithmic input/quality stages allPASS.
`score` rc3 for heldout2 is a completed FAIL, not a pipeline crash.

## What changed

Only production algorithm file `scripts/fuse_mast3r_stereo_imu.py`:

1. Detect >=1s spans with <=1mm bounded body motion in both learned visual
   odometry and independently validated VINS relative odometry. Require IMU
   angular speed<=1deg/s, attitude change<=1deg, acceleration norm within.6m/s2
   of gravity and component scatter<=.1m/s2. Quiet IMU alone cannot certify
   constant-velocity translation as static. Missing/invalid VINS does not
   activate protection.
2. Require every merged interval be covered by a valid quiet window; do not
   bridge camera gaps>.1s, IMU gaps>.02s, invalid poses or short gyro bursts.
3. Add.5mm-sigma relative-correction factors to the joint graph and its
   full-rate acceleration refinement. Preserve original measured micro-motion;
   no absolute Tracker position or zero-motion trajectory replacement.
4. Emit protected interval indices/times and GT-free policy in both reports.

No changes to weights outside those new physical constraints, metric scale
policy, frontend/checkpoint, extrinsics, td, evaluation gates or frame selection.
Formaltd=-.009109323s and replay shift0 remain unchanged. None of the closed
14 families are rerun.

## Tests, review and reproducibility

- RED synthetic graph test reproduced9.584mm invented static motion beforefix.
- RED long-hold camera/IMU gap, gyro-burst and slow-rotation tests reproduced
 4incorrect window unions before validated-edge/cumulative-angle protection.
-162focused/adjacent testsPASS, including17new guard tests. Compile/diff-checkPASS.
- Independent read-only code review: no blocking algorithm/spec/securityissue;
  GT appears only at post-hoc scoring. One minor request to hash the quality
  script was fixed, then sixcached stages7–9 were rerun with the complete hashset.
- The trial `candidate_v1_six` and final `verified_v1_six` use the same algorithm;
  final run adds missing quality-script source provenance, not a new parameter.
  All six final trajectory CSVs are byte-identical between the two runs.
- Cached replay entrypoint:

```sh
cd /home/robot/ego_vio_humble
rtk proxy python3 .planning/static_motion_guard_20260927/run_cached_regression.py \
  --output reports/static_motion_guard_20260927/new_unique_replay
```

Output must not already exist. Runner validates cached files before creation,
records exact commands/input/source hashes, verifies unchanged files after each
case, and scores only after optimization. It needs the existing local cached
observations, image-keyframe metadata, UMI recordings and installed toolchain;
remote evidence backup is not a portable archive of all raw data.

## Deployment and remaining risk

The existing one-key workflow invokes the modified graph automatically; no new
manual stop command is needed. Fresh independent long-stop recordings are still
needed to validate generalization. This bounded fix protects legitimate stops;
it cannot guarantee arbitrary SLAM trajectories will meet max10mm. Further
moving-segment scale/shape optimization must remain GT-free and multi-case.

## Verified recovery artifact

Code/tests/protocol and six final outputs were committed to owned remote
`sencang` (`https://github.com/sencangWei/D405-MAXIMU.git`), branch
`codex/fusion-static-guard-20260927`, capability commit
`27872422b60dad3d744a2ad851df1a0c632b4553`. No force push; no broad reports staging.

Fresh fetched detached restore `/tmp/ego_vio_static_guard_restore_rECuQb`:

-127changed files compared against active workspace: byte-identical.
-115restored focused testsPASS, including graph/full-rate/static detection.
- Six restored trajectory/reference rescores in
  `/tmp/ego_vio_static_guard_rescore__g0by0pi`: all30numeric metrics per case
  match originalwithin1e-12, same thresholds/failures/5PASS/1FAIL.
- Restoredtrackedtreeunchanged after tests/rescoring.

This recovery proves the capability and scoring evidence, not portability of
the installed MASt3R toolchain or a backup of all raw recordings. The bounded
static fix is accepted; moving-segment max10mm precision remains unresolved.
