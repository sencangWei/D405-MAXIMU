# Retain the effective candidate; inspect its unconstrained interval frames

This checkpoint is diagnostic capability, not a precision improvement or PASS.
The retained metric-joint candidate, native solver, config, gates, and fixed10
cohort remain unchanged. The user policy still retains majority improvements
when the five passing controls do not cross 10 mm at comparable full coverage;
technical/missing comparisons are incomplete, not evidence of uselessness.

## New isolated observer and source-bound launcher

- `scripts/capture_mast3r_tracking_inputs.py` wraps only a replay process, cloning
  original learned inference, tracking inputs/outputs and local GN increments
  for explicitly requested actual frame IDs. Nonselected calls pass through.
  Patches and import paths are restored. Original errors/soft failures remain
  failures; new snapshots cannot overwrite an existing file.
- `scripts/run_mast3r_tracking_input_capture.py` binds the retained complete
  source replay, original config/model/code, full source clocks and raw image
  identities. Prefixes are explicitly diagnostic, never full-trajectory/ATE
  outputs. It refuses an active fixed10 queue even between GPU children, and
  refuses another compute producer. It uses no Tracker or robot supervision.
- `capture_after_fixed10_v1.sh` waits for queue PID 3491727 / start-time 23315633;
  no process is interrupted. It then serially captures ind2 RIGHT 576/577/578
  and take04 LEFT 806/807/808 into new directories, preserving failures.

## Fresh verification and actual execution state

The focused observer/launcher/retention/frontend/queue suite passed **69/69** in
22.02 s. Compilation and shell syntax checks passed. Independent code review
approved the four source/test files and the scheduler with no reported issues.
The real subprocess spawn test is CPU-only with fake modules; it does not
establish native CUDA/backend success.

Both actual source-bound dry-runs exited zero: original full sources have 1199
frames, and the requested diagnostic prefixes contain 580 / 810 frames. No
capture output or model producer was created by these dry-runs.

The scheduler actually launched (PID 3569517, exec session 30064) and is waiting
for the exact live fixed10 queue (session 8300). At this checkpoint take07 LEFT
is complete at 1199/1199 and RIGHT is running (producer PID 3548559). Terminal
records remain ind2 EVALUATION_FAILED, take02 COVERAGE_FAILED, and take04
EVALUATION_FAILED. No new comparable ATE or fixed10/full25 acceptance exists.
The scheduler has not yet launched the native diagnostic prefixes.

## Next causal check, not a speculative repair claim

First compare captured prefix online poses against the retained full replay and
replay the original local solve from the exact inputs. Then prepare independent
bidirectional stereo geometry for the same reference/current frames using the
existing gates, and test a soft seven-state metric factor without hard pose
replacement, clipping, interpolation, lost-frame removal or GT inputs. A
benefit on this local check still requires failed5 then passing5 and eventually
full25/new independent recordings. Preserve the helpful LEFT scale component.

Same-day source backup is required on ROOT's owned `sencang` remote; the actual
fetched commit/clean restore evidence is recorded in the goal checkpoint only
after verification. Raw recordings, snapshots, calibration, model checkpoint
and native runtime are not covered by a source-only backup.

RK3576 deployment remains externally blocked by unreachable target and absent
actual ARM candidate/native runtime. Source tests do not constitute deployment
or no-motion HIL acceptance; no live mutations or excluded QR/network/package/
udev/reboot/motion changes were made.
