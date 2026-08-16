#!/usr/bin/env bash
set -euo pipefail

BUNDLE_NAME="ego_vio_jazzy_handoff_20260816"
HANDOFF_SOURCE="/home/robot/ego_vio_humble/JAZZY_HANDOFF_20260816"
MIN_HEADROOM_BYTES=$((20 * 1024 * 1024 * 1024))
DRY_RUN=0

usage() {
  cat <<'EOF'
用法：
  ./copy_to_ssd.sh [--dry-run] /media/robot/<移动固态挂载名>

要求：目标必须是独立挂载的 ext4/btrfs/xfs 文件系统根目录。
脚本不删除源文件、不使用 rsync --delete，可在中断后安全重跑续传。
EOF
}

if [[ ${1:-} == "--dry-run" ]]; then
  DRY_RUN=1
  shift
fi
if [[ $# -ne 1 ]]; then
  usage >&2
  exit 2
fi

DEST_INPUT=$1
if [[ ! -d "$DEST_INPUT" ]]; then
  echo "错误：目标目录不存在：$DEST_INPUT" >&2
  exit 2
fi
DEST_ROOT=$(realpath -e -- "$DEST_INPUT")
MOUNT_TARGET=$(findmnt -n -o TARGET --target "$DEST_ROOT" || true)
FS_TYPE=$(findmnt -n -o FSTYPE --target "$DEST_ROOT" || true)
if [[ -z "$MOUNT_TARGET" || "$DEST_ROOT" != "$MOUNT_TARGET" ]]; then
  echo "错误：请传入移动固态的挂载根目录，而不是其子目录。" >&2
  echo "目标=$DEST_ROOT，检测到的挂载点=${MOUNT_TARGET:-无}" >&2
  exit 2
fi
if [[ "$DEST_ROOT" == "/" || "$DEST_ROOT" == "/home" || "$DEST_ROOT" == "/home/robot" ]]; then
  echo "错误：拒绝把系统目录当成移动固态。" >&2
  exit 2
fi
case "$FS_TYPE" in
  ext4|btrfs|xfs) ;;
  *)
    echo "错误：目标文件系统为 ${FS_TYPE:-未知}，无法可靠保留Linux权限、符号链接和Git工作树。" >&2
    echo "请使用ext4（推荐）、btrfs或xfs。" >&2
    exit 2
    ;;
esac

if ps -eo args= | grep -E '[c]apture_d405|[r]os2 bag record' >/dev/null; then
  echo "错误：检测到正在采集/录bag。请先正常结束采集，再复制一致性快照。" >&2
  exit 3
fi

declare -a SOURCES=(
  "/home/robot/ego_vio_humble"
  "/home/robot/桌面/ego_vio_calib_kit"
  "/home/robot/ros2_ws"
  "/tmp/calib_run"
  "/home/robot/.claude/projects/-home-robot----ego-vio-calib-kit/memory"
  "/home/robot/.codex/skills"
)
for source_path in "${SOURCES[@]}"; do
  if [[ ! -d "$source_path" ]]; then
    echo "错误：必须源目录缺失：$source_path" >&2
    exit 4
  fi
done

source_bytes=0
for source_path in "${SOURCES[@]}"; do
  bytes=$(du -sb -- "$source_path" | awk '{print $1}')
  source_bytes=$((source_bytes + bytes))
done
required_bytes=$((source_bytes + MIN_HEADROOM_BYTES))
available_bytes=$(df -B1 --output=avail "$DEST_ROOT" | tail -n 1 | tr -d ' ')
echo "[容量] 必须源目录约 $(numfmt --to=iec-i --suffix=B "$source_bytes")"
echo "[容量] 含20GiB余量需 $(numfmt --to=iec-i --suffix=B "$required_bytes")"
echo "[容量] 移动固态可用 $(numfmt --to=iec-i --suffix=B "$available_bytes")"
if (( available_bytes < required_bytes )); then
  echo "错误：移动固态空间不足。" >&2
  exit 5
fi

