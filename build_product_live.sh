#!/usr/bin/env bash
# Build the current STM32 product-live VINS and adaptive-loop variants from source.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROS_DISTRO_NAME="${EGO_VIO_ROS_DISTRO:-humble}"
ROS_SETUP="/opt/ros/$ROS_DISTRO_NAME/setup.bash"
BUILD_ROOT="${EGO_VIO_PRODUCT_LIVE_BUILD_ROOT:-$ROOT/.product_live_build}"
VINS_WS="$BUILD_ROOT/vins_ws"
LOOP_WS="$BUILD_ROOT/loop_ws"

if [[ ! -f "$ROS_SETUP" ]]; then
  echo "错误：缺少 $ROS_SETUP；当前发布版要求 Ubuntu 22.04 + ROS 2 Humble。" >&2
  exit 2
fi

prepare_source_link() {
  local ws="$1" source="$2"
  local link="$ws/src/vins_fusion_ros2"
  mkdir -p "$ws/src"
  if [[ -L "$link" ]]; then
    if [[ "$(readlink -f "$link")" != "$(readlink -f "$source")" ]]; then
      echo "错误：已有源码链接指向其他目录：$link" >&2
      exit 3
    fi
  elif [[ -e "$link" ]]; then
    echo "错误：已有非链接路径，拒绝覆盖：$link" >&2
    exit 3
  else
    ln -s "$source" "$link"
  fi
}

prepare_source_link "$VINS_WS" "$ROOT/components/vins_fusion_ros2"
prepare_source_link "$LOOP_WS" "$ROOT/components/vins_fusion_ros2_product_loop"

set +u
source "$ROS_SETUP"
set -u

for ws in "$VINS_WS" "$LOOP_WS"; do
  (
    cd "$ws"
    colcon build --symlink-install --packages-select vins_fusion_ros2 \
      --cmake-args -DCMAKE_BUILD_TYPE=Release
  )
done

VINS_EXECUTABLE="$VINS_WS/build/vins_fusion_ros2/vins_fusion_ros2_node"
VINS_LIBRARY="$VINS_WS/build/vins_fusion_ros2/vins/libvins_lib.so"
LOOP_EXECUTABLE="$LOOP_WS/build/vins_fusion_ros2/loop_fusion/loop_fusion_node"
for artifact in "$VINS_EXECUTABLE" "$VINS_LIBRARY" "$LOOP_EXECUTABLE"; do
  if [[ ! -x "$artifact" ]]; then
    echo "错误：构建产物缺失或不可执行：$artifact" >&2
    exit 4
  fi
done

HASH_MANIFEST="$BUILD_ROOT/product_live_hashes.env"
{
  printf 'PRODUCT_LIVE_VINS_SHA256=%s\n' "$(sha256sum "$VINS_EXECUTABLE" | awk '{print $1}')"
  printf 'PRODUCT_LIVE_VINS_LIBRARY_SHA256=%s\n' "$(sha256sum "$VINS_LIBRARY" | awk '{print $1}')"
  printf 'PRODUCT_LIVE_LOOP_SHA256=%s\n' "$(sha256sum "$LOOP_EXECUTABLE" | awk '{print $1}')"
} > "$HASH_MANIFEST"

echo "PASS：product-live 两套源码构建完成。"
echo "构建目录：$BUILD_ROOT"
echo "运行时哈希：$HASH_MANIFEST"
echo "下一步：./run_vins_realtime.sh product-live"
