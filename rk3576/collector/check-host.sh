#!/usr/bin/env bash
set -euo pipefail

RELEASE_ROOT=$(cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)
export PYTHONDONTWRITEBYTECODE=1

test "$(uname -m)" = aarch64
test "$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" = 3.12
test -f /usr/lib/aarch64-linux-gnu/libpython3.12.so.1.0
command -v gst-launch-1.0 >/dev/null
command -v gst-inspect-1.0 >/dev/null
for element in mpph265enc jpegenc videorate multipartmux tcpserversink; do
  gst-inspect-1.0 "$element" >/dev/null
done
test -x "$RELEASE_ROOT/native/bin/umi-record-native"
test -x "$RELEASE_ROOT/native/bin/umi-rsusb-probe"
test -L "$RELEASE_ROOT/runtime/pyrealsense2.cpython-312-aarch64-linux-gnu.so.2.58"
test "$(readlink -- "$RELEASE_ROOT/runtime/pyrealsense2.cpython-312-aarch64-linux-gnu.so.2.58")" = pyrealsense2.cpython-312-aarch64-linux-gnu.so
if ldd "$RELEASE_ROOT/native/bin/umi-record-native" | grep -q 'not found'; then
  echo 'Native collector has unresolved shared-library dependencies' >&2
  exit 1
fi
PYTHONPATH="$RELEASE_ROOT/adapter:$RELEASE_ROOT/vendor/ego-runtime:$RELEASE_ROOT/vendor/site-packages" \
  python3 -c 'import fastapi, uvicorn, ego_service_bootstrap, umi_recorderctl, umi_preview_web, umi_publish'
printf 'HOST_RUNTIME_PASS\n'
