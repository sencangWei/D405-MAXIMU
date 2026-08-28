#!/usr/bin/env python3
"""Build the device-2 candidate live runner from the immutable product-1 entry.

The product-1 script remains byte-for-byte unchanged.  This deterministic
transform creates a derived entry that gives the CPU-only viewer lower
scheduler priority and optionally replaces the sensor runtime with the proven
single-owner full recorder in ``--publish-vins`` mode.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def transform(source: str) -> str:
    viewer_old = '''  setsid "$PYTHON_BIN" "$ROOT/scripts/rerun_vio_viewer.py" \\
'''
    viewer_new = '''  setsid nice -n "${EGO_VIO_VIEWER_NICE:-10}" \\
    "$PYTHON_BIN" "$ROOT/scripts/rerun_vio_viewer.py" \\
'''
    if source.count(viewer_old) != 1:
        raise RuntimeError("product live viewer launch anchor changed")
    source = source.replace(viewer_old, viewer_new, 1)

    runtime_old = '''setsid env PYTHONPATH="$RSUSB_PYTHON:$ROOT${PYTHONPATH:+:$PYTHONPATH}" \\
  PYTHONUNBUFFERED=1 \\
  "$PYTHON_BIN" "$ROOT/scripts/run_realtime.py" \\
    --config "$DEVICE_CONFIG" --backend vins_fusion_ros2 --no-record \\
    "${EXTRA_RUNTIME_ARGS[@]}" \\
    9>&- > >(tee "$RUN_DIR/runtime.log" 9>&-) 2>&1 &
RUNTIME_PID=$!
wait_with_viewer_supervision "$RUNTIME_PID"
'''
    runtime_new = '''if [[ -n "${EGO_VIO_RECORD_DURATION_S:-}" ]]; then
  RECORD_OUTPUT_ROOT="${EGO_VIO_RECORD_OUTPUT_ROOT:?missing EGO_VIO_RECORD_OUTPUT_ROOT}"
  echo "实时同源录制: ${EGO_VIO_RECORD_DURATION_S}s -> $RECORD_OUTPUT_ROOT"
  setsid env PYTHONPATH="$RSUSB_PYTHON:$ROOT${PYTHONPATH:+:$PYTHONPATH}" \\
    PYTHONUNBUFFERED=1 \\
    "$PYTHON_BIN" "$ROOT/scripts/capture_d405_720p_rgb_stereo_ir.py" \\
      --serial "${EGO_VIO_D405_SERIAL:?missing EGO_VIO_D405_SERIAL}" \\
      --imu-port "${EGO_VIO_IMU_BY_ID:?missing EGO_VIO_IMU_BY_ID}" \\
      --duration "$EGO_VIO_RECORD_DURATION_S" \\
      --capture-mode rgb_stereo_ir \\
      --output-root "$RECORD_OUTPUT_ROOT" \\
      --publish-vins --no-preview \\
      9>&- > >(tee "$RUN_DIR/runtime.log" 9>&-) 2>&1 &
  CAPTURE_PID=$!
  wait_with_viewer_supervision "$CAPTURE_PID"
else
  setsid env PYTHONPATH="$RSUSB_PYTHON:$ROOT${PYTHONPATH:+:$PYTHONPATH}" \\
    PYTHONUNBUFFERED=1 \\
    "$PYTHON_BIN" "$ROOT/scripts/run_realtime.py" \\
      --config "$DEVICE_CONFIG" --backend vins_fusion_ros2 --no-record \\
      "${EXTRA_RUNTIME_ARGS[@]}" \\
      9>&- > >(tee "$RUN_DIR/runtime.log" 9>&-) 2>&1 &
  RUNTIME_PID=$!
  wait_with_viewer_supervision "$RUNTIME_PID"
fi
'''
    if source.count(runtime_old) != 1:
        raise RuntimeError("product live runtime launch anchor changed")
    return source.replace(runtime_old, runtime_new, 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    generated = transform(args.source.read_text(encoding="utf-8"))
    args.output.write_text(generated, encoding="utf-8")
    args.output.chmod(0o755)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
