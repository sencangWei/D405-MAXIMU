#!/usr/bin/env bash
# C2 驱动：幸存臂 × 跨好/差的格，全链对照。串行（单 GPU）。
#
# ⚠ 别再用 `| sed "s/^/[$cell] /"` 注前缀：$cell 含 `/`，会把 s 命令提前截断，
#   sed 当场退出，run_c2.sh 写 stdout 时吃 SIGPIPE **整条静默死掉**（零产物，
#   日志里只剩 `sed: "s"的未知选项`）。改成每格各写一份日志文件。
set -uo pipefail
D="$(cd -- "$(dirname -- "$0")" && pwd)"
ROOT=/home/robot/ego_vio_humble
ARM="${1:-match_wide}"
shift || true
CELLS=("$@")
[[ ${#CELLS[@]} -gt 0 ]] || CELLS=(
  "20260915_batch5_four_videos/group4"
  "20260915_collective_batch4/group1"
)
for cell in "${CELLS[@]}"; do
    log="$D/c2_${ARM}_$(echo "$cell" | tr / _).log"
    echo "===== [$cell] arm=$ARM -> $log"
    bash "$D/run_c2.sh" "$cell" "$ARM" > "$log" 2>&1
    rc=$?
    echo "----- [$cell] rc=$rc"
    tail -14 "$log"
done
echo "[DONE] C2 arm=$ARM 完成"