#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="2.58.2"
SOURCE_COMMIT="5330e760a266d19c7e1e7ec7ada8caa4ff8b3196"
DEPS="$ROOT/.deps"
SOURCE="$DEPS/librealsense-$VERSION-src"
BUILD="$DEPS/librealsense-$VERSION-rsusb-build"
OUTPUT="$DEPS/librealsense-rsusb-$VERSION/python"

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
    -DPYTHON_EXECUTABLE=/usr/bin/python3 \
    -DBUILD_EXAMPLES=OFF \
    -DBUILD_GRAPHICAL_EXAMPLES=OFF \
    -DBUILD_TOOLS=OFF \
    -DBUILD_UNIT_TESTS=OFF \
    -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_BUILD_TYPE=Release \
    -DLIBUSB_LIB=/usr/lib/x86_64-linux-gnu/libusb-1.0.so \
    -DLIBUSB_INC=/usr/include/libusb-1.0
cmake --build "$BUILD" -j "$(nproc)"

mkdir -p "$OUTPUT"
cp "$BUILD/Release/pyrealsense2.cpython-310-x86_64-linux-gnu.so.$VERSION" \
    "$OUTPUT/pyrealsense2.cpython-310-x86_64-linux-gnu.so"
cp "$BUILD/Release/pyrsutils.cpython-310-x86_64-linux-gnu.so.$VERSION" \
    "$OUTPUT/pyrsutils.cpython-310-x86_64-linux-gnu.so"

echo "[RSUSB] 构建完成: $OUTPUT"
