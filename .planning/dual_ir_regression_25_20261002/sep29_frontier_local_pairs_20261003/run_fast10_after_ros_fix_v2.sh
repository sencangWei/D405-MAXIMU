#!/usr/bin/env bash
# Offline continuation: preserve the running frontend and use its terminal output.
set -euo pipefail
readonly trial_root=/home/robot/ego_vio_humble
readonly trial_python=/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/.venv/bin/python
readonly trial_base="$trial_root/.planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
readonly trial_reuse="$trial_base/full_metric_joint_fast10_reuse_v2"
readonly trial_output="$trial_base/full_metric_joint_fast10_v2"

same_process() {
  local process_pid="$1" expected_start="$2" process_state
  [[ -e "/proc/$process_pid/stat" ]] || return 1
  if ! process_state=$(awk '{print $22, $3}' "/proc/$process_pid/stat"); then
    [[ ! -e "/proc/$process_pid/stat" ]] || exit 1
    return 1
  fi
  [[ "$process_state" != "$expected_start Z" && "$process_state" == "$expected_start "* ]]
}

link_terminal() {
  local record_id="$1" eye="$2" source_relative="$3" source_path destination
  [[ "$record_id" == 20260927_ind2 || "$record_id" == 20260929_take02 || "$record_id" == 20260929_take04 ]]
  [[ "$eye" == left || "$eye" == right ]]
  source_path=$(realpath -e "$trial_base/$source_relative")
  [[ "$source_path" == "$trial_base/"* && -d "$source_path" ]]
  jq -e '.status == "FRONTEND_COMPLETE_NOT_SCORED" or .status == "FRONTEND_FAILED"' \
    "$source_path/run_manifest.json" >/dev/null
  destination="$trial_reuse/$record_id/$eye"
  [[ ! -e "$destination" && ! -L "$destination" ]]
  mkdir -p "$trial_reuse/$record_id"
  # These links are read-only reuse inputs, not a temporary cleanup tree.
  ln -s -- "$source_path" "$destination"
}

cd "$trial_root"
[[ $(realpath -e "$trial_base") == "$trial_base" ]]
test -x "$trial_python"
test -f scripts/run_metric_joint_fast10.py
[[ ! -e "$trial_reuse" && ! -L "$trial_reuse" ]]
[[ ! -e "$trial_output" && ! -L "$trial_output" ]]
printf 'Waiting for the exact old queue/current frontend to finish; no process is terminated.\n'
while same_process 3453631 23186086 || same_process 3469663 23251436; do
  sleep 5
done
jq -e '.status == "QUEUE_ERROR_SOURCE_CHANGED"' \
  "$trial_base/full_metric_joint_fast9_v1/summary.json" >/dev/null

mkdir "$trial_reuse"
link_terminal 20260927_ind2 left full_metric_joint_frontend_v1/20260927_ind2/left
link_terminal 20260927_ind2 right full_metric_joint_frontend_v2/20260927_ind2/right
link_terminal 20260929_take02 right full_metric_joint_frontend_v1/20260929_take02/right
link_terminal 20260929_take04 left full_metric_joint_fast9_v1/20260929_take04/left

printf 'Starting fixed failing5 then passing5 with corrected evaluator and unchanged frontend.\n'
exec "$trial_python" scripts/run_metric_joint_fast10.py --run \
  --output-root "$trial_output" --reuse-root "$trial_reuse"
