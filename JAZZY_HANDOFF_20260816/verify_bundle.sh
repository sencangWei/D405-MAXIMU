#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "用法：./verify_bundle.sh <ego_vio_jazzy_handoff_20260816目录>" >&2
  exit 2
fi
BUNDLE=$(realpath -e -- "$1")

declare -a REQUIRED=(
  "handoff/CODEX_HANDOFF.md"
  "projects/ego_vio_humble/.git"
  "projects/ego_vio_humble/recordings"
  "projects/ego_vio_calib_kit/.git"
  "ros2_ws_humble_snapshot/src/vins_fusion_ros2/.git"
  "ros2_ws_humble_snapshot/src/open_vins"
  "ros2_ws_humble_snapshot/src/ego_orbslam3_ros2"
  "calibration/calib_run_20260808/calib_imucam.bag"
  "calibration/calib_run_20260808/calib_imucam2.bag"
  "calibration/calib_run_20260808/calib_intrinsics.bag"
  "calibration/calib_run_20260808/calib_imucam-camchain-imucam.yaml"
  "memory/claude_project_memory/MEMORY.md"
  "metadata/SOURCE_TREE_SUMMARY.tsv"
  "metadata/git/ego_vio_humble/all_refs.bundle"
  "metadata/git/ego_vio_calib_kit/all_refs.bundle"
  "metadata/git/vins_fusion_ros2/all_refs.bundle"
  "SHA256SUMS"
)
for relative_path in "${REQUIRED[@]}"; do
  if [[ ! -e "$BUNDLE/$relative_path" ]]; then
    echo "缺失：$relative_path" >&2
    exit 3
  fi
done

echo "[1/3] 核对源/目标文件数量和普通文件字节数"
while IFS=$'\t' read -r label _source relative expected_files expected_bytes expected_symlinks; do
  [[ "$label" == "label" ]] && continue
  target="$BUNDLE/$relative"
  actual_files=$(find "$target" -type f -printf '.' | wc -c)
  actual_bytes=$(find "$target" -type f -printf '%s\n' | awk '{sum += $1} END {printf "%.0f", sum + 0}')
  actual_symlinks=$(find "$target" -type l -printf '.' | wc -c)
  if [[ "$actual_files" != "$expected_files" || "$actual_bytes" != "$expected_bytes" || "$actual_symlinks" != "$expected_symlinks" ]]; then
    echo "统计不一致：$label files $actual_files/$expected_files bytes $actual_bytes/$expected_bytes symlinks $actual_symlinks/$expected_symlinks" >&2
    exit 4
  fi
  echo "  PASS $label：$actual_files files，$actual_bytes bytes，$actual_symlinks symlinks"
done < "$BUNDLE/metadata/SOURCE_TREE_SUMMARY.tsv"

echo "[2/3] 核对三个Git仓库对象"
for repo in \
  "$BUNDLE/projects/ego_vio_humble" \
  "$BUNDLE/projects/ego_vio_calib_kit" \
  "$BUNDLE/ros2_ws_humble_snapshot/src/vins_fusion_ros2"; do
  git -C "$repo" fsck --no-dangling >/dev/null
  echo "  PASS $(basename "$repo")"
done

echo "[3/3] 核对全部文件SHA-256（约185GiB有效迁移源，可能需要较长时间）"
(
  cd "$BUNDLE"
  sha256sum -c --quiet SHA256SUMS
)

echo "VERIFICATION PASS"
