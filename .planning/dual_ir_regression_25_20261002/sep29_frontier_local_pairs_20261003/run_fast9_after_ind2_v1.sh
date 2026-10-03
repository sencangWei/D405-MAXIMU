#!/usr/bin/env bash
# Offline-only continuation. Wait for the exact existing run, not a reused PID.
set -euo pipefail
readonly trial_root=/home/robot/ego_vio_humble
readonly trial_python=/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/.venv/bin/python
readonly trial_base="$trial_root/.planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"

same_process() {
  local process_pid="$1" expected_start="$2" process_state
  if [[ ! -e "/proc/$process_pid/stat" ]]; then
    return 1
  fi
  if ! process_state=$(awk '{print $22, $3}' "/proc/$process_pid/stat"); then
    [[ ! -e "/proc/$process_pid/stat" ]] || exit 1
    return 1
  fi
  [[ "$process_state" != "$expected_start Z" && "$process_state" == "$expected_start "* ]]
}

cd "$trial_root"
test -x "$trial_python"
test -f scripts/run_metric_joint_fast10.py
test ! -e "$trial_base/full_metric_joint_fast9_v1"
test ! -L "$trial_base/full_metric_joint_fast9_v1"
printf 'Waiting for exact ind2 serial run and native producer to end; no parallel GPU producer.\n'
while same_process 3403073 22998168 || same_process 3408546 23030042 || same_process 3408726 23030765; do
  sleep 5
done

printf 'Prior processes ended. Starting remaining nine in fixed failure-then-control order.\n'
exec "$trial_python" scripts/run_metric_joint_fast10.py --run \
  --output-root "$trial_base/full_metric_joint_fast9_v1" \
  --reuse-root "$trial_base/full_metric_joint_frontend_v1" \
  --record-id 20260929_take02 \
  --record-id 20260929_take04 \
  --record-id 20260929_take07 \
  --record-id 20260930_take06 \
  --record-id 20260927_heldout2 \
  --record-id 20260927_heldout4 \
  --record-id 20260929_take01 \
  --record-id 20260930_take03 \
  --record-id 20260930_take04
