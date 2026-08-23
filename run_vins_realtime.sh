#!/usr/bin/env bash
# D405双IR 30fps + 外置IMU 400Hz + VINS-Fusion + Rerun实时轨迹。
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEVICE_CONFIG="$ROOT/config/devices_vins_fusion_live.yaml"
PRODUCT_LIVE_DEVICE_CONFIG="${EGO_VIO_PRODUCT_LIVE_DEVICE_CONFIG:-$ROOT/config/devices_product_live_stm32.yaml}"
PRODUCT_LIVE_CONFIG="${EGO_VIO_PRODUCT_LIVE_CONFIG:-$ROOT/config/product_live_stm32/vins_config.yaml}"
PRODUCT_LIVE_Z_DEVICE_CONFIG="$ROOT/config/devices_product_live_z_candidate.yaml"
PRODUCT_LIVE_Z_CONFIG="$ROOT/config/product_live_z_candidate/vins_config.yaml"
PRODUCT_LIVE_Z_IMU_CALIBRATION="$ROOT/config/product_live_z_candidate/imu_accel_092447_runtime.yaml"
RSUSB_PYTHON="$ROOT/.deps/librealsense-rsusb-2.58.2/python"
RUN_DIR="${EGO_VIO_RUN_DIR:-/tmp/ego_vio_vins_live_$(date +%Y%m%d_%H%M%S)}"
PRODUCT_LIVE_CALIBRATION_LABEL="${EGO_VIO_PRODUCT_CALIBRATION_LABEL:-assembled STM32 consensus td=-0.009312 s}"
DISABLE_VIEWER="${EGO_VIO_DISABLE_VIEWER:-0}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
MODE="${1:-stable}"
if [[ $# -gt 0 ]]; then
  shift
fi
CAPTURE_ARGS=("$@")

# Serialize every live entrypoint for the whole shell lifetime.  The lock is
# acquired before deployment checks or ROS startup so two near-simultaneous
# invocations cannot both pass the stale-process preflight.
LIVE_LOCK_FILE="${EGO_VIO_LIVE_LOCK_FILE:-/tmp/ego_vio_vins_realtime_${UID}.lock}"
LIVE_LOCK_FD=9
exec 9>"$LIVE_LOCK_FILE"
if ! flock -n "$LIVE_LOCK_FD"; then
  echo "错误：已有实时VINS任务持有启动锁，拒绝并发启动：$LIVE_LOCK_FILE" >&2
  exit 8
fi

is_product_live_mode() {
  [[ "$MODE" == "product-live" || "$MODE" == "product-live-z-candidate" ]]
}
FROZEN_LOOP_EXECUTABLE="${EGO_VIO_FROZEN_LOOP_EXECUTABLE:-$ROOT/frozen_chain_a3a38b8/bin/lfn_product_origin_ready_v7}"
FROZEN_LOOP_BASENAME="$(basename -- "$FROZEN_LOOP_EXECUTABLE")"
FROZEN_LOOP_BASENAME_ERE="$(printf '%s' "$FROZEN_LOOP_BASENAME" | sed 's/[][\\.^$*+?(){}|]/\\&/g')"
FROZEN_LOOP_SHA256="8148cc99945e56c38151254da7aae38269892efb5d6786c6b003e97e8d550001"
FROZEN_BUILD_ROOT="$ROOT/frozen_builds/20260817_191957"
FROZEN_SETUP="$FROZEN_BUILD_ROOT/install/setup.bash"
PRODUCT_LIVE_BUILD_ROOT="${EGO_VIO_PRODUCT_LIVE_BUILD_ROOT:-$ROOT/.product_live_build}"
LEGACY_PRODUCT_LIVE_VINS_WS="$ROOT/.planning/frozen_vs_adaptive_depth_ab_20260823/realtime_components_ws"
LEGACY_PRODUCT_LIVE_LOOP_WS="$ROOT/.planning/slam_60s_stm32_calib_20260822/product_loop_ws"
if [[ -z "${EGO_VIO_PRODUCT_LIVE_BUILD_ROOT:-}" \
      && -z "${EGO_VIO_PRODUCT_LIVE_VINS_WS:-}" \
      && -z "${EGO_VIO_PRODUCT_LIVE_LOOP_WS:-}" \
      && -z "${EGO_VIO_PRODUCT_LIVE_HASH_MANIFEST:-}" \
      && ! -f "$PRODUCT_LIVE_BUILD_ROOT/product_live_hashes.env" \
      && -f "$LEGACY_PRODUCT_LIVE_VINS_WS/install/setup.bash" \
      && -f "$LEGACY_PRODUCT_LIVE_LOOP_WS/install/setup.bash" ]]; then
  # Preserve the exact binaries used by the 2026-08-23 local HIL run. Fresh
  # clones do not have this path and use build_product_live.sh instead.
  PRODUCT_LIVE_VINS_WS="$LEGACY_PRODUCT_LIVE_VINS_WS"
  PRODUCT_LIVE_LOOP_WS="$LEGACY_PRODUCT_LIVE_LOOP_WS"
  PRODUCT_LIVE_HASH_MANIFEST=""
else
  PRODUCT_LIVE_VINS_WS="${EGO_VIO_PRODUCT_LIVE_VINS_WS:-$PRODUCT_LIVE_BUILD_ROOT/vins_ws}"
  PRODUCT_LIVE_LOOP_WS="${EGO_VIO_PRODUCT_LIVE_LOOP_WS:-$PRODUCT_LIVE_BUILD_ROOT/loop_ws}"
  PRODUCT_LIVE_HASH_MANIFEST="${EGO_VIO_PRODUCT_LIVE_HASH_MANIFEST:-$PRODUCT_LIVE_BUILD_ROOT/product_live_hashes.env}"
fi
PRODUCT_LIVE_VINS_SETUP="$PRODUCT_LIVE_VINS_WS/install/setup.bash"
PRODUCT_LIVE_LOOP_SETUP="$PRODUCT_LIVE_LOOP_WS/install/setup.bash"
PRODUCT_LIVE_VINS_EXECUTABLE="$PRODUCT_LIVE_VINS_WS/build/vins_fusion_ros2/vins_fusion_ros2_node"
PRODUCT_LIVE_VINS_LIBRARY="$PRODUCT_LIVE_VINS_WS/build/vins_fusion_ros2/vins/libvins_lib.so"
PRODUCT_LIVE_LOOP_EXECUTABLE="$PRODUCT_LIVE_LOOP_WS/build/vins_fusion_ros2/loop_fusion/loop_fusion_node"
PRODUCT_LIVE_VINS_SHA256="dbb0b8d78aa62c6f93577f12639702e37315ff62d794ee356fcb7a6c3e7fd956"
PRODUCT_LIVE_VINS_LIBRARY_SHA256="186c7db6c224469ab18309cdc24904c2e42c2ba2a3a1a9b5c1e1e22c497c2f80"
PRODUCT_LIVE_LOOP_SHA256="ca6ca5ab02ca10d3ddce2bfb77448ba0fbb6fe48ce215e7f5ae77f08defb70b4"
if [[ -n "$PRODUCT_LIVE_HASH_MANIFEST" && -f "$PRODUCT_LIVE_HASH_MANIFEST" ]]; then
  manifest_value() {
    local key="$1" value
    value="$(sed -n "s/^${key}=//p" "$PRODUCT_LIVE_HASH_MANIFEST" | head -n 1)"
    if [[ ! "$value" =~ ^[0-9a-f]{64}$ ]]; then
      echo "错误：哈希清单字段无效：$key ($PRODUCT_LIVE_HASH_MANIFEST)" >&2
      exit 6
    fi
    printf '%s' "$value"
  }
  PRODUCT_LIVE_VINS_SHA256="$(manifest_value PRODUCT_LIVE_VINS_SHA256)"
  PRODUCT_LIVE_VINS_LIBRARY_SHA256="$(manifest_value PRODUCT_LIVE_VINS_LIBRARY_SHA256)"
  PRODUCT_LIVE_LOOP_SHA256="$(manifest_value PRODUCT_LIVE_LOOP_SHA256)"
fi
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
required_files=("$ROS_SETUP" "$RSUSB_MODULE")
if is_product_live_mode; then
  required_files+=(
    "$PRODUCT_LIVE_VINS_SETUP"
    "$PRODUCT_LIVE_LOOP_SETUP"
    "$PRODUCT_LIVE_VINS_EXECUTABLE"
    "$PRODUCT_LIVE_VINS_LIBRARY"
    "$PRODUCT_LIVE_LOOP_EXECUTABLE"
  )
  if [[ "$MODE" == "product-live-z-candidate" ]]; then
    required_files+=(
      "$PRODUCT_LIVE_Z_DEVICE_CONFIG"
      "$PRODUCT_LIVE_Z_CONFIG"
      "$PRODUCT_LIVE_Z_IMU_CALIBRATION"
    )
  else
    required_files+=("$PRODUCT_LIVE_DEVICE_CONFIG" "$PRODUCT_LIVE_CONFIG")
  fi
elif [[ "$MODE" != "frozen" && "$MODE" != "frozen-record" ]]; then
  required_files+=("$WS_SETUP" "$BASE_CONFIG")
else
  required_files+=("$FROZEN_SETUP")
fi
for required_file in "${required_files[@]}"; do
  if [[ ! -f "$required_file" ]]; then
    echo "错误：部署文件缺失：$required_file" >&2
    exit 5
  fi
done

EXTRA_RUNTIME_ARGS=()
mkdir -p "$RUN_DIR"
case "$MODE" in
  product-live|product-live-z-candidate)
    EXTRA_RUNTIME_ARGS+=(--no-viz)
    EXTRA_RUNTIME_ARGS+=("${CAPTURE_ARGS[@]}")
    if [[ "$MODE" == "product-live-z-candidate" ]]; then
      DEVICE_CONFIG="$PRODUCT_LIVE_Z_DEVICE_CONFIG"
      VINS_CONFIG="$PRODUCT_LIVE_Z_CONFIG"
      mkdir -p /tmp/ego_vio_product_live_z_candidate_output/pose_graph
    else
      DEVICE_CONFIG="$PRODUCT_LIVE_DEVICE_CONFIG"
      VINS_CONFIG="$PRODUCT_LIVE_CONFIG"
      mkdir -p /tmp/ego_vio_product_live_output/pose_graph
    fi
    actual_product_vins_sha256="$(sha256sum "$PRODUCT_LIVE_VINS_EXECUTABLE" | awk '{print $1}')"
    actual_product_vins_library_sha256="$(sha256sum "$PRODUCT_LIVE_VINS_LIBRARY" | awk '{print $1}')"
    actual_product_loop_sha256="$(sha256sum "$PRODUCT_LIVE_LOOP_EXECUTABLE" | awk '{print $1}')"
    if [[ "$actual_product_vins_sha256" != "$PRODUCT_LIVE_VINS_SHA256" ]]; then
      echo "错误：product-live VINS二进制哈希不匹配。" >&2
      echo "期望：$PRODUCT_LIVE_VINS_SHA256" >&2
      echo "实际：$actual_product_vins_sha256" >&2
      exit 6
    fi
    if [[ "$actual_product_vins_library_sha256" != "$PRODUCT_LIVE_VINS_LIBRARY_SHA256" ]]; then
      echo "错误：product-live VINS核心库哈希不匹配。" >&2
      echo "期望：$PRODUCT_LIVE_VINS_LIBRARY_SHA256" >&2
      echo "实际：$actual_product_vins_library_sha256" >&2
      exit 6
    fi
    if [[ "$actual_product_loop_sha256" != "$PRODUCT_LIVE_LOOP_SHA256" ]]; then
      echo "错误：product-live自适应回环二进制哈希不匹配。" >&2
      echo "期望：$PRODUCT_LIVE_LOOP_SHA256" >&2
      echo "实际：$actual_product_loop_sha256" >&2
      exit 6
    fi
    ;;
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
  frozen|frozen-record)
    VINS_CONFIG="$FROZEN_BUILD_ROOT/install/vins_fusion_ros2/share/vins_fusion_ros2/config/d405_stereo_imu/d405_stereo_imu_config.yaml"
    if [[ ! -f "$VINS_CONFIG" ]]; then
      echo "错误：冻结链配置缺失：$VINS_CONFIG" >&2
      exit 6
    fi
    if [[ ! -x "$FROZEN_LOOP_EXECUTABLE" ]]; then
      echo "错误：冻结回环可执行文件不存在或不可执行：$FROZEN_LOOP_EXECUTABLE" >&2
      exit 6
    fi
    actual_frozen_loop_sha256="$(sha256sum "$FROZEN_LOOP_EXECUTABLE" | awk '{print $1}')"
    if [[ "$actual_frozen_loop_sha256" != "$FROZEN_LOOP_SHA256" ]]; then
      echo "错误：冻结回环二进制哈希不匹配。" >&2
      echo "期望：$FROZEN_LOOP_SHA256" >&2
      echo "实际：$actual_frozen_loop_sha256" >&2
      exit 6
    fi
    ;;
  *)
    echo "用法: $0 [product-live|product-live-z-candidate|stable|frozen|frozen-record|smoke] [采集参数]" >&2
    exit 2
    ;;
