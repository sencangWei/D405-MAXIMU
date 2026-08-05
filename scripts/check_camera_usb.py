#!/usr/bin/env python3
"""Check current RealSense D405 USB speed.

Windows Device Manager hints:
  - D405 under "USB 3.0 eXtensible Host Controller" = USB3.0
  - D405 under "USB 2.0" / "Enhanced Host Controller" = USB2.0

This script uses pyrealsense2 to enumerate devices and tries to open the RGB stream:
  - USB3.0: RGB stream opens at the requested resolution/fps
  - USB2.1: RGB stream usually fails or falls back

Usage:
  python scripts/check_camera_usb.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ego_vio.config import load_config


def main():
    import pyrealsense2 as rs

    ctx = rs.context()
    devices = ctx.query_devices()
    if len(devices) == 0:
        print("No RealSense device found")
        return 1

    print(f"Found {len(devices)} RealSense device(s):")
    for dev in devices:
        name = dev.get_info(rs.camera_info.name)
        serial = dev.get_info(rs.camera_info.serial_number)
        print(f"\n  {name}")
        print(f"  serial: {serial}")

        sensors = dev.query_sensors()
        sensor_names = [s.get_info(rs.camera_info.name) for s in sensors]
        print(f"  sensors: {sensor_names}")

        has_rgb_enum = any("RGB" in n for n in sensor_names)
        has_stereo = any("Stereo" in n or "Depth" in n for n in sensor_names)

        # Some firmware/USB combinations do not enumerate RGB separately.
        if has_rgb_enum and has_stereo:
            print("  sensor enum: USB3.0 (RGB + Depth)")
        elif has_stereo and not has_rgb_enum:
            print("  sensor enum: RGB not enumerated separately (D405 may merge into Stereo Module)")
        else:
            print("  sensor enum: unclear")

    # Try to start the configured pipeline to verify RGB actually works.
    cfg = load_config()
    unit = cfg.units[0]
    pipe = rs.pipeline()
    rc = rs.config()
    if unit.camera.serial:
        rc.enable_device(unit.camera.serial)
    rc.enable_stream(rs.stream.color, unit.camera.width, unit.camera.height,
                     rs.format.bgr8, unit.camera.fps)
    try:
        profile = pipe.start(rc)
        color_profile = profile.get_stream(rs.stream.color)
        fmt = color_profile.format()
        fps = color_profile.fps()
        print(f"\nActual RGB stream: {unit.camera.width}x{unit.camera.height} @ {fps}fps, format {fmt}")
        print("Conclusion: D405 is running in USB3.0 mode [OK]")
        pipe.stop()
    except Exception as e:
        print(f"\nCannot start RGB stream: {e}")
        print("Conclusion: D405 is running in USB2.1 mode [FAIL]")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
