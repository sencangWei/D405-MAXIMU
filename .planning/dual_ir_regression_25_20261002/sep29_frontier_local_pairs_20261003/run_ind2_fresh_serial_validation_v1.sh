#!/usr/bin/env bash
# Offline only; preserve interrupted/OOM runs and never overwrite outputs.
set -euo pipefail
readonly trial_root=/home/robot/ego_vio_humble
readonly trial_python=/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/.venv/bin/python
readonly trial_base="$trial_root/.planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
readonly trial_left=reports/joint_scale_independent_four_20260927/take2/fusion/rescue/mast3r/dataset
readonly trial_right=.planning/dual_ir_regression_25_20261002/batch_v1/20260927_ind2/right_cache/dataset
cd "$trial_root"
test -x "$trial_python"
jq -e '.status == "FRONTEND_COMPLETE_NOT_SCORED"' \
  "$trial_base/full_metric_joint_frontend_control_v2/20260927_ind2/left/run_manifest.json" >/dev/null

failures=0
if "$trial_python" scripts/run_native_metric_frontend_control.py \
  --dataset "$trial_right" --paired-left-dataset "$trial_left" --eye right \
  --config config/mast3r_slam_d405_offline.yaml \
  --output "$trial_base/full_metric_joint_frontend_control_v2/20260927_ind2/right"; then
  if ! "$trial_python" scripts/evaluate_metric_joint_frontend.py \
    --manifest config/dual_ir_regression_25_20261002.json --record-id 20260927_ind2 \
    --left-frontend-dir "$trial_base/full_metric_joint_frontend_control_v2/20260927_ind2/left" \
    --right-frontend-dir "$trial_base/full_metric_joint_frontend_control_v2/20260927_ind2/right" \
    --output "$trial_base/full_metric_joint_frontend_evaluation_v1/20260927_ind2/control"; then
    failures=$((failures + 1))
  fi
else
  failures=$((failures + 1))
fi

# Previous RIGHT was externally terminated (exit143); all producer PIDs ended
# and no native full export exists. This is a fresh guarded run, not a reset of
# an alive process or a relabelled partial trajectory. Algorithm remains frozen.
if "$trial_python" scripts/run_experimental_metric_joint_frontend.py \
  --dataset "$trial_right" --paired-left-dataset "$trial_left" --eye right \
  --config config/mast3r_slam_d405_offline.yaml \
  --output "$trial_base/full_metric_joint_frontend_v2/20260927_ind2/right"; then
  if ! "$trial_python" scripts/evaluate_metric_joint_frontend.py \
    --manifest config/dual_ir_regression_25_20261002.json --record-id 20260927_ind2 \
    --left-frontend-dir "$trial_base/full_metric_joint_frontend_v1/20260927_ind2/left" \
    --right-frontend-dir "$trial_base/full_metric_joint_frontend_v2/20260927_ind2/right" \
    --output "$trial_base/full_metric_joint_frontend_evaluation_v1/20260927_ind2/candidate"; then
    failures=$((failures + 1))
  fi
else
  failures=$((failures + 1))
fi

printf 'Fresh serial local probe finished; technical stage failures=%s. Not full10/full25 acceptance.\n' "$failures"
test "$failures" -eq 0
