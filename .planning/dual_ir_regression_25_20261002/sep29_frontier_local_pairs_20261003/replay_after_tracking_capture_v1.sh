#!/usr/bin/env bash
# CPU original-local-solve diagnostics after the exact native capture scheduler.
set -euo pipefail
set -C
readonly replay_root=/home/robot/ego_vio_humble
readonly replay_python=/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/.venv/bin/python
readonly replay_base="$replay_root/.planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
readonly replay_output="$replay_base/tracking_opt_replay_v1"

same_capture_scheduler() {
  local process_state
  [[ -e /proc/3569517/stat ]] || return 1
  if ! process_state=$(awk '{print $22, $3}' /proc/3569517/stat); then
    [[ ! -e /proc/3569517/stat ]] || exit 1
    return 1
  fi
  [[ "$process_state" != '23591558 Z' && "$process_state" == '23591558 '* ]]
}

cd "$replay_root"
[[ $(realpath -e "$replay_base") == "$replay_base" ]]
test -x "$replay_python"
test -f scripts/replay_mast3r_tracking_capture.py
printf 'Waiting for exact native capture scheduler; no job is stopped.\n'
while same_capture_scheduler; do
  sleep 10
done

# The CPU reader requires terminal, complete, unchanged source-bound snapshots.
# Preserve each failure log and attempt all six actual frames independently.
[[ ! -e "$replay_output" && ! -L "$replay_output" ]]
mkdir "$replay_output"
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
replay_rc=0
for replay_frame in 576 577 578; do
  "$replay_python" scripts/replay_mast3r_tracking_capture.py \
    --capture-run "$replay_base/tracking_input_capture_v1/20260927_ind2/right" \
    --frame-id "$replay_frame" --output "$replay_output/ind2_right_$replay_frame.json" \
    > "$replay_output/ind2_right_$replay_frame.log" 2>&1 || replay_rc=1
done
for replay_frame in 806 807 808; do
  "$replay_python" scripts/replay_mast3r_tracking_capture.py \
    --capture-run "$replay_base/tracking_input_capture_v1/20260929_take04/left" \
    --frame-id "$replay_frame" --output "$replay_output/take04_left_$replay_frame.json" \
    > "$replay_output/take04_left_$replay_frame.log" 2>&1 || replay_rc=1
done
exit "$replay_rc"
