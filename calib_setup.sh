#!/usr/bin/env bash
# Ubuntu 22.04 / ROS 2 Humble 产品标定环境安装与校验。
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CAPTURE_RUNTIME="${EGO_VIO_CAPTURE_RUNTIME:-/home/robot/ego_vio_humble}"
RSUSB_RUNTIME="${EGO_VIO_RSUSB_RUNTIME:-/home/robot/D405-MAXIMU}"
KALIBR_IMAGE="${EGO_VIO_KALIBR_IMAGE:-ego-vio-kalibr:1f602274-minimal}"
KALIBR_IMAGE_ID="sha256:4e1506d4ff12b1c6918441ca514bc0001f4c10bf17efe0283b5db1453640f863"
CURRENT_USER="$(id -un)"

if [[ "$(lsb_release -rs 2>/dev/null || true)" != "22.04" ]]; then
  echo "BLOCKED：客户标定机必须是 Ubuntu 22.04。" >&2
  exit 2
fi

echo "[1/5] 安装 Ubuntu 22.04 基础依赖"
sudo apt-get update
sudo apt-get install -y \
  docker.io libopencv-dev libusb-1.0-0-dev python3-opencv python3-pip \
  python3-serial python3-scipy python3-venv python3-yaml udev usbutils

echo "[2/5] 安装主机 Python 采集/分析依赖"
python3 -m pip install --user \
  "numpy==1.24.4" "rosbags==0.11.4" "aprilgrid==0.5.0"

echo "[3/5] 安装固定 Kalibr 容器入口"
for command in kalibr_calibrate_cameras kalibr_calibrate_imu_camera kalibr_camera_validator; do
  install -Dm755 "$ROOT/product_calibration/kalibr_docker_command" \
    "$HOME/.local/bin/$command"
done

echo "[4/5] 配置永久设备权限"
sudo usermod -aG dialout,docker "$CURRENT_USER"

echo "[5/5] 校验交付运行时"
missing=0
for path in \
  "$CAPTURE_RUNTIME/scripts/collect_calib_data.py" \
  "$CAPTURE_RUNTIME/scripts/convert_to_kalibr_bag.py" \
  "$CAPTURE_RUNTIME/config/aprilgrid_6x6_35mm.yaml" \
  "$RSUSB_RUNTIME/.deps"; do
  if [[ ! -e "$path" ]]; then
    echo "缺失：$path" >&2
    missing=1
  fi
done
if ! docker image inspect "$KALIBR_IMAGE" >/dev/null 2>&1; then
  echo "缺失：固定 Kalibr 镜像 $KALIBR_IMAGE" >&2
  echo "请从交付介质执行：docker load -i <kalibr镜像tar>" >&2
  missing=1
elif [[ "$(docker image inspect "$KALIBR_IMAGE" --format '{{.Id}}')" != "$KALIBR_IMAGE_ID" ]]; then
  echo "错误：Kalibr镜像标签指向了非签发镜像：$KALIBR_IMAGE" >&2
  missing=1
fi
if [[ "$missing" -ne 0 ]]; then
  echo "BLOCKED：交付运行时不完整；不要开始硬件采集。" >&2
  exit 2
fi

echo "安装文件已就位。必须注销并重新登录，使 dialout/docker 组生效。"
echo "重新登录后执行：cd $ROOT && ./calibrate_preflight.sh"
