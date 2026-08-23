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

for argument in "$@"; do
    case "$argument" in
        --duration|--duration=*)
            echo "[3h监测] 正式入口固定为10800秒；短时冒烟请直接运行Python监测器。" >&2
            exit 2
            ;;
    esac
done

exec systemd-inhibit --what=sleep --why="3h D405 STM32 monitor-only soak" \
    "$PYTHON_BIN" "$ROOT/scripts/monitor_d405_stm32_soak.py" \
    "$@" \
    --duration 10800
