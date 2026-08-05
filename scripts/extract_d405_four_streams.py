#!/usr/bin/env python3
"""Export portable PNG images from a headless D405 four-stream session."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from pathlib import Path

import cv2
import numpy as np


# spec: (dtype, shape, output_dir, optional cv2 color-conversion code)
RAW4_SPECS = {
    "color_yuyv": (np.uint8, (720, 1280, 2), "rgb", cv2.COLOR_YUV2BGR_YUYV),
    "infrared_left_y8": (np.uint8, (720, 1280), "infrared_left"),
    "infrared_right_y8": (np.uint8, (720, 1280), "infrared_right"),
    "depth_z16": (np.uint16, (720, 1280), "depth"),
}

SUPPORTED3_SPECS = {
    "color_yuyv": (np.uint8, (720, 1280, 2), "rgb", cv2.COLOR_YUV2BGR_YUYV),
    "left_gray_y8": (np.uint8, (720, 1280), "left_gray"),
    "infrared_right_y8": (np.uint8, (720, 1280), "right_ir"),
    "depth_z16": (np.uint16, (720, 1280), "depth"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出D405四路PNG图片")
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--every", type=int, default=1, help="每N帧导出一帧")
    parser.add_argument("--max-frames", type=int, default=0, help="每路上限；0表示全部")
    return parser.parse_args()


def read_exact(stream, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def export_stream(
    camera_dir: Path,
    output: Path,
    specs: dict,
    name: str,
    every: int,
    limit: int,
) -> int:
    dtype, shape, output_name = specs[name][:3]
    convert = specs[name][3] if len(specs[name]) == 4 else None
    raw_path = camera_dir / f"{name}.raw"
    compressed_path = camera_dir / f"{name}.raw.zst"
    compressed = compressed_path.is_file()
    if compressed:
        raw_path = compressed_path
    csv_path = camera_dir / f"{name}_timestamps.csv"
    stream_output = output / output_name
    stream_output.mkdir(parents=True, exist_ok=True)
    exported = 0
    process = None
    raw = None
    try:
        if compressed:
            process = subprocess.Popen(
                ["zstd", "-q", "-dc", str(raw_path)],
                stdout=subprocess.PIPE,
            )
            raw = process.stdout
        else:
            raw = raw_path.open("rb")
        if raw is None:
            raise RuntimeError(f"failed to open {raw_path}")
        with csv_path.open(newline="", encoding="utf-8") as metadata:
            for row_index, row in enumerate(csv.DictReader(metadata)):
                if limit > 0 and exported >= limit:
                    break
                if compressed:
                    size = int(row["raw_size_bytes"])
                    payload = read_exact(raw, size)
                else:
                    offset = int(row["offset_bytes"])
                    size = int(row["size_bytes"])
                    raw.seek(offset)
                    payload = raw.read(size)
                if len(payload) != size:
                    raise IOError(
                        f"{name} frame {row_index}: expected {size} bytes, "
                        f"got {len(payload)}"
                    )
                if row_index % every:
                    continue
                image = np.frombuffer(payload, dtype=dtype).reshape(shape)
                if convert is not None:
                    image = cv2.cvtColor(image, convert)
                filename = f"{exported:06d}_f{int(row['frame_number']):010d}.png"
                if not cv2.imwrite(str(stream_output / filename), image):
                    raise IOError(f"failed to write {stream_output / filename}")
                exported += 1
    finally:
        if process is not None:
            if process.stdout is not None:
                process.stdout.close()
            process.terminate()
            process.wait(timeout=5)
        elif raw is not None:
            raw.close()
    return exported


def main() -> int:
    args = parse_args()
    if args.every < 1:
        raise ValueError("--every must be at least 1")
    session = args.session.resolve()
    camera_dir = session / "camera"
    output = args.output.resolve() if args.output else session / "images"
    output.mkdir(parents=True, exist_ok=True)
    specs = (
        SUPPORTED3_SPECS
        if (camera_dir / "left_gray_y8.raw").is_file()
        or (camera_dir / "left_gray_y8.raw.zst").is_file()
        else RAW4_SPECS
    )
    counts = {
        name: export_stream(camera_dir, output, specs, name, args.every, args.max_frames)
        for name in specs
    }
    report = {
        "source_session": str(session),
        "output": str(output),
        "every": args.every,
        "source_storage": "zstd" if any(camera_dir.glob("*.raw.zst")) else "raw",
        "counts": counts,
        "formats": {
            "rgb": "PNG BGR pixels converted from recorded YUYV",
            "left_gray" if specs is SUPPORTED3_SPECS else "infrared_left": (
                "PNG mono8 derived synchronously from RGB"
                if specs is SUPPORTED3_SPECS
                else "PNG mono8"
            ),
            "right_ir" if specs is SUPPORTED3_SPECS else "infrared_right": "PNG mono8",
            "depth": "PNG uint16, original Z16 values",
        },
    }
    (output / "export_manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
