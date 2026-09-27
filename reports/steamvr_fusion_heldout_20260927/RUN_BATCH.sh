#!/usr/bin/env bash
# Exact existing commands, four fresh heldout takes; no parameter search.
set -eo pipefail
cd /home/robot/ego_vio_humble
source /opt/ros/humble/setup.bash
source /home/robot/ros2_ws/install/setup.bash
set -u
batch=/home/robot/ego_vio_humble/reports/steamvr_fusion_heldout_20260927/run_frozen_four
[[ ! -e "$batch" ]] || { echo 'Refuse existing batch output'; exit 2; }
captures=(
  20260927_150519_steamvr_fusion_heldout_take3_moving_retry
  20260927_150812_steamvr_fusion_heldout_take4_of6
  20260927_150958_steamvr_fusion_heldout_take5_of6
  20260927_151138_steamvr_fusion_heldout_take6_of6
)
sessions=(20260927_150523 20260927_150816 20260927_151002 20260927_151141)
for i in 0 1 2 3; do
  [[ -f "reports/steamvr_umi_sessions/${captures[$i]}/capture_manifest.json" ]]
  [[ -d "/home/robot/umi_ego_vio_data_device2_c48df736/recordings/d405_720p_rgb_stereo_ir_${sessions[$i]}" ]]
done
mkdir "$batch"
for i in 0 1 2 3; do
  case_dir="$batch/take$((i+1))"
  capture="/home/robot/ego_vio_humble/reports/steamvr_umi_sessions/${captures[$i]}"
  session="/home/robot/umi_ego_vio_data_device2_c48df736/recordings/d405_720p_rgb_stereo_ir_${sessions[$i]}"
  mkdir "$case_dir"
  echo "$(date -Is) take$((i+1)) VINS_INPUT_START" | tee -a "$batch/events.log"
  input_status=0
  input_dir="$case_dir/vins_input"
  python3 scripts/test_vins_auto_loop.py "$session" \
    --out-dir "$input_dir" --rate 0.5 --skip-s 1.5 --imu-shift-ms 0 --expect-loop any \
    --config /home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/formal_runtime_calibration/vins_config.yaml \
    --vins-executable /home/robot/ego_vio_humble/build/vins_fusion_ros2/vins_fusion_ros2_node \
    --loop-executable /home/robot/ego_vio_humble/build/vins_fusion_ros2/loop_fusion/loop_fusion_node \
    --replay-executable /home/robot/ego_vio_humble/build/vins_fusion_ros2/db3_replay_cpp \
    > "$case_dir/vins_input.log" 2>&1 || input_status=$?
  echo "$(date -Is) take$((i+1)) VINS_INPUT_END rc=$input_status" | tee -a "$batch/events.log"
  if [[ "$input_status" -ne 0 ]]; then continue; fi
  fusion_status=0
  echo "$(date -Is) take$((i+1)) FUSION_START" | tee -a "$batch/events.log"
  bash scripts/mast3r_slam_precision_workflow.sh fusion-guarded "$session" \
    "$input_dir/vio_corrected_stream.csv" "$input_dir/run_acceptance.json" "$case_dir/fusion" \
    > "$case_dir/fusion.log" 2>&1 || fusion_status=$?
  echo "$(date -Is) take$((i+1)) FUSION_END rc=$fusion_status" | tee -a "$batch/events.log"
  if [[ "$fusion_status" -ne 0 ]]; then continue; fi
  score_status=0
  python3 scripts/score_steamvr_slam.py --capture "$capture" \
    --estimate "$case_dir/fusion/trajectory_fused.csv" --query-time-domain camera \
    --output "$case_dir/official_score" > "$case_dir/scoring.log" 2>&1 || score_status=$?
  echo "$(date -Is) take$((i+1)) SCORE_END rc=$score_status" | tee -a "$batch/events.log"
done
echo "$(date -Is) BATCH_FINISHED (inspect all stage codes, not a precision PASS claim)" | tee -a "$batch/events.log"
