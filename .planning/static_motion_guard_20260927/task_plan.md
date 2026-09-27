# Static-motion graph protection

## Scope and hypothesis

Repair the graph's missing stationary displacement constraint, using only UMI
visual/body motion, independently accepted VINS relative motion and IMU. The
fresh heldout take2 is healthy raw data: 5–10s reference range .769mm, VINS
.810mm, pregraph .037mm, graph 10.849mm. The graph improves global scale error
but introduces artificial movement during this legitimate long pause.

No Tracker/GT input to optimization, no per-recording thresholds, no scale or
global-weight sweeps, no closed 14-family experiments, no capture deletion.
Preserve original small motion rather than flattening the trajectory to GT.

## Execution

1. RED tests for conservative joint static detection, gaps, invalid inputs,
   constant-velocity/rotating motion, and optimizer displacement preservation.
2. Add persistent stationary relative-position factors to the existing graph.
   Protect the same independently detected spans in full-rate refinement.
3. Run focused existing tests and six cached downstream stage7–9 regressions
   (development1, development2 rescue, four fresh heldout). Immutable input
   observations, calibration and frontend outputs; new output directories.
4. Independently review changes, fix issues, verify tests and six scores.
5. Commit exact capability/tests/evidence to owned sencang branch, no force
   push; fetch and restore source bytes/tests/evaluation before reporting backup.

## Acceptance

Confirmed stationary range in failed take2's 5–10s should be <=2mm (previous
10.893mm). Movement must not be frozen: quiet IMU alone and inconsistent visual
or VINS movement must never activate the guard. No lost poses/timestamps.
All five prior passing cases must remain max <=10mm under unchanged SE3-only
official scoring. Report all six scores including failure; new take2 <=10mm
is a desired precision gate, not permission to change thresholds or use GT.

## Progress

- Root-cause analysis complete; RED detector tests assigned to a bounded agent.
- Initial source target was clean against main HEAD a8f44e37 before this fix.
- RED synthetic graph reproduced9.584mm false static motion; detector's4gap/
  rotation bridge tests reproduced failures before quiet-edge merge fix.
- Implemented consensus detector + correction-difference factors in graph and
  full-rate refinement. No GT used and no hard zero-position/velocity clamp.
-162focused/adjacenttestsPASS. First sixcachedregressionfinished: priorfive
  PASS retained, allsix mean/max improved. heldout2stillFAIL max11.707mm.
- Failed take2's5–10s final static bbox range10.892→.241mm, reference.769mm.
- Independent reviewer found no coreblockingissue; requestedqualityscript hash
  in runner. Added it; rerunning exact sixcachedcommands into verified_v1_six.
- FinalreviewAPPROVEforboundedstaticfix,notuniversal10mm. Reviewerindependently
  confirmedall6gates/inputs/sourcehashes andran219adjacenttestsPASS.
- Code/tests/protocol/all6finaloutputs backedtoownedremote sencang branch
  codex/fusion-static-guard-20260927 commit27872422b60dad3d744a2ad851df1a0c632b4553.
  Freshfetchedrestore/tmp/ego_vio_static_guard_restore_rECuQb:127changedfiles
  byteidentical,115restoredtestsPASS. All6restoredrescoresmatch30numericfields
  eachwithin1e-12 andsamePASS/FAIL/thresholds. Restoredtrackedtreeunchanged.
- Boundedstatic-motionfix COMPLETE. Globalmax10goal remainsunmet;heldout2's
  remainingpeak11.707mm ismovingframe692 at23.063s,notthestatic5–10sinterval.
