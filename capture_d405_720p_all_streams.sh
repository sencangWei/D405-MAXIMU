#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# Use the system pyrealsense2: the .deps librealsense RSUSB build freezes the
# D405 color stream in a synced 4-stream frameset (measured ~5 fps stale frames),
# while the system build delivers clean ~30 fps on all four streams.
exec python3 -u "$ROOT_DIR/scripts/capture_d405_720p_all_streams.py" "$@"
