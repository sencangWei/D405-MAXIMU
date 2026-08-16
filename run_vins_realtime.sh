#!/usr/bin/env bash
# D405双IR 30fps + 外置IMU 400Hz + VINS-Fusion + Rerun实时轨迹。
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEVICE_CONFIG="$ROOT/config/devices_vins_fusion_live.yaml"
RSUSB_PYTHON="$ROOT/.deps/librealsense-rsusb-2.58.2/python"
RUN_DIR="/tmp/ego_vio_vins_live_$(date +%Y%m%d_%H%M%S)"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
PYTHON_EXT_SUFFIX="$("$PYTHON_BIN" -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX"))')"
RSUSB_MODULE="$RSUSB_PYTHON/pyrealsense2${PYTHON_EXT_SUFFIX}"

if [[ -n "${EGO_VIO_ROS_DISTRO:-}" ]]; then
  ROS_DISTRO_NAME="$EGO_VIO_ROS_DISTRO"
elif [[ -f /opt/ros/jazzy/setup.bash ]]; then
  ROS_DISTRO_NAME="jazzy"
elif [[ -f /opt/ros/humble/setup.bash ]]; then
  ROS_DISTRO_NAME="humble"
else
  echo "错误：未发现/opt/ros/jazzy或/opt/ros/humble。" >&2
  exit 4
fi
ROS_SETUP="/opt/ros/$ROS_DISTRO_NAME/setup.bash"

if [[ -n "${EGO_VIO_ROS_WS:-}" ]]; then
  ROS_WS="$EGO_VIO_ROS_WS"
elif [[ -f "$HOME/ego_vio_jazzy_ws/install/setup.bash" ]]; then
  ROS_WS="$HOME/ego_vio_jazzy_ws"
else
  ROS_WS="/home/robot/ros2_ws"
fi
WS_SETUP="$ROS_WS/install/setup.bash"
BASE_CONFIG="${EGO_VIO_VINS_CONFIG:-$ROS_WS/src/vins_fusion_ros2/config/d405_stereo_imu/d405_stereo_imu_config.yaml}"
for required_file in "$ROS_SETUP" "$WS_SETUP" "$BASE_CONFIG" "$RSUSB_MODULE"; do
  if [[ ! -f "$required_file" ]]; then
    echo "错误：部署文件缺失：$required_file" >&2
    exit 5
  fi
done

MODE="${1:-stable}"
EXTRA_RUNTIME_ARGS=()
mkdir -p "$RUN_DIR"
case "$MODE" in
  stable)
    VINS_CONFIG="$BASE_CONFIG"
    ;;
  level-candidate)
    echo "错误：level-candidate已由跨会话A/B否决，只保留为离线证据，禁止实时加载。" >&2
    echo "请使用: $0 stable" >&2
    exit 3
    ;;
  smoke)
    VINS_CONFIG="$BASE_CONFIG"
    EXTRA_RUNTIME_ARGS+=(--no-viz --duration-s 15)
    ;;
  *)
    echo "用法: $0 [stable|smoke]" >&2
    exit 2
    ;;
esac

set +u
source "$ROS_SETUP"
source "$WS_SETUP"
set -u

cleanup() {
  if [[ -n "${RECT_REC_PID:-}" ]]; then
    kill "$RECT_REC_PID" 2>/dev/null || true
    wait "$RECT_REC_PID" 2>/dev/null || true
  fi
  if [[ -n "${RAW_REC_PID:-}" ]]; then
    kill "$RAW_REC_PID" 2>/dev/null || true
    wait "$RAW_REC_PID" 2>/dev/null || true
  fi
  if [[ -n "${LOOP_PID:-}" ]]; then
    kill "$LOOP_PID" 2>/dev/null || true
    wait "$LOOP_PID" 2>/dev/null || true
  fi
  if [[ -n "${VINS_PID:-}" ]]; then
    kill "$VINS_PID" 2>/dev/null || true
    wait "$VINS_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

pkill -9 -f vins_fusion_ros2_node 2>/dev/null || true
pkill -9 -f loop_fusion_node 2>/dev/null || true
pkill -9 -f 'scripts/run_realtime.py.*vins_fusion_ros2' 2>/dev/null || true

echo "=== 实时VINS-Fusion ==="
echo "双IR: 1280x720@30fps  IMU: 400Hz"
echo "模式: $MODE"
echo "ROS: $ROS_DISTRO_NAME  工作区: $ROS_WS"
echo "VINS配置: $VINS_CONFIG"
echo "日志: $RUN_DIR"
echo "启动后请保持设备静止5秒；Rerun显示 /odometry_rect 自动回环校正轨迹。"

ros2 run vins_fusion_ros2 vins_fusion_ros2_node --ros-args \
  -p use_sim_time:=false -p config_file:="$VINS_CONFIG" \
  > "$RUN_DIR/vins.log" 2>&1 &
VINS_PID=$!

ros2 run vins_fusion_ros2 loop_fusion_node "$VINS_CONFIG" \
  > "$RUN_DIR/loop_fusion.log" 2>&1 &
LOOP_PID=$!

sleep 6
if ! kill -0 "$VINS_PID" 2>/dev/null || ! kill -0 "$LOOP_PID" 2>/dev/null; then
  echo "VINS或自动回环节点启动失败：$RUN_DIR" >&2
  exit 1
fi

"$PYTHON_BIN" "$ROOT/scripts/record_odom_csv.py" \
  --topic /odometry --out "$RUN_DIR/odometry_raw.csv" \
  > "$RUN_DIR/odometry_raw_recorder.log" 2>&1 &
RAW_REC_PID=$!
"$PYTHON_BIN" "$ROOT/scripts/record_odom_csv.py" \
  --topic /odometry_rect --out "$RUN_DIR/odometry_rect.csv" \
  > "$RUN_DIR/odometry_rect_recorder.log" 2>&1 &
RECT_REC_PID=$!
echo "三轴诊断轨迹: $RUN_DIR/odometry_raw.csv 和 odometry_rect.csv"

PYTHONPATH="$RSUSB_PYTHON:$ROOT${PYTHONPATH:+:$PYTHONPATH}" \
  "$PYTHON_BIN" "$ROOT/scripts/run_realtime.py" \
    --config "$DEVICE_CONFIG" --backend vins_fusion_ros2 --no-record \
    "${EXTRA_RUNTIME_ARGS[@]}" 2>&1 | tee "$RUN_DIR/runtime.log"
