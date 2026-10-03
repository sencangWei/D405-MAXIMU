#!/usr/bin/env bash
# Diagnostic prefixes only. Never interrupt or overlap the retained fixed10 queue.
set -euo pipefail
readonly capture_root=/home/robot/ego_vio_humble
readonly capture_python=/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/.venv/bin/python
readonly capture_base="$capture_root/.planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"

same_queue() {
  local process_state
  [[ -e /proc/3491727/stat ]] || return 1
  if ! process_state=$(awk '{print $22, $3}' /proc/3491727/stat); then
    [[ ! -e /proc/3491727/stat ]] || exit 1
    return 1
  fi
  [[ "$process_state" != '23315633 Z' && "$process_state" == '23315633 '* ]]
}

cd "$capture_root"
[[ $(realpath -e "$capture_base") == "$capture_base" ]]
test -x "$capture_python"
test -f scripts/run_mast3r_tracking_input_capture.py
printf 'Waiting for the exact retained fixed10 queue; no producer is stopped.\n'
while same_queue; do
  sleep 10
done

# Each runner revalidates source/model/image identities and refuses a busy GPU.
# Preserve failure evidence and still attempt the independent second locus.
capture_rc=0
"$capture_python" scripts/run_mast3r_tracking_input_capture.py --run \
  --source-run "$capture_base/full_metric_joint_frontend_v2/20260927_ind2/right" \
  --output "$capture_base/tracking_input_capture_v1/20260927_ind2/right" \
  --frame-id 576 --frame-id 577 --frame-id 578 || capture_rc=1
"$capture_python" scripts/run_mast3r_tracking_input_capture.py --run \
  --source-run "$capture_base/full_metric_joint_fast9_v1/20260929_take04/left" \
  --output "$capture_base/tracking_input_capture_v1/20260929_take04/left" \
  --frame-id 806 --frame-id 807 --frame-id 808 || capture_rc=1
exit "$capture_rc"
