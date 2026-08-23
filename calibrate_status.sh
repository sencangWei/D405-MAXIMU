#!/usr/bin/env bash
# 查看一台产品的阶段状态，不采集、不修改报告。
set -euo pipefail

if [[ "$#" -ne 1 ]]; then
  echo "用法：$0 产品编号" >&2
  exit 2
fi
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SESSION_ROOT="${EGO_VIO_CALIBRATION_SESSIONS:-$ROOT/calibration_sessions}"
exec python3 "$ROOT/product_calibration_wizard.py" status \
  --session "$SESSION_ROOT/$1"
