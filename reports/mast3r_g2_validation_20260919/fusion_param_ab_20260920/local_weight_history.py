#!/usr/bin/env python3
"""`local_weight` 是什么时候变成 0 的？

用户的问题：「之前没遇到过 [8/9][9/9] 空转这回事啊」。
⇒ 把盘上**全部** fusion_report.json 按 mtime 排序，看
`docker2_local_weight` / `docker2_scale_weight` / `adaptive_local_weight` /
`effective_local_weight_max` 的历时轨迹，找出 0 首次出现的时刻。

只读产物，不跑管线。
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path("/home/robot/ego_vio_humble/reports")


def pick(d: dict, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def main():
    rows = []
    for p in ROOT.rglob("fusion_report.json"):
        try:
            mtime = p.stat().st_mtime
            d = json.loads(p.read_text())
        except Exception:
            continue
        f = d.get("fusion", d)
        rows.append((mtime, p, {
            "lw": pick(f, "local_weight"),
            "sw": pick(f, "docker2_scale_weight"),
            "ada": pick(f, "adaptive_local_weight"),
            "eff_max": pick(f, "effective_local_weight_max"),
            "eff_med": pick(f, "effective_local_weight_median"),
        }))
    rows.sort(key=lambda r: r[0])
    print(f"共 {len(rows)} 份 fusion_report.json\n")

    # 压缩成「参数指纹变化点」时间轴
    print("=" * 118)
    print("参数指纹变化时间轴（只在该组合首次出现时打印）")
    print("=" * 118)
    print(f"{'首次出现':<20}{'lw':>8}{'sw':>8}{'adaptive':>10}{'eff_max':>10}  目录")
    print("-" * 118)
    seen = {}
    for mtime, p, v in rows:
        key = (v["lw"], v["sw"], v["ada"])
        if key in seen:
            seen[key] += 1
            continue
        seen[key] = 1
        import datetime
        ts = datetime.datetime.fromtimestamp(mtime).strftime("%m-%d %H:%M:%S")
        rel = str(p).replace(str(ROOT) + "/", "")
        print(f"{ts:<20}{str(v['lw']):>8}{str(v['sw']):>8}"
              f"{str(v['ada']):>10}{str(v['eff_max']):>10}  {rel}")
    print()
    print("=" * 118)
    print("各指纹的总数")
    print("=" * 118)
    for key, n in sorted(seen.items(), key=lambda kv: -kv[1]):
        print(f"  lw={str(key[0]):<6} sw={str(key[1]):<6} "
              f"adaptive={str(key[2]):<6}  →  {n:>4} 份")


if __name__ == "__main__":
    main()