#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RSUSB_PYTHON="$ROOT/.deps/librealsense-rsusb-2.58.2/python"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
PYTHON_EXT_SUFFIX="$("$PYTHON_BIN" -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX"))')"
RSUSB_MODULE="$RSUSB_PYTHON/pyrealsense2${PYTHON_EXT_SUFFIX}"

if [[ ! -f "$RSUSB_MODULE" ]]; then
    echo "[RSUSB] 本地后端不存在，请先运行: scripts/build_librealsense_rsusb.sh" >&2
    exit 2
fi

export EGO_VIO_REALSENSE_BACKEND="RSUSB"
export PYTHONPATH="$RSUSB_PYTHON${PYTHONPATH:+:$PYTHONPATH}"

exec "$PYTHON_BIN" "$ROOT/scripts/capture_d405_720p_rgb_stereo_ir.py" \
    --capture-mode depth_stereo_ir "$@"
