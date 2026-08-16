#!/usr/bin/env python3
"""测试 AprilGrid 图片能否被识别。

用法:
  python scripts/test_aprilgrid_image.py --img path/to/aprilgrid.png
"""
import argparse
import sys
from pathlib import Path

import cv2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", required=True, help="AprilGrid 图片路径")
    args = ap.parse_args()

    img = cv2.imread(args.img, cv2.IMREAD_GRAYSCALE)
    if img is None:
        print(f"无法读取图片: {args.img}")
        return 1

    try:
        from aprilgrid import Detector
    except ImportError:
        print("pip install aprilgrid")
        return 1

    det = Detector("t36h11")
    dets = det.detect(img)
    ids = sorted([int(d.tag_id) for d in dets])
    print(f"识别到 {len(ids)} 个 tag")
    print(f"IDs: {ids[:20]}{'...' if len(ids) > 20 else ''}")
    if len(ids) >= 36:
        print("OK: 全部 36 个 tag 可识别")
    else:
        print(f"WARNING: 只识别了 {len(ids)}/36")
    return 0


if __name__ == "__main__":
    sys.exit(main())
