#!/usr/bin/env bash
# Run the signed product VINS + adaptive-loop chain on one recorded session.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$ROOT/scripts/reset_ros_environment.sh"
SESSION="${1:-}"
if [[ -z "$SESSION" ]]; then
  echo "用法: $0 <录制会话目录> [输出目录] [额外test_vins_auto_loop参数]" >&2
  exit 2
fi
shift

if [[ $# -gt 0 && "$1" != --* ]]; then
  OUT_DIR="$1"
  shift
else
  OUT_DIR="$ROOT/slam_trajectories/$(date +%Y%m%d_%H%M%S)_product"
fi

ROS_SETUP="/opt/ros/humble/setup.bash"
BUILD_ROOT="$ROOT/.product_live_build"
HASH_MANIFEST="$BUILD_ROOT/product_live_hashes.env"
OFFLINE_SETUP="$BUILD_ROOT/loop_ws/install/setup.bash"
VINS_EXECUTABLE="$BUILD_ROOT/loop_ws/build/vins_fusion_ros2/vins_fusion_ros2_node"
VINS_LIBRARY="$BUILD_ROOT/loop_ws/build/vins_fusion_ros2/vins/libvins_lib.so"
LOOP_EXECUTABLE="$BUILD_ROOT/loop_ws/build/vins_fusion_ros2/loop_fusion/loop_fusion_node"
REPLAY_EXECUTABLE="$BUILD_ROOT/vins_ws/build/vins_fusion_ros2/db3_replay_cpp"
CONFIG="$ROOT/config/product_live_stm32/vins_config.yaml"

for required in "$ROS_SETUP" "$HASH_MANIFEST" "$OFFLINE_SETUP" "$VINS_EXECUTABLE" \
  "$VINS_LIBRARY" \
  "$LOOP_EXECUTABLE" "$REPLAY_EXECUTABLE" "$CONFIG"; do
  if [[ ! -f "$required" ]]; then
    echo "错误：正式产品后处理文件缺失：$required" >&2
    echo "请先运行：$ROOT/build_product_live.sh" >&2
    exit 5
  fi
done

manifest_value() {
  local key="$1"
  sed -n "s/^${key}=//p" "$HASH_MANIFEST" | head -n 1
}
verify_hash() {
  local key="$1" path="$2" expected actual
  expected="$(manifest_value "$key")"
  actual="$(sha256sum "$path" | awk '{print $1}')"
  if [[ ! "$expected" =~ ^[0-9a-f]{64}$ || "$actual" != "$expected" ]]; then
    echo "错误：产品产物哈希不匹配：$key" >&2
    exit 6
  fi
}
verify_hash PRODUCT_OFFLINE_VINS_SHA256 "$VINS_EXECUTABLE"
verify_hash PRODUCT_OFFLINE_VINS_LIBRARY_SHA256 "$VINS_LIBRARY"
verify_hash PRODUCT_LIVE_LOOP_SHA256 "$LOOP_EXECUTABLE"
verify_hash PRODUCT_LIVE_REPLAY_SHA256 "$REPLAY_EXECUTABLE"

set +u
source "$ROS_SETUP"
source "$OFFLINE_SETUP"
set -u

mkdir -p "$OUT_DIR"
exec /usr/bin/python3 "$ROOT/scripts/test_vins_auto_loop.py" \
  "$SESSION" \
  --out-dir "$OUT_DIR" \
  --config "$CONFIG" \
  --vins-executable "$VINS_EXECUTABLE" \
  --loop-executable "$LOOP_EXECUTABLE" \
  --replay-executable "$REPLAY_EXECUTABLE" \
  "$@"
