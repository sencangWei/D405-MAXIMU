#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# 三路 720p 采集 (RGB + 左IR + 右IR) + 外置 IMU。
# 用于 OpenVINS 实时双目 VIO + 后端双目/RGB SLAM。
exec python3 -u "$ROOT_DIR/scripts/capture_d405_720p_rgb_stereo_ir.py" "$@"
