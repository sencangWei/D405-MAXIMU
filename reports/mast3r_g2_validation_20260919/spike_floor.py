#!/usr/bin/env python3
"""尖峰(max 误差)的逐项地板 —— 「还能压到多少」的直接答案。

把本目录几轮扫描的全部配置汇总, 对每一项取「所有配置里最好的一次 max」。
这就是尾段参数能做到的极限; 若某项的地板仍 > 10mm, 说明尾段再怎么调也过不去。

重要读法警告: 各项的「最好配置」是互相矛盾的(A 项靠关 VINS、B 项靠全借尺度……),
所以「每项各挑最好的配置」是一个**不可部署的上界**, 只能用来回答「还有没有余地」,
不能当成一个可选方案。真正的现役方案是单一配置 A。

用法: python3 spike_floor.py [--json out.json]
"""
import argparse
import json
import statistics as st
from pathlib import Path

SWEEPS = {
    "vsw": "/tmp/claude-1000/stereoab/vsw/vsw.json",       # VINS 权重取舍(§10)
    "spol": "/tmp/claude-1000/stereoab/spol/spol.json",    # 策略七配置(§11)
    "zb": "/tmp/claude-1000/stereoab/zb/spol.json",        # 两开关同关(§10.3)
    "cap": "/tmp/claude-1000/stereoab/cap/spol.json",      # 修正幅度上限(§13.1)
    "scale": "/tmp/claude-1000/stereoab/scale/scale.json",  # 尺度权重(§13.2)
}
LIM_MAX_MM = 10.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()

    per = {}
    for tag, path in SWEEPS.items():
        p = Path(path)
        if not p.is_file():
            print(f"[跳过] {tag}: {path} 不存在")
            continue
        for r in json.loads(p.read_text()):
            key = f"{r['group']}/{r['cand']}"
            for cfg, e in r.get("configs", {}).items():
                s = e.get("score")
                if s:
                    per.setdefault(key, {})[f"{tag}:{cfg}"] = s["mx"]
    ncfg = len({c for d in per.values() for c in d})
    print(f"汇总 {len(per)} 项 / {ncfg} 个(扫描x配置)组合\n")

    hdr = f"{'项':<46}{'地板':>8}{'现役A':>8}{'最差':>8}   地板由哪个配置取得"
    print(hdr)
    print("-" * len(hdr))
    rows = []
    for k in sorted(per):
        d = per[k]
        best = min(d, key=d.get)
        rows.append({"item": k, "floor": d[best], "ceiling": max(d.values()),
                     "best_cfg": best, "all": d})
        a_val = d.get("spol:A_g2_baseline")
        a_txt = f"{a_val:>8.2f}" if a_val is not None else f"{'—':>8}"
        print(f"{k:<46}{d[best]:>8.2f}{a_txt}{max(d.values()):>8.2f}   {best}")

    floors = [r["floor"] for r in rows]
    ok = [r for r in rows if r["floor"] <= LIM_MAX_MM]
    print(f"\n地板 <= {LIM_MAX_MM:.0f}mm 的项: {len(ok)}/{len(rows)}")
    for r in ok:
        print(f"   {r['item']:<46} 地板 {r['floor']:>6.2f}mm")
    print(f"\n地板: 中位 {st.median(floors):.2f}  最好 {min(floors):.2f}  最差 {max(floors):.2f}")

    bad = sorted((r for r in rows if r["floor"] > LIM_MAX_MM), key=lambda x: -x["floor"])
    print(f"\n=> 另外 {len(bad)} 项无论尾段怎么调都过不了 10mm:")
    for r in bad:
        print(f"   {r['item']:<46} 地板 {r['floor']:>6.2f}mm   最差 {r['ceiling']:>6.2f}mm")

    print("\n注意: 上表地板是把多轮扫描里互相矛盾的配置按项挑最优得到的不可部署上界,")
    print("      用来回答还有没有余地; 可部署的单一配置仍是现役 A。")
    if a.json:
        a.json.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
        print(f"\n已写 {a.json}")


if __name__ == "__main__":
    main()
