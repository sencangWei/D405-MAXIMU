#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RSUSB_PYTHON="$ROOT/.deps/librealsense-rsusb-2.58.2/python"

if [[ ! -f "$RSUSB_PYTHON/pyrealsense2.cpython-310-x86_64-linux-gnu.so" ]]; then
    echo "[RSUSB] 本地后端不存在，请先运行: scripts/build_librealsense_rsusb.sh" >&2
    exit 2
fi

export EGO_VIO_REALSENSE_BACKEND="RSUSB"
export LD_LIBRARY_PATH="/usr/lib/x86_64-linux-gnu"
export PYTHONPATH="$RSUSB_PYTHON${PYTHONPATH:+:$PYTHONPATH}"

exec /usr/bin/python3 "$ROOT/scripts/capture_d405_720p_rgb_stereo_ir.py" "$@"
