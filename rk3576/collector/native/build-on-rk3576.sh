#!/usr/bin/env bash
set -euo pipefail

NATIVE_ROOT=$(cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)
RELEASE_ROOT=$(cd -- "$NATIVE_ROOT/.." && pwd)
PYRS_NAME=pyrealsense2.cpython-312-aarch64-linux-gnu.so
PYRS_PATH="$RELEASE_ROOT/runtime/$PYRS_NAME"
PYRS_SONAME="$PYRS_NAME.2.58"
PYRS_SONAME_PATH="$RELEASE_ROOT/runtime/$PYRS_SONAME"
PYTHON_DSO=/usr/lib/aarch64-linux-gnu/libpython3.12.so.1.0

test "$(uname -m)" = aarch64
test -f "$PYRS_PATH"
test -f "$PYTHON_DSO"
if [[ -e "$PYRS_SONAME_PATH" || -L "$PYRS_SONAME_PATH" ]]; then
  test -L "$PYRS_SONAME_PATH"
  test "$(readlink -- "$PYRS_SONAME_PATH")" = "$PYRS_NAME"
else
  ln -s -- "$PYRS_NAME" "$PYRS_SONAME_PATH"
fi
mkdir -p "$NATIVE_ROOT/bin"

g++ -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror \
  -isystem "$NATIVE_ROOT/vendor/librealsense/include" \
  "$NATIVE_ROOT/src/rsusb_probe.cpp" \
  -L"$RELEASE_ROOT/runtime" -Wl,--no-as-needed -l:"$PYRS_NAME" \
  -L/usr/lib/aarch64-linux-gnu -l:libpython3.12.so.1.0 \
  -Wl,--as-needed -Wl,-rpath,'$ORIGIN/../../runtime' \
  -pthread -ldl -o "$NATIVE_ROOT/bin/umi-rsusb-probe"

g++ -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror \
  -isystem "$NATIVE_ROOT/vendor/librealsense/include" \
  "$NATIVE_ROOT/src/umi_collector.cpp" \
  -L"$RELEASE_ROOT/runtime" -Wl,--no-as-needed -l:"$PYRS_NAME" \
  -L/usr/lib/aarch64-linux-gnu -l:libpython3.12.so.1.0 \
  -Wl,--as-needed -Wl,-rpath,'$ORIGIN/../../runtime' \
  -pthread -ldl -o "$NATIVE_ROOT/bin/umi-record-native"

file "$NATIVE_ROOT/bin/umi-rsusb-probe"
file "$NATIVE_ROOT/bin/umi-record-native"
if [[ "${UMI_SKIP_PROBE:-0}" = 1 ]]; then
  printf 'BUILD_PASS_PROBE_SKIPPED (UMI_SKIP_PROBE=1)\n'
else
  "$NATIVE_ROOT/bin/umi-rsusb-probe"
fi
