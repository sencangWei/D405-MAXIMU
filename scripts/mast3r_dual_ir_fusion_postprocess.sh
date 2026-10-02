#!/usr/bin/env bash
# Experimental local dual-frontend candidate. Failed graph outputs remain
# explicitly diagnostic; no external reference is read by this workflow.
set -euo pipefail
if [[ $# != 5 ]]; then
  echo "Usage: bash $0 SESSION LEFT_MAST3R_DIR RIGHT_MAST3R_DIR VINS_DIR NEW_OUTPUT_DIR" >&2
  exit 2
fi
root=/home/robot/ego_vio_humble
python=/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/.venv/bin/python
session=$1
left=$2
right=$3
vins=$4
output=$5
for input in "$session" "$left" "$right" "$vins"; do
  [[ "$input" == /* && -d "$input" ]] || { echo "Invalid input directory: $input" >&2; exit 2; }
done
[[ "$output" == "$root/"* && ! -e "$output" && ! -L "$output" ]] || { echo "Requires a new output directory under $root" >&2; exit 2; }
mkdir -p "$output"
vins_config=/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/formal_runtime_calibration/vins_config.yaml
imu_config="$root/config/imu_runtime_accel_calibrated_raw_gyro_20260816.yaml"
"$python" - "$root" "$left" "$right" "$vins" "$output" "$vins_config" "$imu_config" <<'PY'
import hashlib
import json
from pathlib import Path
import sys
root, left, right, vins, output, vins_config, imu_config = map(Path, sys.argv[1:])
inputs = [
    root / "scripts/fuse_mast3r_stereo_imu.py",
    root / "scripts/mast3r_dual_ir_fusion_postprocess.sh",
    root / "ego_vio/vio/dual_ir_factors.py",
    left / "trajectory_imu_metric.csv", left / "imu_scale_report.json",
    right / "imu_metric_trajectory.csv", right / "imu_scale_report.json",
    vins / "vio_corrected_stream.csv", vins / "run_acceptance.json",
    vins_config, imu_config,
]
inputs.extend(left / name for name in (
    "stereo_scale_bidirectional_report.json", "stereo_scale_long_hops_report.json",
    "stereo_scale_dense10hz_report.json", "stereo_scale_multisecond_report.json",
))
inputs.extend(right / name for name in (
    "stereo_scale_right_report.json", "stereo_scale_long_hops_right_report.json",
    "stereo_scale_dense10hz_right_report.json", "stereo_scale_multisecond_right_report.json",
))
manifest = {
    "schema": "umi_dual_ir_experimental_candidate_v1",
    "status": "EXPERIMENTAL_NOT_ACCEPTED",
    "accepted": False, "external_ground_truth_used": False,
    "input_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs},
    "output": str(output),
}
(output / "candidate_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
PY
set +e
"$python" "$root/scripts/fuse_mast3r_stereo_imu.py" \
  --session "$session" --trajectory "$left/trajectory_imu_metric.csv" --stream infrared_left \
  --stereo-report "$left/stereo_scale_bidirectional_report.json" \
  --additional-stereo-report "$left/stereo_scale_long_hops_report.json" \
  --additional-stereo-report "$left/stereo_scale_dense10hz_report.json" \
  --additional-stereo-report "$left/stereo_scale_multisecond_report.json" \
  --imu-scale-report "$left/imu_scale_report.json" \
  --vins-config "$vins_config" --imu-calibration "$imu_config" --expected-td-s -0.009109323 \
  --orientation-node-stride 10 --position-node-stride 5 --minimum-stereo-sample-hop 1 \
  --keyframe-dir "$left/mast3r_logs/keyframes/dataset" \
  --relative-motion-trajectory "$vins/vio_corrected_stream.csv" \
  --relative-motion-report "$vins/run_acceptance.json" --relative-motion-sigma-m 0.008 \
  --auto-visual-position-sigma --joint-max-correction-mm 1000 --joint-correction-cap-mode per-node \
  --joint-metric-scale-optimization --full-rate-imu-position-refinement --full-rate-max-correction-mm 20 \
  --metric-scale-mode joint --scale-disagreement-policy diagnose --position-mode keyframe-graph \
  --secondary-right-trajectory "$right/imu_metric_trajectory.csv" \
  --secondary-right-stereo-report "$right/stereo_scale_right_report.json" \
  --secondary-right-additional-stereo-report "$right/stereo_scale_long_hops_right_report.json" \
  --secondary-right-additional-stereo-report "$right/stereo_scale_dense10hz_right_report.json" \
  --secondary-right-additional-stereo-report "$right/stereo_scale_multisecond_right_report.json" \
  --secondary-right-imu-scale-report "$right/imu_scale_report.json" \
  --write-failed-output --output "$output/trajectory_graph.csv" --report "$output/graph_fusion_report.json" \
  > "$output/graph.log" 2>&1
graph_rc=$?
set -e
[[ "$graph_rc" == 0 || "$graph_rc" == 3 ]] || exit "$graph_rc"
[[ -f "$output/trajectory_graph.csv" ]] || exit 2
"$python" "$root/scripts/fuse_docker2_mast3r_complementary.py" \
  --mast3r "$output/trajectory_graph.csv" --docker2 "$vins/vio_corrected_stream.csv" \
  --docker2-report "$vins/run_acceptance.json" --body-t-camera-yaml "$vins_config" \
  --scale-horizon-s 1 --smoothing-s 8 --docker2-local-weight 0 --docker2-scale-weight 0.25 \
  --graph-report "$output/graph_fusion_report.json" --roughness-threshold-mm 9 --adaptive-weight-strength 0.45 \
  --output "$output/trajectory_fused_unsmoothed.csv" --report "$output/fusion_report.json" \
  > "$output/complementary.log" 2>&1
"$python" "$root/scripts/smooth_pose_trajectory.py" \
  --input "$output/trajectory_fused_unsmoothed.csv" --output "$output/trajectory_fused.csv" \
  --method gaussian --gaussian-sigma-s 0.025 --report "$output/smoothing_report.json" \
  > "$output/smoothing.log" 2>&1
echo "Experimental candidate frozen: $output (graph return code $graph_rc; inspect graph report before acceptance)"
