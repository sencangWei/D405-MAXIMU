#!/usr/bin/env bash
# Run the frozen product SLAM binaries with one explicitly selected config.
set -euo pipefail

ROOT=/home/robot/ego_vio_humble
SESSION="${1:-}"
OUT_DIR="${2:-}"
CONFIG="${3:-}"
if [[ -z "$SESSION" || -z "$OUT_DIR" || -z "$CONFIG" ]]; then
  echo "用法: $0 <录制会话目录> <输出目录> <VINS配置>" >&2
  exit 2
fi

ROS_SETUP=/opt/ros/humble/setup.bash
BUILD_ROOT="$ROOT/.product_live_build"
HASH_MANIFEST="$BUILD_ROOT/product_live_hashes.env"
OFFLINE_SETUP="$BUILD_ROOT/loop_ws/install/setup.bash"
VINS_EXECUTABLE="$BUILD_ROOT/loop_ws/build/vins_fusion_ros2/vins_fusion_ros2_node"
VINS_LIBRARY="$BUILD_ROOT/loop_ws/build/vins_fusion_ros2/vins/libvins_lib.so"
LOOP_EXECUTABLE="$BUILD_ROOT/loop_ws/build/vins_fusion_ros2/loop_fusion/loop_fusion_node"
# This must be distinct from test_vins_auto_loop.py's vins_ws default path.
# The runner then executes this exact, verified file directly instead of
# silently switching to `ros2 run` through an overlay.
REPLAY_EXECUTABLE="$BUILD_ROOT/loop_ws/build/vins_fusion_ros2/db3_replay_cpp"
RESET_SCRIPT="$ROOT/scripts/reset_ros_environment.sh"
RUNNER="$ROOT/scripts/test_vins_auto_loop.py"
PARENT_WRAPPER="$ROOT/run_slam_postprocess.sh"

for required in "$ROS_SETUP" "$HASH_MANIFEST" "$OFFLINE_SETUP" \
  "$VINS_EXECUTABLE" "$VINS_LIBRARY" "$LOOP_EXECUTABLE" \
  "$REPLAY_EXECUTABLE" "$CONFIG" "$RESET_SCRIPT" "$RUNNER" \
  "$PARENT_WRAPPER"; do
  if [[ ! -f "$required" ]]; then
    echo "错误：候选A/B后处理文件缺失：$required" >&2
    exit 5
  fi
done

verify_literal_hash() {
  local expected="$1" path="$2" label="$3" actual
  actual="$(sha256sum "$path" | awk '{print $1}')"
  if [[ "$actual" != "$expected" ]]; then
    echo "错误：冻结产品文件哈希不匹配：$label" >&2
    exit 6
  fi
}

verify_literal_hash \
  f4aae2ca6400906b3f996e21e8e0acbb11f35533a90bb1a895af59b251809a5b \
  "$PARENT_WRAPPER" "parent postprocess wrapper"
verify_literal_hash \
  8ad3673817920855a9e6debb2cb691587bc7699ddb04c5d778bd4f4cc6622d16 \
  "$RESET_SCRIPT" "ROS environment reset"
verify_literal_hash \
  9e5cdec4bbfdd189b94065c849b9f36f92ec5c5931fbdd4f79819d3de3cc4f0f \
  "$RUNNER" "offline SLAM runner"
verify_literal_hash \
  f789d3419db91ebe7cb5ea7b276910eac1199d41361ddfb75edf179a5520768e \
  "$ROOT/scripts/slam_benchmark_environment.py" "benchmark environment module"
verify_literal_hash \
  15eb4de09f0482b87699fcf62e4b9ae2f15596669f4f7fc77d6d3a3a76bf5509 \
  "$ROOT/scripts/slam_run_health.py" "SLAM health module"
verify_literal_hash \
  96b125d1719379548ead82183aefd4f500fadcac53fe94ed1220de7d6b95ce7a \
  "$ROOT/scripts/slam_runtime_watchdog.py" "runtime watchdog module"
verify_literal_hash \
  5fd6d507bb599e409b6646f3cc51695cee6506e86f04f41b810ccf6dcb77c369 \
  "$HASH_MANIFEST" "product binary hash manifest"

BASELINE_CONFIG="$ROOT/config/product_live_stm32/vins_config.yaml"
if [[ "$(realpath -e "$CONFIG")" == "$BASELINE_CONFIG" ]]; then
  verify_literal_hash \
    c251de961f86e973047781b65b9878855540f36dee936620dd5a37c6c08c97f5 \
    "$BASELINE_CONFIG" "baseline VINS config"
  verify_literal_hash \
    cc44a4a4df6c1d0f0926fc5c4ec248d9bb2e7ff52c729edc3468f9ea5feb9ca8 \
    "$ROOT/config/product_live_stm32/left.yaml" "baseline left camera"
  verify_literal_hash \
    cc44a4a4df6c1d0f0926fc5c4ec248d9bb2e7ff52c729edc3468f9ea5feb9ca8 \
    "$ROOT/config/product_live_stm32/right.yaml" "baseline right camera"
fi

SESSION="$(realpath -e "$SESSION")"
CONFIG="$(realpath -e "$CONFIG")"
SESSION_PARENT="$(realpath -e "$(dirname "$SESSION")")"
OUT_PARENT="$(realpath -e "$(dirname "$OUT_DIR")")"
if [[ -L "$SESSION" ]] || { \
  [[ "$SESSION_PARENT" != /data/recordings ]] \
  && [[ "$SESSION_PARENT" != /data/candidate_ab/recordings ]]; }; then
  echo "错误：后处理输入不在授权的直接子目录" >&2
  exit 7
fi
if [[ -e "$OUT_DIR" ]] || { \
  [[ "$OUT_PARENT" != /data/slam_results ]] \
  && [[ "$OUT_PARENT" != /data/candidate_ab/slam_results ]]; }; then
  echo "错误：后处理输出越界或已存在" >&2
  exit 7
fi

source "$RESET_SCRIPT"

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
verify_literal_hash \
  2a869141fc9dd46ce23c92fe06300d5c222f46ee9019245365ba94d0b7964973 \
  "$REPLAY_EXECUTABLE" "direct loop-workspace DB3 replay executable"

set +u
source "$ROS_SETUP"
source "$OFFLINE_SETUP"
set -u

mkdir "$OUT_DIR"
exec /usr/bin/python3 "$RUNNER" \
  "$SESSION" \
  --out-dir "$OUT_DIR" \
  --config "$CONFIG" \
  --vins-executable "$VINS_EXECUTABLE" \
  --loop-executable "$LOOP_EXECUTABLE" \
  --replay-executable "$REPLAY_EXECUTABLE"
