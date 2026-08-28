#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${UMI_DEVICE2_D405_IMAGE:-umi-ego-vio:device2-c48df736-d405-product-v1-20260829}"
DATA_ROOT="${UMI_DEVICE2_D405_DATA_ROOT:-$HOME/umi_ego_vio_data_device2_c48df736}"
CALIB_KIT="${UMI_DEVICE2_D405_CALIB_KIT:-/home/robot/ego_vio_calib_kit}"
CONTAINER_PREFIX=umi-device2-d405
IMU_BY_ID=/dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_c48df736b505f011adda8d1272aab386-if00-port0
D405_SERIAL=260322279785
CALIB_PRODUCT_ID=UMI_DEVICE_02_D405_260322279785_20260828

usage() {
  cat <<'EOF'
第二套正式 UMI D405 产品 V1（容器一算法基线）

用法:
  ./umi-device2-d405.sh build
  ./umi-device2-d405.sh software-check
  ./umi-device2-d405.sh hardware-check
  ./umi-device2-d405.sh install-bundled-runtime-calibration
  ./umi-device2-d405.sh status
  ./umi-device2-d405.sh bind-camera
  ./umi-device2-d405.sh imu-check [秒数]
  ./umi-device2-d405.sh calibrate-init
  ./umi-device2-d405.sh calibrate-static
  ./umi-device2-d405.sh calibrate-noise
  ./umi-device2-d405.sh calibrate-d405
  ./umi-device2-d405.sh calibrate-camera-imu
  ./umi-device2-d405.sh calibrate-world-z [每条轨迹秒数]
  ./umi-device2-d405.sh calibrate-world-z-resume [每条轨迹秒数]
  ./umi-device2-d405.sh calibrate-world-z-retry-elevation2 [秒数]
  ./umi-device2-d405.sh candidate-realtime
  ./umi-device2-d405.sh candidate-realtime-record [秒数]
  ./umi-device2-d405.sh candidate-capture [秒数]
  ./umi-device2-d405.sh candidate-postprocess <候选录制会话名> <baseline|candidate>
  ./umi-device2-d405.sh install-runtime-calibration <签发目录>
  ./umi-device2-d405.sh capture [秒数]
  ./umi-device2-d405.sh realtime
  ./umi-device2-d405.sh postprocess <录制会话目录名>

说明:
  正式版本: UMI_DEVICE2_D405_PRODUCT_V1_20260829。
  VINS、回环、实时runtime与D405采集主链继承容器一。
  未绑定D405或未安装本次相机—IMU签发标定时，正式运行命令会BLOCKED。
  本启动器没有D435i临时命令。
EOF
}

mkdir -p "$DATA_ROOT"/{recordings,realtime_sessions,slam_results,logs,hil_evidence,active_runtime_calibration}
mkdir -p "$DATA_ROOT/calibration_sessions"
mkdir -p "$DATA_ROOT/candidate_ab"/{recordings,slam_results,logs}

base_args=(
  --rm
  --network host
  --ipc host
  --user "$(id -u):$(id -g)"
  --env HOME=/home/robot
  --env EGO_VIO_DEVICE_SET_ID=UMI_DEVICE_02_C48DF736
  --env EGO_VIO_RELEASE_ID=UMI_DEVICE2_D405_PRODUCT_V1_20260829
  --env "EGO_VIO_D405_SERIAL=$D405_SERIAL"
  --env "EGO_VIO_IMU_BY_ID=$IMU_BY_ID"
  --mount "type=bind,src=$DATA_ROOT,dst=/data"
  --mount "type=bind,src=$DATA_ROOT/realtime_sessions,dst=/home/robot/ego_vio_humble/recordings"
)

add_name() {
  base_args+=(--name "${CONTAINER_PREFIX}-${1}-$$")
}

add_imu() {
  local tty_device
  [[ -e "$IMU_BY_ID" ]] || { echo "缺少第二套STM32串口: $IMU_BY_ID" >&2; exit 3; }
  tty_device="$(readlink -f "$IMU_BY_ID")"
  [[ -c "$tty_device" ]] || { echo "串口目标不是字符设备: $tty_device" >&2; exit 3; }
  base_args+=(
    --device "$tty_device:$tty_device"
    --group-add "$(stat -c %g "$tty_device")"
    --mount type=bind,src=/dev/serial/by-id,dst=/dev/serial/by-id,readonly
  )
}

add_camera_usb() {
  [[ -d /dev/bus/usb ]] || { echo "缺少 /dev/bus/usb" >&2; exit 3; }
  base_args+=(
    --device-cgroup-rule "c 189:* rmw"
    --mount type=bind,src=/dev/bus/usb,dst=/dev/bus/usb
  )
  if [[ -d /dev/dri ]]; then
    base_args+=(--device /dev/dri)
    while IFS= read -r gid; do
      base_args+=(--group-add "$gid")
    done < <(find /dev/dri -maxdepth 1 -type c -printf '%G\n' | sort -u)
  fi
}

