#!/usr/bin/env python3
"""跨流丢帧同步性测试: 丢帧是同步命中三路(对SLAM无害)还是单路独立丢(破坏双目)。

跑 60s 三路流, 对每个 frameset 记录 (color, ir1, ir2) 帧号,
然后分析:
  - 每路帧号间隙 (中间丢的)
  - 两路 IR 的间隙位置是否完全一致 (同步丢) -> 双目仍配对
  - 若 IR1 有某帧而 IR2 没有 (错位) -> 双目帧对断裂, 对 stereo SLAM 有影响

结论:
  - IR1/IR2 间隙位置 100% 一致 -> 丢帧对双目无损 (同一瞬间两目都丢, SLAM 可跳过)
  - 出现单侧丢 -> 该帧双目失配, 需要处理
"""
from __future__ import annotations

import argparse
import time
from collections import defaultdict

import pyrealsense2 as rs

SERIAL = "260322273737"
W, H, FPS = 1280, 720, 30


def gaps(seq):
    """seq: 有序帧号列表. 返回缺的帧号集合."""
    if not seq:
        return set()
    missing = set()
    for a, b in zip(seq, seq[1:]):
        for n in range(a + 1, b):
            missing.add(n)
    return missing


def test(duration: float, streams: list):
    """streams: 启用的流. 只对启用的流统计."""
    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_device(SERIAL)
    want = set(streams)
    if "color" in want:
        config.enable_stream(rs.stream.color, W, H, rs.format.yuyv, FPS)
    if "ir1" in want:
        config.enable_stream(rs.stream.infrared, 1, W, H, rs.format.y8, FPS)
    if "ir2" in want:
        config.enable_stream(rs.stream.infrared, 2, W, H, rs.format.y8, FPS)
    pipeline.start(config)
    for _ in range(30):
        pipeline.wait_for_frames()

    seq = defaultdict(list)   # key -> list of frame numbers in order
    first_ts = None

    t0 = time.time()
    try:
        while time.time() - t0 < duration:
            f = pipeline.wait_for_frames(timeout_ms=2000)
            if first_ts is None:
                first_ts = f.get_timestamp()
            for key, fr in (
                ("color", f.get_color_frame()),
                ("ir1", f.get_infrared_frame(1)),
                ("ir2", f.get_infrared_frame(2)),
            ):
                if key in want and fr is not None:
                    seq[key].append(fr.get_frame_number())
    except KeyboardInterrupt:
        pass
    elapsed = time.time() - t0
    pipeline.stop()

    print(f"\n===== 跨流丢帧同步性 ({'+'.join(streams)}, {duration:.0f}s) =====")
    # 逐流统计
    miss = {}
    for key, name in (("color", "Color"), ("ir1", "IR1"), ("ir2", "IR2")):
        if key not in want:
            continue
        s = seq[key]
        if not s:
            print(f"  {name}: 无帧")
            continue
        m = gaps(s)
        miss[key] = m
        span = s[-1] - s[0]
        print(f"  {name}: 收到{len(s)}帧 跨度{span} 丢{len(m)} 速率{span/elapsed:.2f}Hz")

    # 同步性: 两路 IR 的丢帧位置
    if "ir1" in miss and "ir2" in miss:
        m1, m2 = miss["ir1"], miss["ir2"]
        inter = len(m1 & m2)
        print(f"\n  IR1丢{len(m1)}处 IR2丢{len(m2)}处 交集{inter}处")
        if len(m1) or len(m2):
            print(f"  同步率 = 交集/最大 = {inter/max(len(m1),len(m2))*100:.1f}%")
            if m1 ^ m2:
                only1 = sorted(m1 - m2)
                only2 = sorted(m2 - m1)
                print(f"  仅IR1丢: {only1[:10]}{'...' if len(only1)>10 else ''}")
                print(f"  仅IR2丢: {only2[:10]}{'...' if len(only2)>10 else ''}")
            if m1 == m2:
                print("  -> 两目丢帧位置完全一致: 双目帧对仍配对, 对 stereo SLAM 无害")
        else:
            print("  -> 双 IR 零丢帧")

    # frameset 完整性: 每帧三路是否都齐
    if want >= {"ir1", "ir2"}:
        s1, s2 = set(seq["ir1"]), set(seq["ir2"])
        if s1 and s2:
            # 以 IR1 为基准, 看 IR1 出现的帧 IR2 是否也在 (帧号同值 = 同瞬间)
            solo1 = sum(1 for n in s1 if n not in s2)
            solo2 = sum(1 for n in s2 if n not in s1)
            print(f"\n  帧号对 IR1-有-IR2-无: {solo1} | IR2-有-IR1-无: {solo2}")
            if solo1 == 0 and solo2 == 0:
                print("  -> 双 IR 帧号完全一一对应: 双目完全同步")
            else:
                print(f"  -> 存在 {solo1+solo2} 帧双目错位 (破坏立体匹配)")

    return miss


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=60.0)
    args = ap.parse_args()
    test(args.duration, ["color", "ir1", "ir2"])


if __name__ == "__main__":
    main()