esac

set +u
source "$ROS_SETUP"
if is_product_live_mode; then
  source "$PRODUCT_LIVE_VINS_SETUP"
  source "$PRODUCT_LIVE_LOOP_SETUP"
elif [[ "$MODE" != "frozen" && "$MODE" != "frozen-record" ]]; then
  source "$WS_SETUP"
else
  source "$FROZEN_SETUP"
  export LD_LIBRARY_PATH="$FROZEN_BUILD_ROOT/build/vins_fusion_ros2:$FROZEN_BUILD_ROOT/build/vins_fusion_ros2/vins${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
set -u

# Never start a second live pipeline on the same ROS topics.  A stale node
# from an abnormal previous exit can otherwise mix odometry/IMU streams and
# look like a numerical VINS failure.  Fail closed so unrelated ROS jobs are
# never killed implicitly.
CONFLICTING_PROCESSES="$(
  {
    pgrep -af 'vins_fusion_ros2_node|loop_fusion_node' \
      || true
    pgrep -af '^([^[:space:]]*/)?python[^[:space:]]*([[:space:]]+[^[:space:]]+)*[[:space:]]+[^[:space:]]*/run_realtime\.py([[:space:]].*)--backend([=[:space:]])+vins_fusion_ros2([[:space:]]|$)' \
      || true
    # The historical frozen loop has a different executable name and may be
    # the only survivor after an abnormal frozen/frozen-record shutdown.
    pgrep -af -- "^([^[:space:]]*/)?${FROZEN_LOOP_BASENAME_ERE}([[:space:]]|$)" \
      || true
    # frozen-record execs this Python producer.  Detect it separately because
    # it can be the sole process still holding the D405 and IMU serial port.
    pgrep -af '^([^[:space:]]*/)?python[^[:space:]]*([[:space:]]+[^[:space:]]+)*[[:space:]]+[^[:space:]]*/capture_d405_720p_rgb_stereo_ir\.py([[:space:]].*)--publish-vins([[:space:]]|$)' \
      || true
    # Product mode launches Rerun as an independent subscriber.  Refuse a
    # duplicate stale viewer so two windows cannot make the operator mistake
    # an old trajectory for the current run.
    pgrep -af '^([^[:space:]]*/)?python[^[:space:]]*([[:space:]]+[^[:space:]]+)*[[:space:]]+[^[:space:]]*/rerun_vio_viewer\.py([[:space:]]|$)' \
      || true
    # The managed viewer owns port 9876.  A detached server from an older
    # build must never receive a new run or retain gigabytes of stale data.
    pgrep -af 'rerun.*--port(=|[[:space:]])9876([[:space:]]|$)' \
      || true
  } | sort -u
)"
if [[ -n "$CONFLICTING_PROCESSES" ]]; then
  echo "错误：检测到残留VINS/回环进程，为避免污染实时轨迹已拒绝启动。" >&2
  echo "$CONFLICTING_PROCESSES" >&2
  echo "请先正常停止上一次任务，确认进程退出后重试；本脚本不会自动pkill。" >&2
  exit 8
