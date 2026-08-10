#!/usr/bin/env python3
"""压缩回放变体: 读原始 db3, 把 IR 流经 HEVC cq18 (与 capture_d405_mp4_inline 完全一致) 转码后再喂 VINS。

用途: 验证内联 MP4 录制的有损 HEVC 压缩是否影响 VINS 双IR 精度。
编码命令与 capture_d405_mp4_inline.py 完全一致:
  ffmpeg rawvideo(gray) -> hevc_nvenc -preset p6 -tune ll -rc vbr -cq 18 -> yuv420p -> mp4
  (灰度源转 yuv420p, 色度子采样对灰度无损, 只压亮度)

实现: 批量转码 (整流 -> 临时 raw -> mp4 -> 解码回 raw), 帧序/数量不变,
时间戳用原始 bag 设备时间, IMU 原样。无流式桥接, 确定性。
用法同 replay_db3_to_ros2.py; 通过 _test_vins_dynamic.py REPLAY_SCRIPT 环境变量指定。
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_db3_to_ros2 as base  # noqa: E402

from sensor_msgs.msg import Image as RosImage  # noqa: E402

# 模块加载时捕获原始迭代器 (之后 __main__ 会替换 base.bag_event_iter, 避免自引用递归)
_ORIG_BAG_ITER = base.bag_event_iter

W, H = 1280, 720
FRAME_BYTES = W * H


def transcode_frames(frames: list[bytes], tmpdir: Path, tag: str) -> list[bytes]:
    """整流批量: raw -> HEVC cq18 (mp4) -> 解码回 Y8。返回与输入等序等长的帧列表。"""
    raw_in = tmpdir / f"{tag}_in.raw"
    mp4 = tmpdir / f"{tag}.mp4"
    raw_out = tmpdir / f"{tag}_out.raw"
    raw_in.write_bytes(b"".join(frames))

    enc_cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{W}x{H}", "-r", "30", "-i", str(raw_in),
        "-vf", "format=yuv420p", "-c:v", "hevc_nvenc", "-preset", "p6",
        "-tune", "ll", "-rc", "vbr", "-cq", "18", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", "-f", "mp4", str(mp4),
    ]
    dec_cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(mp4),
               "-f", "rawvideo", "-pix_fmt", "gray", str(raw_out)]
    subprocess.run(enc_cmd, check=True)
    subprocess.run(dec_cmd, check=True)

    out = raw_out.read_bytes()
    n = len(frames)
    expect = n * FRAME_BYTES
    if len(out) != expect:
        raise RuntimeError(f"{tag}: 解码帧字节数 {len(out)} != 期望 {expect} (输入 {n} 帧)")
    return [out[i * FRAME_BYTES:(i + 1) * FRAME_BYTES] for i in range(n)]


def bag_event_iter_hevc(db3: Path, mode: str):
    # 先把整个模式的图像事件读进内存 (短会话 ~30-45s, 2 流共 ~1.5-2GB, 可接受)
    events = list(_ORIG_BAG_ITER(db3, mode))
    ir_streams = [s for s in base.MODE_STREAMS[mode] if s in ("ir_left", "ir_right")]
    transcoded: dict[str, dict[int, bytes]] = {s: {} for s in ir_streams}

    if ir_streams:
        with tempfile.TemporaryDirectory(prefix="vins_hevc_") as td:
            tmpdir = Path(td)
            for s in ir_streams:
                frames = [bytes(e[2].data) for e in events if e[1] == s]
                if not frames:
                    continue
                print(f"[hevc] 转码 {s}: {len(frames)} 帧 -> HEVC cq18", flush=True)
                dec = transcode_frames(frames, tmpdir, s)
                idx = 0
                for e in events:
                    if e[1] == s:
                        transcoded[s][id(e[2])] = dec[idx]
                        idx += 1

    for t, key, msg in events:
        if key in transcoded:
            m2 = RosImage()
            m2.header = msg.header
            m2.height, m2.width = msg.height, msg.width
            m2.encoding = msg.encoding
            m2.is_bigendian = msg.is_bigendian
            m2.step = msg.step
            m2.data = transcoded[key][id(msg)]
            yield t, key, m2
        else:
            yield t, key, msg


if __name__ == "__main__":
    base.bag_event_iter = bag_event_iter_hevc
    raise SystemExit(base.main())