BUNDLE="$DEST_ROOT/$BUNDLE_NAME"
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[DRY-RUN] 将创建/续传：$BUNDLE"
  echo "[DRY-RUN] 检查通过；未写入任何文件。"
  exit 0
fi

mkdir -p -- \
  "$BUNDLE/projects" \
  "$BUNDLE/calibration" \
  "$BUNDLE/diagnostics/realtime_runs" \
  "$BUNDLE/memory" \
  "$BUNDLE/codex" \
  "$BUNDLE/legacy_and_protocols" \
  "$BUNDLE/metadata/git" \
  "$BUNDLE/handoff"

RSYNC=(rsync -aH --partial --human-readable --info=progress2)
copy_tree() {
  local source_path=$1
  local target_path=$2
  echo "[复制] $source_path -> $target_path"
  mkdir -p -- "$target_path"
  "${RSYNC[@]}" -- "$source_path/" "$target_path/"
}

copy_tree "/home/robot/ego_vio_humble" "$BUNDLE/projects/ego_vio_humble"
copy_tree "/home/robot/桌面/ego_vio_calib_kit" "$BUNDLE/projects/ego_vio_calib_kit"
copy_tree "/home/robot/ros2_ws" "$BUNDLE/ros2_ws_humble_snapshot"
copy_tree "/tmp/calib_run" "$BUNDLE/calibration/calib_run_20260808"
copy_tree "/home/robot/.claude/projects/-home-robot----ego-vio-calib-kit/memory" "$BUNDLE/memory/claude_project_memory"
copy_tree "/home/robot/.codex/skills" "$BUNDLE/codex/user_skills"
copy_tree "$HANDOFF_SOURCE" "$BUNDLE/handoff"

if [[ -f /home/robot/.codex/RTK.md ]]; then
  "${RSYNC[@]}" -- /home/robot/.codex/RTK.md "$BUNDLE/codex/RTK.md"
fi

shopt -s nullglob
for runtime_dir in /tmp/ego_vio_vins_live_*; do
  if [[ -d "$runtime_dir" ]]; then
    copy_tree "$runtime_dir" "$BUNDLE/diagnostics/realtime_runs/$(basename "$runtime_dir")"
  fi
done
if [[ -d /tmp/orb_node_backup ]]; then
  copy_tree /tmp/orb_node_backup "$BUNDLE/diagnostics/orb_node_backup"
fi

declare -a EXTRA_FILES=(
  "/home/robot/桌面/KT-EX9-2J-2-F1产品技术协议_20260612(1).docx"
  "/home/robot/桌面/KT-EX9-2 客户协议20260716.docx"
  "/home/robot/semg.claude/KT-EX9-2_protocol.txt"
  "/home/robot/桌面/ego_vio_calib_kit.tar.gz"
  "/home/robot/桌面/ego_vio_calib_kit_fixed_20260802.tar.gz"
  "/home/robot/桌面/ego_vio_calib_kit_fixed_20260801.tar.gz"
)
for source_file in "${EXTRA_FILES[@]}"; do
  if [[ -f "$source_file" ]]; then
    "${RSYNC[@]}" -- "$source_file" "$BUNDLE/legacy_and_protocols/"
  fi
done
datasheet=$(find /home/robot/文档 -type f -name 'KT-EX9-2_DataSheet_En_20240109.pdf' -print -quit 2>/dev/null || true)
if [[ -n "$datasheet" ]]; then
  "${RSYNC[@]}" -- "$datasheet" "$BUNDLE/legacy_and_protocols/"
fi

snapshot_tree() {
  local label=$1
  local source_path=$2
  local relative_target=$3
  local files bytes symlinks
  files=$(find "$source_path" -type f -printf '.' | wc -c)
  bytes=$(find "$source_path" -type f -printf '%s\n' | awk '{sum += $1} END {printf "%.0f", sum + 0}')
  symlinks=$(find "$source_path" -type l -printf '.' | wc -c)
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$label" "$source_path" "$relative_target" "$files" "$bytes" "$symlinks"
}