add_display() {
  [[ -n "${DISPLAY:-}" && -d /tmp/.X11-unix ]] || {
    echo "DISPLAY或X11 socket不可用" >&2
    exit 4
  }
  base_args+=(
    --env "DISPLAY=$DISPLAY"
    --mount type=bind,src=/tmp/.X11-unix,dst=/tmp/.X11-unix,readonly
  )
  if [[ -n "${XAUTHORITY:-}" && -f "$XAUTHORITY" ]]; then
    base_args+=(
      --env "XAUTHORITY=$XAUTHORITY"
      --mount "type=bind,src=$XAUTHORITY,dst=$XAUTHORITY,readonly"
    )
  fi
}

add_calibration_kit() {
  [[ -f "$CALIB_KIT/product_calibration_stage.py" ]] || {
    echo "缺少标定工具包: $CALIB_KIT" >&2
    exit 3
  }
  base_args+=(
    --env PYTHONDONTWRITEBYTECODE=1
    --mount "type=bind,src=$CALIB_KIT,dst=/home/robot/ego_vio_calib_kit,readonly"
  )
}

run_calibration_stage() {
  local stage="$1"
  shift
  docker run --interactive "${base_args[@]}" "$IMAGE" \
    python3 /home/robot/ego_vio_calib_kit/product_calibration_stage.py \
    "$stage" "$CALIB_PRODUCT_ID" \
    --session-root /data/calibration_sessions \
    "$@"
}

latest_world_z_candidate() {
  local attempts_host latest_attempt report candidate
  attempts_host="$DATA_ROOT/calibration_sessions/$CALIB_PRODUCT_ID/world_z/attempts"
  latest_attempt="$(find "$attempts_host" -mindepth 1 -maxdepth 1 -type d \
    -name 'attempt_*' -printf '%f\n' 2>/dev/null | sort | tail -n 1)"
  [[ -n "$latest_attempt" ]] || {
    echo "没有world-Z候选attempt" >&2
    return 3
  }
  report="$attempts_host/$latest_attempt/report.yaml"
  candidate="$attempts_host/$latest_attempt/candidate_runtime"
  [[ -f "$report" && -f "$candidate/manifest.yaml" ]] || {
    echo "最新world-Z attempt没有完整候选: $latest_attempt" >&2
    return 3
  }
  printf '/data/calibration_sessions/%s/world_z/attempts/%s/candidate_runtime\n' \
    "$CALIB_PRODUCT_ID" "$latest_attempt"
}

