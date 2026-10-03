#!/usr/bin/env bash
# Serial real-pair diagnostics only after the exact capture/CPU replay chain.
set -euo pipefail
set -C
readonly probe_root=/home/robot/ego_vio_humble
readonly probe_python=/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/.venv/bin/python
readonly probe_base="$probe_root/.planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
readonly probe_output="$probe_base/local_tracking_metric_probe_v1"

same_replay_scheduler() {
  local process_state
  [[ -e /proc/3609162/stat ]] || return 1
  if ! process_state=$(awk '{print $22, $3}' /proc/3609162/stat); then
    [[ ! -e /proc/3609162/stat ]] || exit 1
    return 1
  fi
  [[ "$process_state" != '23715053 Z' && "$process_state" == '23715053 '* ]]
}

cd "$probe_root"
[[ $(realpath -e "$probe_base") == "$probe_base" ]]
test -x "$probe_python"
# Freeze this new diagnostic slice before waiting; native inputs have their
# separate capture SHA bindings. Do not mix helper revisions across six frames.
probe_hashes=$(sha256sum \
  "$probe_base/probe_local_tracking_metric.py" \
  "$probe_base/local_tracking_pair_graph.py" \
  "$probe_base/local_tracking_pair_inputs.py" \
  "$probe_base/local_tracking_reverse_decoder.py" \
  "$probe_base/local_tracking_metric_candidate.py" \
  "$probe_root/scripts/replay_mast3r_tracking_capture.py")
[[ -n "$probe_hashes" ]]
readonly probe_hashes
printf 'Waiting for exact capture/replay chain; retained queue is not stopped.\n'
while same_replay_scheduler; do
  sleep 10
done

[[ ! -e "$probe_output" && ! -L "$probe_output" ]]
mkdir "$probe_output"
printf '%s\n' "$probe_hashes" > "$probe_output/source.sha256"
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1
probe_rc=0
for probe_case in ind2_right:20260927_ind2:right:576 ind2_right:20260927_ind2:right:577 ind2_right:20260927_ind2:right:578 \
                  take04_left:20260929_take04:left:806 take04_left:20260929_take04:left:807 take04_left:20260929_take04:left:808; do
  IFS=: read -r probe_label probe_id probe_eye probe_frame <<< "$probe_case"
  if ! sha256sum --check --status "$probe_output/source.sha256"; then
    printf 'SOURCE_CHANGED: not running %s frame %s\n' "$probe_label" "$probe_frame" >&2
    probe_rc=1
    continue
  fi
  "$probe_python" -B "$probe_base/probe_local_tracking_metric.py" --run \
    --capture-run "$probe_base/tracking_input_capture_v1/$probe_id/$probe_eye" \
    --frame-id "$probe_frame" --output "$probe_output/${probe_label}_$probe_frame" \
    > "$probe_output/${probe_label}_$probe_frame.log" 2>&1 || probe_rc=1
done
exit "$probe_rc"
