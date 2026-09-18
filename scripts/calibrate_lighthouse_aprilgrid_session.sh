#!/usr/bin/env bash
set -eo pipefail

if [[ $# -ne 3 ]]; then
  echo "用法: $0 <D405采集会话目录> <Tracker CSV> <输出目录>" >&2
  exit 64
fi

session_dir=$(realpath "$1")
tracker_csv=$(realpath "$2")
output_dir=$(realpath -m "$3")
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
aprilgrid_yaml="$repo_root/config/aprilgrid_6x6_35mm.yaml"
camera_yaml="/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/formal_runtime_calibration/left.yaml"
right_camera_yaml="/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/formal_runtime_calibration/right.yaml"
stereo_config="/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/formal_runtime_calibration/vins_config.yaml"
camera_poses="$output_dir/aprilgrid_camera_poses.csv"
calibration_json="$output_dir/lighthouse_d405_aprilgrid_calibration.json"
time_sync_json="$output_dir/lighthouse_imu_time_offset.json"
imu_bin="$session_dir/external_imu/imu.bin"

[[ -d "$session_dir" ]] || { echo "D405会话不存在: $session_dir" >&2; exit 66; }
[[ -f "$tracker_csv" ]] || { echo "Tracker CSV不存在: $tracker_csv" >&2; exit 66; }
[[ -f "$session_dir/d405_frames.csv" ]] || {
  echo "缺少D405时钟映射: $session_dir/d405_frames.csv" >&2
  exit 66
}
[[ -f "$camera_yaml" ]] || { echo "Docker2正式左IR内参不存在: $camera_yaml" >&2; exit 66; }
[[ -f "$right_camera_yaml" ]] || { echo "Docker2正式右IR内参不存在: $right_camera_yaml" >&2; exit 66; }
[[ -f "$stereo_config" ]] || { echo "Docker2正式双目外参不存在: $stereo_config" >&2; exit 66; }
[[ -f "$imu_bin" ]] || { echo "缺少UMI 400Hz IMU: $imu_bin" >&2; exit 66; }
mkdir -p "$output_dir"

source /opt/ros/humble/setup.bash
set -u

python3 "$repo_root/scripts/extract_aprilgrid_ground_truth.py" \
  --session "$session_dir" \
  --output "$camera_poses" \
  --aprilgrid "$aprilgrid_yaml" \
  --camera-yaml "$camera_yaml" \
  --right-camera-yaml "$right_camera_yaml" \
  --stereo-config "$stereo_config"

python3 "$repo_root/scripts/estimate_lighthouse_imu_time_offset.py" \
  --capture "$imu_bin" "$tracker_csv" \
  --output "$time_sync_json"
tracker_query_offset_ms=$(python3 - "$time_sync_json" <<'PY'
import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if report.get("result") != "PASS_CANDIDATE":
    raise SystemExit("independent IMU/Tracker time synchronization did not pass")
print(report["tracker_query_offset_ms"])
PY
)

python3 "$repo_root/scripts/calibrate_lighthouse_aprilgrid.py" \
  --camera-poses "$camera_poses" \
  --tracker "$tracker_csv" \
  --aprilgrid "$aprilgrid_yaml" \
  --d405-frames "$session_dir/d405_frames.csv" \
  --body-camera-config "$stereo_config" \
  --tracker-query-offset-ms "$tracker_query_offset_ms" \
  --tracker-time-source host_monotonic \
  --output "$calibration_json"

python3 - "$calibration_json" "$camera_yaml" "$right_camera_yaml" "$stereo_config" "$aprilgrid_yaml" "$time_sync_json" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

calibration, left, right, stereo, grid, time_sync = map(Path, sys.argv[1:])
payload = json.loads(calibration.read_text(encoding="utf-8"))
if payload.get("result") != "PASS_CANDIDATE":
    raise SystemExit("refusing to freeze a calibration that did not pass")
if payload.get("calibration_target_frame") != "docker2_vins_body":
    raise SystemExit("refusing to freeze a calibration not solved at the VINS body")
if payload.get("time_offset_policy") != "fixed_from_independent_imu_tracker_sync":
    raise SystemExit("refusing to freeze a calibration with coupled time offset")
if "tracker_T_body" not in payload:
    raise SystemExit("refusing to freeze a calibration without tracker_T_body")
artifacts = {
    "calibration": calibration,
    "left_camera": left,
    "right_camera": right,
    "stereo_extrinsic": stereo,
    "aprilgrid": grid,
    "time_sync": time_sync,
}
manifest = {
    "schema": "lighthouse_d405_aprilgrid_frozen_manifest_v1",
    "result": "PASS",
    "independent_ground_truth": True,
    "slam_supervision": False,
    "artifacts": {
        name: {
            "path": str(path.resolve()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for name, path in artifacts.items()
    },
}
manifest_path = calibration.with_name("frozen_manifest.json")
manifest_path.write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
calibration.with_suffix(".sha256").write_text(
    f"{manifest['artifacts']['calibration']['sha256']}  {calibration.name}\n",
    encoding="utf-8",
)
print(f"冻结清单: {manifest_path}")
PY

echo "独立标定报告: $calibration_json"
