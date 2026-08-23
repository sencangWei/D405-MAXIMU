#!/usr/bin/env bash
# 只读预检：不会开始采集、不会改标定参数。
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CAPTURE_RUNTIME="${EGO_VIO_CAPTURE_RUNTIME:-/home/robot/ego_vio_humble}"
RSUSB_RUNTIME="${EGO_VIO_RSUSB_RUNTIME:-/home/robot/D405-MAXIMU}"
VINS_RUNTIME="${EGO_VIO_VINS_RUNTIME:-/home/robot/ego_vio_humble}"
KALIBR_IMAGE="${EGO_VIO_KALIBR_IMAGE:-ego-vio-kalibr:1f602274-minimal}"
KALIBR_IMAGE_ID="sha256:4e1506d4ff12b1c6918441ca514bc0001f4c10bf17efe0283b5db1453640f863"
export EGO_VIO_CAPTURE_RUNTIME="$CAPTURE_RUNTIME"
export EGO_VIO_RSUSB_RUNTIME="$RSUSB_RUNTIME"
export EGO_VIO_VINS_RUNTIME="$VINS_RUNTIME"
cd "$ROOT"

fail() {
  echo "BLOCKED：$1" >&2
  exit 2
}

[[ "$(lsb_release -rs 2>/dev/null || true)" == "22.04" ]] \
  || fail "系统不是 Ubuntu 22.04"
id -nG | tr ' ' '\n' | grep -qx dialout \
  || fail "当前登录会话尚未获得 dialout 组权限，请注销后重新登录"
for command in kalibr_calibrate_cameras kalibr_calibrate_imu_camera kalibr_camera_validator; do
  wrapper="$HOME/.local/bin/$command"
  [[ -x "$wrapper" ]] || fail "缺少 $command，请先运行 ./calib_setup.sh"
  cmp -s "$ROOT/product_calibration/kalibr_docker_command" "$wrapper" \
    || fail "$command 不是本交付包的固定容器入口，请重跑 ./calib_setup.sh"
done
docker info >/dev/null 2>&1 \
  || fail "Docker服务不可用或当前登录会话尚未获得docker组权限"
docker image inspect "$KALIBR_IMAGE" >/dev/null 2>&1 \
  || fail "缺少固定Kalibr镜像 $KALIBR_IMAGE"
[[ "$(docker image inspect "$KALIBR_IMAGE" --format '{{.Id}}')" == "$KALIBR_IMAGE_ID" ]] \
  || fail "Kalibr镜像标签未指向签发镜像ID"

for path in \
  "$CAPTURE_RUNTIME/scripts/collect_calib_data.py" \
  "$CAPTURE_RUNTIME/scripts/convert_to_kalibr_bag.py" \
  "$CAPTURE_RUNTIME/config/aprilgrid_6x6_35mm.yaml" \
  "$VINS_RUNTIME/run_vins_realtime.sh" \
  "$RSUSB_RUNTIME/.deps"; do
  [[ -e "$path" ]] || fail "交付运行时缺失 $path"
done

python3 - <<'PY' || fail "Python依赖不完整"
for module in ("aprilgrid", "cv2", "numpy", "rosbags", "scipy", "serial", "yaml"):
    __import__(module)
PY

python3 - <<'PY' || fail "D405或RSUSB运行库不可用"
from product_calibration.kalibr_pipeline import (
    DEFAULT_CAPTURE_RUNTIME, DEFAULT_RSUSB_RUNTIME, detect_d405,
)
device = detect_d405(DEFAULT_CAPTURE_RUNTIME, DEFAULT_RSUSB_RUNTIME)
print(f"D405: {device['serial']} firmware={device['firmware']}")
PY

ports=(/dev/serial/by-id/*)
[[ -e "${ports[0]}" ]] || fail "没有发现稳定的 /dev/serial/by-id/ IMU端口"
[[ "${#ports[@]}" -eq 1 ]] \
  || fail "发现${#ports[@]}个串口；建档时必须断开无关串口或显式指定 --port"
[[ -r "${ports[0]}" && -w "${ports[0]}" ]] \
  || fail "当前用户不能读写 ${ports[0]}"

echo "PASS：标定环境、D405和唯一IMU端口预检通过。"
echo "下一步：./calibrate_init.sh 产品编号"
