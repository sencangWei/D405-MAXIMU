#!/usr/bin/env python3
"""Make the calibration converter's camera timebase diagnostic truthful."""

from pathlib import Path


TARGET = Path("/home/robot/ego_vio_humble/scripts/convert_to_kalibr_bag.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one source block, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    text = TARGET.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '    cam_rows = read_camera_ts(unit_dir / "camera_ts.csv")',
        '    cam_rows = read_camera_ts(unit_dir / "camera_ts.csv")\n'
        '    print("相机时间基准: ts_mono (产品RealSense global_time映射)")',
        "camera timebase report",
    )
    text = replace_once(
        text,
        'print(f"相机去抖: 实测 {cinfo[\'rate_hz\']:.1f}fps, 送达抖动 σ={cinfo[\'sigma_ms\']:.2f}ms, "',
        'print(f"相机帧序号拟合: 实测 {cinfo[\'rate_hz\']:.1f}fps, 全段时基残差 σ={cinfo[\'sigma_ms\']:.2f}ms, "',
        "camera timing label",
    )
    text = replace_once(
        text,
        'print("!! 相机送达抖动过大(>8ms), 建议换主板后置 USB 口/减少 USB 设备后重采")',
        'print("!! 相机全段时基残差过大(>8ms)，本轮禁止用于联合标定；请暂停异常系统校时后重采")',
        "camera timing warning",
    )
    TARGET.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