fi

cleanup() {
  local pid
  local child_pids=(
    "${VIEWER_PID:-}"
    "${CAPTURE_PID:-}"
    "${RUNTIME_PID:-}"
    "${RECT_REC_PID:-}"
    "${WATCHDOG_PID:-}"
    "${RAW_REC_PID:-}"
    "${LOOP_PID:-}"
    "${VINS_PID:-}"
  )

  # Every background child below starts in its own process group.  Signal the
  # whole group so wrappers such as `ros2 run` cannot leave a node orphaned.
  for pid in "${child_pids[@]}"; do
    if [[ -n "$pid" ]] && kill -0 -- "-$pid" 2>/dev/null; then
      kill -- "-$pid" 2>/dev/null || true
    fi
  done

  # Give ROS nodes two seconds for an orderly shutdown, then guarantee that
  # no camera/VINS process is left behind after an exception in the viewer.
  for _ in {1..20}; do
    local any_alive=0
    for pid in "${child_pids[@]}"; do
      if [[ -n "$pid" ]] && kill -0 -- "-$pid" 2>/dev/null; then
        any_alive=1
      fi
    done
    [[ "$any_alive" -eq 0 ]] && break
    sleep 0.1
  done
  for pid in "${child_pids[@]}"; do
    if [[ -n "$pid" ]] && kill -0 -- "-$pid" 2>/dev/null; then
      kill -KILL -- "-$pid" 2>/dev/null || true
    fi
    if [[ -n "$pid" ]]; then
      wait "$pid" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT INT TERM

wait_with_viewer_supervision() {
  local owner_pid="$1"
  while kill -0 "$owner_pid" 2>/dev/null; do
    if [[ -n "${VIEWER_PID:-}" ]] && ! kill -0 "$VIEWER_PID" 2>/dev/null; then
      echo "错误：Rerun可视化进程异常退出；停止实时主链，避免无画面运行。" >&2
      echo "日志: $RUN_DIR/rerun.log" >&2
      tail -n 20 "$RUN_DIR/rerun.log" >&2 || true
      return 1
    fi
    sleep 0.5
  done
  wait "$owner_pid"
}

if [[ "$MODE" != "smoke" && "$DISABLE_VIEWER" != "1" ]]; then
  if ! "$PYTHON_BIN" -c 'import rerun, rerun.blueprint' >/dev/null 2>&1; then
    echo "错误：当前Python缺少兼容的rerun-sdk；请安装项目requirements.txt后重试。" >&2
    exit 7
  fi
fi

echo "=== 实时VINS-Fusion ==="
echo "双IR: 1280x720@30fps  IMU: 400Hz"
echo "模式: $MODE"
echo "ROS: $ROS_DISTRO_NAME  工作区: $ROS_WS"
echo "VINS配置: $VINS_CONFIG"
echo "日志: $RUN_DIR"
if is_product_live_mode; then
  echo "STM32协议: stm32_combined_v1 (63字节，禁止自动降级)"
  echo "product-live VINS SHA256: $PRODUCT_LIVE_VINS_SHA256"
  echo "product-live VINS核心库 SHA256: $PRODUCT_LIVE_VINS_LIBRARY_SHA256"
  echo "VINS节拍: 相机/特征30Hz，旧机稳定策略后端约15Hz"
  echo "动态近景失效保护: 单步>0.05m即锁存失败，冻结轨迹并阻断回环直至重启"
  echo "自适应回环 SHA256: $PRODUCT_LIVE_LOOP_SHA256"
  echo "标定: $PRODUCT_LIVE_CALIBRATION_LABEL"
fi
if [[ "$MODE" == "product-live-z-candidate" ]]; then
  echo "Z候选: 092447 加速度内参 + acc_n=0.1"
  echo "状态: 诊断候选，未签发，不覆盖 product-live"
fi
if is_product_live_mode && [[ "$DISABLE_VIEWER" != "1" ]]; then
  setsid env LD_LIBRARY_PATH="$PRODUCT_LIVE_VINS_WS/build/vins_fusion_ros2:$PRODUCT_LIVE_VINS_WS/build/vins_fusion_ros2/vins${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    "$PRODUCT_LIVE_VINS_EXECUTABLE" \
    --ros-args -p use_sim_time:=false -p config_file:="$VINS_CONFIG" \
    9>&- > "$RUN_DIR/vins.log" 2>&1 &
  VINS_PID=$!
elif [[ "$MODE" == "frozen" || "$MODE" == "frozen-record" ]]; then
  echo "冻结回环: $FROZEN_LOOP_EXECUTABLE"
  echo "冻结回环SHA256: $FROZEN_LOOP_SHA256"
  setsid "$FROZEN_BUILD_ROOT/build/vins_fusion_ros2/vins_fusion_ros2_node" \
    --ros-args -p use_sim_time:=false -p config_file:="$VINS_CONFIG" \
    9>&- > "$RUN_DIR/vins.log" 2>&1 &
  VINS_PID=$!
else
  setsid ros2 run vins_fusion_ros2 vins_fusion_ros2_node --ros-args \
    -p use_sim_time:=false -p config_file:="$VINS_CONFIG" \
    9>&- > "$RUN_DIR/vins.log" 2>&1 &
  VINS_PID=$!
fi

echo "启动后请保持设备静止5秒；Rerun显示 /odometry_rect 回环校正后端轨迹。"

if is_product_live_mode; then
  setsid env LD_LIBRARY_PATH="$PRODUCT_LIVE_LOOP_WS/build/vins_fusion_ros2:$PRODUCT_LIVE_LOOP_WS/build/vins_fusion_ros2/vins${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    "$PRODUCT_LIVE_LOOP_EXECUTABLE" "$VINS_CONFIG" \
    9>&- > "$RUN_DIR/loop_fusion.log" 2>&1 &
elif [[ "$MODE" == "frozen" || "$MODE" == "frozen-record" ]]; then
  setsid "$FROZEN_LOOP_EXECUTABLE" "$VINS_CONFIG" \
    9>&- > "$RUN_DIR/loop_fusion.log" 2>&1 &
else
  setsid ros2 run vins_fusion_ros2 loop_fusion_node "$VINS_CONFIG" \
    9>&- > "$RUN_DIR/loop_fusion.log" 2>&1 &
fi
LOOP_PID=$!

sleep 6
if ! kill -0 "$VINS_PID" 2>/dev/null || ! kill -0 "$LOOP_PID" 2>/dev/null; then
  echo "VINS或自动回环节点启动失败：$RUN_DIR" >&2
  exit 1
fi

setsid "$PYTHON_BIN" "$ROOT/scripts/record_odom_csv.py" \
  --topic /odometry --out "$RUN_DIR/odometry_raw.csv" \
  9>&- > "$RUN_DIR/odometry_raw_recorder.log" 2>&1 &
RAW_REC_PID=$!
setsid "$PYTHON_BIN" "$ROOT/scripts/record_odom_csv.py" \
  --topic /odometry_rect --out "$RUN_DIR/odometry_rect.csv" \
  9>&- > "$RUN_DIR/odometry_rect_recorder.log" 2>&1 &
RECT_REC_PID=$!
echo "三轴诊断轨迹: $RUN_DIR/odometry_raw.csv 和 odometry_rect.csv"

setsid "$PYTHON_BIN" "$ROOT/scripts/slam_runtime_watchdog.py" \
  --raw-topic /odometry --corrected-topic /odometry_rect \
  --max-data-age-s 0.5 \
  --output-json "$RUN_DIR/slam_health.json" \
  9>&- > "$RUN_DIR/slam_watchdog.log" 2>&1 &
WATCHDOG_PID=$!
echo "实时健康监测: $RUN_DIR/slam_health.json（ROS: /slam/health）"

if is_product_live_mode; then
  echo "产品模式可视化与传感器主链进程隔离；RGB预览为latest-only，不反压VINS。"
  setsid "$PYTHON_BIN" "$ROOT/scripts/rerun_vio_viewer.py" \
    --raw-odom-topic /odometry \
    --odom-topic /odometry_rect \
    --propagated-topic /imu_propagate \
    --image-topic /rgb_preview/image_raw \
    9>&- > "$RUN_DIR/rerun.log" 2>&1 &
  VIEWER_PID=$!
fi

if [[ "$MODE" == "frozen-record" ]]; then
  echo "实时显示与原始落盘由同一采集源驱动；Rerun使用 /odometry_rect 回环校正后端轨迹。"
  setsid "$PYTHON_BIN" "$ROOT/scripts/rerun_vio_viewer.py" \
    --raw-odom-topic /odometry \
    --odom-topic /odometry_rect \
    --propagated-topic /imu_propagate \
    9>&- > "$RUN_DIR/rerun.log" 2>&1 &
  VIEWER_PID=$!
  setsid "$ROOT/capture_d405_720p_rgb_stereo_ir_rsusb.sh" \
    --publish-vins --no-preview "${CAPTURE_ARGS[@]}" \
    9>&- > >(tee "$RUN_DIR/capture.log" 9>&-) 2>&1 &
  CAPTURE_PID=$!
  wait_with_viewer_supervision "$CAPTURE_PID"
else
  setsid env PYTHONPATH="$RSUSB_PYTHON:$ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    PYTHONUNBUFFERED=1 \
    "$PYTHON_BIN" "$ROOT/scripts/run_realtime.py" \
      --config "$DEVICE_CONFIG" --backend vins_fusion_ros2 --no-record \
      "${EXTRA_RUNTIME_ARGS[@]}" \
      9>&- > >(tee "$RUN_DIR/runtime.log" 9>&-) 2>&1 &
  RUNTIME_PID=$!
  wait_with_viewer_supervision "$RUNTIME_PID"
fi
