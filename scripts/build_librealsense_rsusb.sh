#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="2.58.2"
SOURCE_COMMIT="5330e760a266d19c7e1e7ec7ada8caa4ff8b3196"
DEPS="$ROOT/.deps"
SOURCE="$DEPS/librealsense-$VERSION-src"
BUILD="$DEPS/librealsense-$VERSION-rsusb-build"
OUTPUT="$DEPS/librealsense-rsusb-$VERSION/python"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
PYTHON_EXT_SUFFIX="$($PYTHON_BIN -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX"))')"
LIBUSB_LIB="$(pkg-config --variable=libdir libusb-1.0)/libusb-1.0.so"
LIBUSB_INC="$(pkg-config --cflags-only-I libusb-1.0 | sed -E 's/^-I([^[:space:]]+).*/\1/')"
if [[ ! -f "$LIBUSB_INC/libusb.h" ]]; then
    echo "[RSUSB] libusb头文件目录无效: $LIBUSB_INC" >&2
    exit 4
fi

mkdir -p "$DEPS"
if [[ ! -d "$SOURCE/.git" ]]; then
    git clone --depth 1 --branch "v$VERSION" \
        https://github.com/IntelRealSense/librealsense.git "$SOURCE"
fi

CURRENT_COMMIT="$(git -C "$SOURCE" rev-parse HEAD)"
if [[ "$CURRENT_COMMIT" != "$SOURCE_COMMIT" ]]; then
    echo "[RSUSB] 源码版本不匹配: $CURRENT_COMMIT" >&2
    echo "[RSUSB] 期望 librealsense v$VERSION: $SOURCE_COMMIT" >&2
    echo "[RSUSB] 请移走 $SOURCE 后重新运行；脚本不会覆盖已有源码。" >&2
    exit 2
fi

cmake -S "$SOURCE" -B "$BUILD" -G Ninja \
    -DFORCE_RSUSB_BACKEND=ON \
    -DBUILD_PYTHON_BINDINGS=ON \
    -DPYTHON_EXECUTABLE="$PYTHON_BIN" \
    -DBUILD_EXAMPLES=OFF \
    -DBUILD_GRAPHICAL_EXAMPLES=OFF \
    -DBUILD_TOOLS=OFF \
    -DBUILD_UNIT_TESTS=OFF \
    -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_BUILD_TYPE=Release \
    -DLIBUSB_LIB="$LIBUSB_LIB" \
    -DLIBUSB_INC="$LIBUSB_INC"
cmake --build "$BUILD" -j "$(nproc)"

mkdir -p "$OUTPUT"
PYREALSENSE_SOURCE="$(find "$BUILD/Release" -maxdepth 1 -type f \
    -name "pyrealsense2${PYTHON_EXT_SUFFIX}*" -print -quit)"
PYRSUTILS_SOURCE="$(find "$BUILD/Release" -maxdepth 1 -type f \
    -name "pyrsutils${PYTHON_EXT_SUFFIX}*" -print -quit)"
if [[ -z "$PYREALSENSE_SOURCE" || -z "$PYRSUTILS_SOURCE" ]]; then
    echo "[RSUSB] 未找到当前Python ABI产物: $PYTHON_EXT_SUFFIX" >&2
    exit 3
fi
cp -- "$PYREALSENSE_SOURCE" "$OUTPUT/pyrealsense2${PYTHON_EXT_SUFFIX}"
cp -- "$PYRSUTILS_SOURCE" "$OUTPUT/pyrsutils${PYTHON_EXT_SUFFIX}"

echo "[RSUSB] 构建完成: $OUTPUT (ABI=$PYTHON_EXT_SUFFIX)"
