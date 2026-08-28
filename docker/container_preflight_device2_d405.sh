#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/robot/ego_vio_humble
MANIFEST=/opt/umi/device_manifest.yaml
HARDWARE=1
if [[ "${1:-}" == "--software-only" ]]; then
  HARDWARE=0
elif [[ $# -gt 0 ]]; then
  echo "用法: umi-container-preflight [--software-only]" >&2
  exit 2
fi

failures=0
pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1" >&2; failures=$((failures + 1)); }

if /usr/local/bin/umi-container-preflight-product1 --software-only; then
  pass "容器一软件基线预检"
else
  fail "容器一软件基线预检"
fi

check_hash() {
  local expected="$1"
  local path="$2"
  local label="$3"
  if [[ -f "$path" && "$(sha256sum "$path" | awk '{print $1}')" == "$expected" ]]; then
    pass "$label"
  else
    fail "$label"
  fi
}

check_hash a5c4209a10407f4d284305e43b33de611fabbf1ea9472f460e10e761590a620f \
  "$ROOT/run_vins_realtime.sh" "容器一实时入口未变"
check_hash f4aae2ca6400906b3f996e21e8e0acbb11f35533a90bb1a895af59b251809a5b \
  "$ROOT/run_slam_postprocess.sh" "容器一后处理入口未变"
check_hash 102c67ffcda0e1b1d3950d7433bd057647523d17f35390e38ee28db5b74057a7 \
  "$ROOT/ego_vio/runtime.py" "容器一实时runtime未变"
check_hash ba23481fa5612cbc150f44fec40103c5c8ca936c18572d1e2f25f29e239c16ec \
  "$ROOT/ego_vio/imu/imu_reader.py" "容器一IMU reader未变"
check_hash e0cb280349fa0ca8792462f7864e818f138094890965f6cf4d6dc4413c0eb4bc \
  "$ROOT/config/aprilgrid_6x6_35mm.yaml" "6x6 AprilGrid标定资产"

if python3 - <<'PY'
import hashlib
from pathlib import Path
import yaml

root = Path("/opt/umi/formal_runtime_calibration")
manifest = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
assert manifest["schema"] == "umi_device_runtime_calibration_v1"
assert manifest["result"] == "PASS"
assert manifest["release_id"] == "UMI_DEVICE2_D405_PRODUCT_V1_20260829"
assert manifest["device_set_id"] == "UMI_DEVICE_02_C48DF736"
assert manifest["d405_serial"] == "260322279785"
for name, expected in manifest["files"].items():
    actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
    assert actual == expected, name
PY
then
  pass "第二套正式签发运行标定"
else
  fail "第二套正式签发运行标定"
fi

check_hash 6400b4adf5e004b3f62086cafd23da8481c48bbe7dc596ca9ded37b7157440cb \
  /usr/local/bin/umi-run-slam-postprocess-configurable \
  "隔离A/B后处理入口及完整哈希锚定"

if [[ -x "$ROOT/run_vins_realtime_candidate.sh" ]] \
  && grep -Fq 'nice -n "${EGO_VIO_VIEWER_NICE:-10}"' \
    "$ROOT/run_vins_realtime_candidate.sh" \
  && grep -Fq -- '--publish-vins --no-preview' \
    "$ROOT/run_vins_realtime_candidate.sh"; then
  pass "候选实时入口低优先级可视化与单源完整录制"
else
  fail "候选实时入口低优先级可视化与单源完整录制"
fi

if grep -Fxq \
  'PRODUCT_CAMERA_IMU_TD_S = float(os.environ["EGO_VIO_CAMERA_IMU_TD_S"])' \
  "$ROOT/scripts/capture_d405_720p_rgb_stereo_ir.py"; then
  pass "采集器使用签发camera-IMU td"
else
  fail "采集器使用签发camera-IMU td"
fi

if grep -Fq 'preview_topic="/rgb_preview/image_raw"' \
    "$ROOT/scripts/capture_d405_720p_rgb_stereo_ir.py" \
  && grep -Fq 'def color_frame_to_bgr(frame)' \
    "$ROOT/scripts/capture_d405_720p_rgb_stereo_ir.py" \
  && grep -Fq 'color_yuyv.dtype == np.uint16' \
    "$ROOT/scripts/capture_d405_720p_rgb_stereo_ir.py" \
  && grep -Fq 'color=color_frame_to_bgr(' \
    "$ROOT/scripts/capture_d405_720p_rgb_stereo_ir.py" \
  && grep -Fq 'EGO_VIO_VIEWER_INITIAL_SETTLE_S' \
    "$ROOT/scripts/rerun_vio_viewer.py" \
  && grep -Fq '"gripper_pub": self._gripper_published' \
    "$ROOT/ego_vio/vio/openvins_ros2_bridge.py" \
  && grep -Fq 'gripper_topic: str = "/gripper/state"' \
    "$ROOT/ego_vio/vio/openvins_ros2_bridge.py" \
  && grep -Fq 'gripper={counts["gripper"]}' \
    "$ROOT/scripts/rerun_vio_viewer.py" \
  && grep -Fq 'TextDocumentView(origin=f"world/{name}/gripper"' \
    "$ROOT/ego_vio/visualizer/rerun_viz.py"; then
  pass "同源录制保留RGB/夹爪实时显示且仅隐藏静止收敛段"
else
  fail "同源录制保留RGB/夹爪实时显示且仅隐藏静止收敛段"
fi

if python3 - <<'PY'
import importlib.metadata as metadata
from aprilgrid import Detector

assert metadata.version("aprilgrid") == "0.5.0"
Detector("t36h11b1")
PY
then
  pass "AprilGrid检测器0.5.0"
else
  fail "AprilGrid检测器0.5.0"
fi

if python3 - <<'PY'
from pathlib import Path
from PIL import ImageFont

source = Path(
    "/home/robot/ego_vio_humble/scripts/collect_calib_data.py"
).read_text(encoding="utf-8")
assert "PREVIEW_FONT_PATHS" in source
assert "ImageFont.load_default()" in source
assert "--disable-emitter" not in source
assert "emitter_enabled" not in source
assert "q_camera_write" in source
assert "q_imu_write" in source
assert "q_write = Queue" not in source
assert "q_write.put" not in source
assert '"arrival_mono"' in source
assert "f.ts_arrival" in source
assert '''item = ("img", f.frame_idx, f.ts, f.color, right,
                    time.time(), f.frame_number, f.ts_arrival)''' in source
assert "_, idx, ts, img, img_right, ts_wall, fnum, arrival_mono = item" in source
assert "stage.feed_image(item[3], detector)" in source

converter = Path(
    "/home/robot/ego_vio_humble/scripts/convert_to_kalibr_bag.py"
).read_text(encoding="utf-8")
assert 'raw_rows.append((int(r["idx"]), float(r["ts_mono"]), r))' in converter
assert 'timestamp_field = "arrival_mono"' not in converter
assert "全段时基残差" in converter

paths = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.otf",
)
font = None
for path in paths:
    try:
        font = ImageFont.truetype(path, 25)
        break
    except OSError:
        continue
assert font is not None, "missing required Noto CJK preview font"
assert font.getbbox("相机—IMU联合标定") is not None
PY
then
  pass "D405中文标定预览与相机/IMU解耦写盘且未引入D435i代码"
else
  fail "D405中文标定预览与相机/IMU解耦写盘且未引入D435i代码"
fi

if python3 - <<'PY'
from pathlib import Path
import yaml
from ego_vio.gripper import ManualGripperCalibration

manifest = yaml.safe_load(Path('/opt/umi/device_manifest.yaml').read_text())
assert manifest['device_set_id'] == 'UMI_DEVICE_02_C48DF736'
assert manifest['software']['base_image_id'] == 'sha256:59974df3c3906683739fc7af530c6e7a5e78a80b341506ccdb49d7be2fb5ef3a'
assert manifest['software']['d435i_runtime_policy'] == 'EXCLUDED'
profile = ManualGripperCalibration.load()
assert profile.profile_id == 'UMI_MANUAL_GRIPPER_C48DF736_20260826_V4'
print(profile.profile_id)
PY
then
  pass "第二套身份与V4夹爪默认配置"
else
  fail "第二套身份与V4夹爪默认配置"
fi

if /usr/local/bin/umi-device2-d405-control --help | grep -qi 'd435i'; then
  fail "正式控制器包含D435i运行入口"
else
  pass "正式控制器不含D435i运行入口"
fi

if (( HARDWARE )); then
  IMU_BY_ID="${EGO_VIO_IMU_BY_ID:-/dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_c48df736b505f011adda8d1272aab386-if00-port0}"
  D405_SERIAL="${EGO_VIO_D405_SERIAL:-260322279785}"
  [[ -r "$IMU_BY_ID" && -w "$IMU_BY_ID" ]] \
    && pass "第二套STM32串口" || fail "第二套STM32串口"
  if PYTHONPATH="$ROOT/.deps/librealsense-rsusb-2.58.2/python" \
      python3 - "$D405_SERIAL" <<'PY'
import sys
import pyrealsense2 as rs

serial = sys.argv[1]
found = []
for device in rs.context().query_devices():
    name = device.get_info(rs.camera_info.name)
    current = device.get_info(rs.camera_info.serial_number)
    if 'D405' in name.upper() and current == serial:
        found.append((name, current))
if len(found) != 1:
    raise SystemExit(f'expected one D405 {serial}, found={found}')
print(found[0])
PY
  then
    pass "第二套D405 $D405_SERIAL"
  else
    fail "第二套D405 $D405_SERIAL"
  fi
fi

if (( failures )); then
  echo "第二套正式D405容器预检 FAIL: $failures 项" >&2
  exit 1
fi
echo "第二套正式D405容器预检 PASS"