{
  printf 'label\tsource\tdestination_relative\tregular_files\tregular_file_bytes\tsymlinks\n'
  snapshot_tree main_project /home/robot/ego_vio_humble projects/ego_vio_humble
  snapshot_tree calibration_project /home/robot/桌面/ego_vio_calib_kit projects/ego_vio_calib_kit
  snapshot_tree ros2_ws_humble /home/robot/ros2_ws ros2_ws_humble_snapshot
  snapshot_tree kalibr_raw /tmp/calib_run calibration/calib_run_20260808
  snapshot_tree project_memory /home/robot/.claude/projects/-home-robot----ego-vio-calib-kit/memory memory/claude_project_memory
  snapshot_tree codex_skills /home/robot/.codex/skills codex/user_skills
} > "$BUNDLE/metadata/SOURCE_TREE_SUMMARY.tsv"

if [[ -f /home/robot/ego_vio_humble/recordings/SESSION_CLASSIFICATION.tsv ]]; then
  cp -- /home/robot/ego_vio_humble/recordings/SESSION_CLASSIFICATION.tsv \
    "$BUNDLE/metadata/SESSION_CLASSIFICATION.tsv"
fi
if [[ -f /home/robot/ego_vio_humble/JAZZY_HANDOFF_20260816/LEGACY_QUARANTINE_MANIFEST.tsv ]]; then
  cp -- /home/robot/ego_vio_humble/JAZZY_HANDOFF_20260816/LEGACY_QUARANTINE_MANIFEST.tsv \
    "$BUNDLE/metadata/LEGACY_QUARANTINE_MANIFEST.tsv"
fi

{
  echo "captured_at=$(date --iso-8601=seconds)"
  echo "hostname=$(hostname)"
  echo "kernel=$(uname -srmo)"
  echo "os_release:"
  sed 's/^/  /' /etc/os-release
  echo "mount=$DEST_ROOT"
  echo "filesystem=$FS_TYPE"
  echo "bundle=$BUNDLE"
} > "$BUNDLE/metadata/HOST_AND_TRANSFER.txt"
apt-mark showmanual > "$BUNDLE/metadata/apt_manual_packages.txt" 2>&1 || true
dpkg-query -W -f='${binary:Package}\t${Version}\n' > "$BUNDLE/metadata/dpkg_versions.tsv" 2>&1 || true
python3 -m pip freeze > "$BUNDLE/metadata/python3_pip_freeze.txt" 2>&1 || true

snapshot_git() {
  local name=$1
  local repo=$2
  local out="$BUNDLE/metadata/git/$name"
  mkdir -p -- "$out"
  git -C "$repo" rev-parse HEAD > "$out/HEAD.txt"
  git -C "$repo" status --porcelain=v1 -uall > "$out/status.txt"
  git -C "$repo" log -1 --decorate=full --stat > "$out/latest_commit.txt"
  git -C "$repo" branch -avv > "$out/branches.txt"
  git -C "$repo" tag -n > "$out/tags.txt"
  git -C "$repo" remote -v > "$out/remotes.txt"
  git -C "$repo" diff --binary > "$out/working_tree.patch"
  git -C "$repo" diff --cached --binary > "$out/index.patch"
  git -C "$repo" ls-files --others --exclude-standard > "$out/untracked_files.txt"
  git -C "$repo" bundle create "$out/all_refs.bundle" --all
}
snapshot_git ego_vio_humble /home/robot/ego_vio_humble
snapshot_git ego_vio_calib_kit /home/robot/桌面/ego_vio_calib_kit
snapshot_git vins_fusion_ros2 /home/robot/ros2_ws/src/vins_fusion_ros2

echo "[哈希] 正在生成全部文件SHA-256；数据量大，这是最终不丢文件校验。"
(
  cd "$BUNDLE"
  find . -type f ! -name SHA256SUMS -print0 \
    | sort -z \
    | xargs -0 sha256sum
) > "$BUNDLE/SHA256SUMS"

sync
echo "[校验] 运行整包验证。"
"$BUNDLE/handoff/verify_bundle.sh" "$BUNDLE"
echo "[完成] 迁移包：$BUNDLE"
