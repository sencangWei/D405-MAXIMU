#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if command -v systemd-inhibit >/dev/null 2>&1; then
  exec systemd-inhibit --what=sleep --why="product IMU Allan calibration" \
    python3 "$ROOT/product_calibration_stage.py" imu-noise "$@"
fi
exec python3 "$ROOT/product_calibration_stage.py" imu-noise "$@"
