#!/usr/bin/env python3
"""VINS 的误差是"漂"还是"局部崩"?

漂(单调增长, 与时间/路程强相关) => VIO 发散, 该在融合里降权/剔除
局部崩(某段突起又回来)        => 特定事件, 该做段级鲁棒
同时看 vio_raw vs vio_corrected 是否只差一个刚体量(即"校正"只做了对齐)。
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
SURVEY = json.loads(Path("/tmp/claude-1000/stereoab/survey_all.json").read_text())


def profile(ct, cp, rt, rp, rq):
    inside, valid, interp, iq = E.interpolate_ground_truth(ct, rt, rp, rq, 0.1)
    if valid.sum() < 10:
        return None
    t0 = ct[inside][valid]
    P, G = cp[inside][valid], interp[:, 1:]
    R, t = E.rigid_align(P, G)
    e = np.linalg.norm(P @ R.T + t - G, axis=1) * 1000
    # 累计路程
    s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(G, axis=0), axis=1))])
    # 漂移性: e 与(时间, 路程)的秩相关;  局部性: 最差 10% 样本撑起多少总能量
    def spear(a, b):
        ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
        return float(np.corrcoef(ra, rb)[0, 1])
    e_sorted = np.sort(e)[::-1]
    top10 = float((e_sorted[:max(1, len(e) // 10)] ** 2).sum() / (e ** 2).sum())
    return dict(rmse=float(np.sqrt(np.mean(e ** 2))), mx=float(e.max()),
                corr_t=spear(t0, e), corr_s=spear(s, e), top10_energy=top10,
                span=float(t0[-1] - t0[0]), path_m=float(s[-1]),
                med=float(np.median(e)))


def main():
    print(f"{'组':<44}{'VINS rmse':>10}{'max':>8}{'中位':>7}"
          f"{'ρ(时间)':>9}{'ρ(路程)':>9}{'最差10%能量':>12}  形态")
    print("-" * 108)
    rows = []
    for m in sorted(SURVEY, key=lambda r: -r["mx"]):
        g = ROOT / m["group"]
        v = g / "docker2_slam" / "vio_corrected_stream.csv"
        if not v.is_file():
            continue
        rt, rp, rq = E.load_trajectory(g / "lighthouse_body_ground_truth.csv")
        ct, cp, cq = E.load_trajectory(v)
        p = profile(ct, cp, rt, rp, rq)
        if not p:
            continue
        # 形态判定
        if p["top10_energy"] > 0.55:
            shape = "局部崩"
        elif p["corr_s"] > 0.5 and p["corr_t"] > 0.5:
            shape = "单调漂"
        elif p["corr_s"] > 0.5 or p["corr_t"] > 0.5:
            shape = "弱漂"
        else:
            shape = "无结构"
        p.update(group=m["group"], shape=shape)
        rows.append(p)
        print(f"{m['group']:<44}{p['rmse']:>10.2f}{p['mx']:>8.2f}{p['med']:>7.2f}"
              f"{p['corr_t']:>9.2f}{p['corr_s']:>9.2f}{p['top10_energy']:>12.2f}  {shape}")

    Path("/tmp/claude-1000/stereoab/vins_profile.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1))
    from collections import Counter
    print(f"\n形态统计: {dict(Counter(r['shape'] for r in rows))}")
    print(f"VINS rmse 中位 {np.median([r['rmse'] for r in rows]):.2f}mm, "
          f"中位误差(中位) {np.median([r['med'] for r in rows]):.2f}mm")
    print("ρ=斯皮尔曼秩相关; 最差10%能量=最差10%样本占总平方误差的比例(高=局部崩)")


if __name__ == "__main__":
    main()
