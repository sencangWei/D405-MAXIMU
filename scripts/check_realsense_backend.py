#!/usr/bin/env python3
"""Reject the obsolete project-local RealSense backend before acquisition."""

import os
from pathlib import Path

import pyrealsense2 as rs


def main() -> int:
    module_path = Path(rs.__file__).resolve()
    version = getattr(rs, "__version__", "unknown")
    if "/.deps/librealsense-rsusb/build-rsusb/" in str(module_path):
        raise RuntimeError(
            "检测到已停用的项目内置 librealsense 2.13.6 RSUSB 后端: "
            f"{module_path}"
        )
    try:
        version_tuple = tuple(int(part) for part in version.split("."))
    except (AttributeError, ValueError):
        version_tuple = ()
    if version_tuple < (2, 58, 2):
        raise RuntimeError(f"librealsense 版本过旧: {version}")
    if os.environ.get("EGO_REQUIRE_REALSENSE_RSUSB") == "1" and (
        "/librealsense-rsusb-2.58.2/" not in str(module_path)
    ):
        raise RuntimeError(f"没有加载已验收的 RSUSB 2.58.2 后端: {module_path}")
    loaded_libusb = "unknown"
    maps = Path("/proc/self/maps").read_text(encoding="utf-8")
    for line in maps.splitlines():
        if "libusb-1.0.so" in line and "/" in line:
            loaded_libusb = line.split()[-1]
            break
    if loaded_libusb.startswith("/opt/MVS/"):
        raise RuntimeError(
            "RealSense 错误加载了海康 MVS 自带的 libusb: " f"{loaded_libusb}"
        )
    print(
        f"[RealSense] SDK {version}, module={module_path}, "
        f"libusb={loaded_libusb}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