case "${1:-}" in
  build)
    docker build \
      --tag "$IMAGE" \
      "$ROOT"
    ;;
  software-check)
    add_name software-check
    docker run "${base_args[@]}" "$IMAGE" \
      umi-container-preflight --software-only
    ;;
  hardware-check)
    add_name hardware-check
    add_imu
    add_camera_usb
    docker run "${base_args[@]}" "$IMAGE" umi-container-preflight
    ;;
  status)
    add_name status
    add_imu
    docker run "${base_args[@]}" "$IMAGE" \
      umi-device2-d405-control status
    ;;
  bind-camera)
    add_name bind-camera
    add_imu
    add_camera_usb
    docker run "${base_args[@]}" "$IMAGE" \
      umi-device2-d405-control bind-camera
    ;;
  imu-check)
    duration="${2:-10}"
    add_name imu-check
    add_imu
    docker run "${base_args[@]}" "$IMAGE" \
      umi-device2-d405-control imu-check --duration "$duration"
    ;;
  calibrate-init)
    add_name calibrate-init
    add_imu
    add_camera_usb
    add_calibration_kit
    run_calibration_stage init --port "$IMU_BY_ID"
    ;;
  calibrate-static)
    add_name calibrate-static
    add_imu
    add_calibration_kit
    run_calibration_stage imu-static \
      --port "$IMU_BY_ID" --protocol stm32_combined_v1
    ;;
  calibrate-noise)
    add_name calibrate-noise
    add_imu
    add_calibration_kit
    run_calibration_stage imu-noise \
      --port "$IMU_BY_ID" --protocol stm32_combined_v1
    ;;
  calibrate-d405)
    add_name calibrate-d405
    add_imu
    add_camera_usb
    add_display
    add_calibration_kit
    run_calibration_stage d405-factory \
      --port "$IMU_BY_ID" --protocol stm32_combined_v1
    ;;
  calibrate-camera-imu)
    add_name calibrate-camera-imu
    add_imu
    add_camera_usb
    add_display
    add_calibration_kit
    run_calibration_stage camera-imu \
      --port "$IMU_BY_ID" --protocol stm32_combined_v1 --capture-only
    pending_host="$DATA_ROOT/calibration_sessions/$CALIB_PRODUCT_ID/camera_imu/pending_split_capture.yaml"
    pending_container="/data/calibration_sessions/$CALIB_PRODUCT_ID/camera_imu/pending_split_capture.yaml"
    [[ -f "$pending_host" ]] || {
      echo "两轮采集结束但缺少待求解清单: $pending_host" >&2
      exit 3
    }
    python3 "$ROOT/docker/solve_camera_imu_split.py" \
      --manifest "$pending_host" \
      --host-data-root "$DATA_ROOT" \
      --calibration-kit "$CALIB_KIT" \
      --target-asset "$ROOT/calibration_assets/aprilgrid_6x6_35mm.yaml"
    base_args=()
    base_args=(
      --rm
      --network host
      --ipc host
      --user "$(id -u):$(id -g)"
      --env HOME=/home/robot
      --env EGO_VIO_DEVICE_SET_ID=UMI_DEVICE_02_C48DF736
      --env "EGO_VIO_D405_SERIAL=$D405_SERIAL"
      --env "EGO_VIO_IMU_BY_ID=$IMU_BY_ID"
      --mount "type=bind,src=$DATA_ROOT,dst=/data"
      --mount "type=bind,src=$DATA_ROOT/realtime_sessions,dst=/home/robot/ego_vio_humble/recordings"
    )
    add_name calibrate-camera-imu-finalize
    add_calibration_kit
    run_calibration_stage camera-imu \
      --finalize-manifest "$pending_container"
    ;;
  calibrate-world-z)
    duration="${2:-120}"
    add_name calibrate-world-z
    add_imu
    add_camera_usb
    add_calibration_kit
    run_calibration_stage world-z \
      --vins-runtime /home/robot/ego_vio_humble \
      --duration "$duration"
    ;;
  calibrate-world-z-resume)
    duration="${2:-120}"
    attempts_host="$DATA_ROOT/calibration_sessions/$CALIB_PRODUCT_ID/world_z/attempts"
    latest_attempt="$(find "$attempts_host" -mindepth 1 -maxdepth 1 -type d \
      -name 'attempt_*' -printf '%f\n' 2>/dev/null | sort | tail -n 1)"
    [[ -n "$latest_attempt" ]] || {
      echo "没有可续跑的world-Z attempt" >&2
      exit 3
    }
    [[ ! -f "$attempts_host/$latest_attempt/report.yaml" ]] || {
      echo "最新world-Z attempt已有最终报告，禁止续写: $latest_attempt" >&2
      exit 3
    }
    add_name calibrate-world-z-resume
    add_imu
    add_camera_usb
    add_calibration_kit
    run_calibration_stage world-z \
      --vins-runtime /home/robot/ego_vio_humble \
      --duration "$duration" \
      --resume-attempt "/data/calibration_sessions/$CALIB_PRODUCT_ID/world_z/attempts/$latest_attempt"
    ;;
  calibrate-world-z-retry-elevation2)
    duration="${2:-50}"
    attempts_host="$DATA_ROOT/calibration_sessions/$CALIB_PRODUCT_ID/world_z/attempts"
    latest_attempt="$(find "$attempts_host" -mindepth 1 -maxdepth 1 -type d \
      -name 'attempt_*' -printf '%f\n' 2>/dev/null | sort | tail -n 1)"
    [[ -n "$latest_attempt" ]] || {
      echo "没有可重试的world-Z attempt" >&2
      exit 3
    }
    [[ -f "$attempts_host/$latest_attempt/report.yaml" ]] || {
      echo "最新world-Z attempt尚无最终FAIL报告: $latest_attempt" >&2
      exit 3
    }
    add_name calibrate-world-z-retry-elevation2
    add_imu
    add_camera_usb
    add_calibration_kit
    run_calibration_stage world-z \
      --vins-runtime /home/robot/ego_vio_humble \
      --duration "$duration" \
      --resume-attempt "/data/calibration_sessions/$CALIB_PRODUCT_ID/world_z/attempts/$latest_attempt" \
      --replace-capture elevation_2
    ;;
  candidate-capture)
    duration="${2:-60}"
    candidate="$(latest_world_z_candidate)"
    add_name candidate-capture
    add_imu
    add_camera_usb
    preview_args=()
    if [[ "${UMI_CAPTURE_PREVIEW:-0}" == 1 ]]; then
      add_display
      preview_args=(--preview)
    fi
    docker run "${base_args[@]}" "$IMAGE" \
      umi-device2-d405-control candidate-capture \
      "$candidate" "$duration" "${preview_args[@]}"
    ;;
  candidate-realtime)
    candidate="$(latest_world_z_candidate)"
    add_name candidate-realtime
    add_imu
    add_camera_usb
    add_display
    docker run "${base_args[@]}" "$IMAGE" \
      umi-device2-d405-control candidate-realtime "$candidate"
    ;;
  candidate-realtime-record)
    duration="${2:-60}"
    candidate="$(latest_world_z_candidate)"
    add_name candidate-realtime-record
    add_imu
    add_camera_usb
    add_display
    docker run "${base_args[@]}" "$IMAGE" \
      umi-device2-d405-control candidate-realtime-record \
      "$candidate" "$duration"
    ;;
  candidate-postprocess)
    session_name="${2:-}"
    variant="${3:-}"
    [[ -n "$session_name" && "$session_name" != */* && \
      "$session_name" == d405_720p_rgb_stereo_ir_* ]] || {
      echo "请提供candidate_ab/recordings下的d405_720p_rgb_stereo_ir_*会话名" >&2
      exit 2
    }
    [[ "$variant" == baseline || "$variant" == candidate ]] || {
      echo "A/B变体必须是baseline或candidate" >&2
      exit 2
    }
    session="$DATA_ROOT/candidate_ab/recordings/$session_name"
    [[ -d "$session" ]] || { echo "候选会话不存在: $session" >&2; exit 3; }
    capture_binding="$session/candidate_ab_capture.yaml"
    [[ -f "$capture_binding" ]] || {
      echo "候选会话缺少哈希绑定: $capture_binding" >&2
      exit 3
    }
    candidate="$(python3 - "$capture_binding" <<'PY'
import sys
import yaml
document = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
print(document.get("candidate_source", ""))
PY
)"
    case "$candidate" in
      /data/calibration_sessions/$CALIB_PRODUCT_ID/world_z/attempts/attempt_*/candidate_runtime) ;;
      *) echo "候选会话绑定了非法候选路径: $candidate" >&2; exit 3 ;;
    esac
    output_name="${session_name}_${variant}_$(date +%Y%m%d_%H%M%S)_slam"
    add_name "candidate-postprocess-$variant"
    docker run "${base_args[@]}" "$IMAGE" \
      umi-device2-d405-control candidate-postprocess \
      "$candidate" "/data/candidate_ab/recordings/$session_name" \
      "/data/candidate_ab/slam_results/$output_name" \
      --variant "$variant"
    echo "候选A/B结果: $DATA_ROOT/candidate_ab/slam_results/$output_name"
    ;;
  install-runtime-calibration)
    source_dir="${2:-}"
    [[ -n "$source_dir" && -d "$source_dir" ]] || {
      echo "请提供存在的签发标定目录" >&2
      exit 2
    }
    source_dir="$(realpath "$source_dir")"
    add_name install-runtime-calibration
    docker run "${base_args[@]}" \
      --mount "type=bind,src=$source_dir,dst=/import_calibration,readonly" \
      "$IMAGE" umi-device2-d405-control \
      install-runtime-calibration /import_calibration
    ;;
  install-bundled-runtime-calibration)
    add_name install-bundled-runtime-calibration
    docker run "${base_args[@]}" "$IMAGE" \
      umi-device2-d405-control install-runtime-calibration \
      /opt/umi/formal_runtime_calibration
    ;;
  capture)
    duration="${2:-60}"
    add_name capture
    add_imu
    add_camera_usb
    preview_args=()
    if [[ "${UMI_CAPTURE_PREVIEW:-0}" == 1 ]]; then
      add_display
      preview_args=(--preview)
    fi
    docker run "${base_args[@]}" "$IMAGE" \
      umi-device2-d405-control capture "$duration" "${preview_args[@]}"
    ;;
  realtime)
    add_name realtime
    add_imu
    add_camera_usb
    add_display
    docker run "${base_args[@]}" "$IMAGE" \
      umi-device2-d405-control realtime
    ;;
  postprocess)
    session_name="${2:-}"
    [[ -n "$session_name" && "$session_name" != */* && \
      "$session_name" == d405_720p_rgb_stereo_ir_* ]] || {
      echo "请提供recordings下的d405_720p_rgb_stereo_ir_*会话名" >&2
      exit 2
    }
    session="$DATA_ROOT/recordings/$session_name"
    [[ -d "$session" ]] || { echo "会话不存在: $session" >&2; exit 3; }
    output_name="${session_name}_$(date +%Y%m%d_%H%M%S)_slam"
    add_name postprocess
    docker run "${base_args[@]}" "$IMAGE" \
      umi-device2-d405-control postprocess \
      "/data/recordings/$session_name" "/data/slam_results/$output_name"
    echo "SLAM结果: $DATA_ROOT/slam_results/$output_name"
    ;;
  *)
    usage
    exit 2
    ;;
esac
