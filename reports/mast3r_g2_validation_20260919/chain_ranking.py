#!/usr/bin/env python3
"""全轨迹三链打分: VINS / MASt3R / 融合, 各自独立对齐真值。

目的: 看清"融合"到底是变好还是变坏, 以及融合调参的天花板在哪
      (如果 VINS 独立就能达标, 那"更重地依赖 VINS"是可走的路线)。
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
SURVEY = json.loads(Path("/tmp/claude-1000/stereoab/survey_all.json").read_text())
LIM = dict(rmse=10.0, p95=10.0, mx=10.0, w10=95.0, rot=2.0)


def full(ct, cp, cq, rt, rp, rq):
    inside, valid, interp, iq = E.interpolate_ground_truth(ct, rt, rp, rq, 0.1)
    if valid.sum() < 10:
        return None
    a, aq, G = cp[inside][valid], cq[inside][valid], interp[:, 1:]
    R, tr = E.rigid_align(a, G)
    d = np.linalg.norm(a @ R.T + tr - G, axis=1) * 1000
    ang = np.degrees((E.Rotation.from_quat(iq).inv()
                      * (E.Rotation.from_matrix(R) * E.Rotation.from_quat(aq))).magnitude())
    m = dict(rmse=float(np.sqrt(np.mean(d ** 2))), p95=float(np.percentile(d, 95)),
             mx=float(d.max()), w10=float(np.mean(d <= 10.0) * 100),
             rot=float(np.sqrt(np.mean(ang ** 2))))
    m["fail"] = [k for k in ("rmse", "p95", "mx") if m[k] > LIM[k]] + \
                (["w10"] if m["w10"] < LIM["w10"] else []) + \
                (["rot"] if m["rot"] > LIM["rot"] else [])
    return m


def main():
    rows = []
    print(f"{'组':<44}{'VINS max':>10}{'MASt3R max':>12}{'融合 max':>10}"
          f"   {'VINS':>6}{'MASt3R':>8}{'融合':>6}")
    print("-" * 100)
    for m in sorted(SURVEY, key=lambda r: -r["mx"]):
        g = ROOT / m["group"]
        rt, rp, rq = E.load_trajectory(g / "lighthouse_body_ground_truth.csv")
        ch = {}
        v = g / "docker2_slam" / "vio_corrected_stream.csv"
        if v.is_file():
            t, p, q = E.load_trajectory(v)
            r = full(t, p, q, rt, rp, rq)
            if r:
                ch["VINS"] = r
        for sub in ("sparse", "tight"):
            ms = g / "fusion" / sub / "mast3r" / "trajectory_imu_metric.csv"
            if ms.is_file():
                t, p, q = E.load_trajectory(ms)
                r = full(t, p, q, rt, rp, rq)
                if r:
                    ch["MASt3R"] = r
                break
        et, ep, eq = E.load_trajectory(g / "fusion" / "trajectory_fused.csv")
        r = full(et, ep, eq, rt, rp, rq)
        if r:
            ch["融合"] = r

        row = dict(group=m["group"], chains={k: v for k, v in ch.items()})
        row["best"] = min(ch, key=lambda k: ch[k]["mx"]) if ch else None
        rows.append(row)

        def c(k, w):
            return f"{ch[k]['mx']:>{w}.2f}" if k in ch else " " * (w - 1) + "—"

        def ok(k):
            return "✓" if k in ch and not ch[k]["fail"] else ("✗" if k in ch else "—")

        print(f"{m['group']:<44}{c('VINS',10)}{c('MASt3R',12)}{c('融合',10)}   "
              f"{ok('VINS'):>6}{ok('MASt3R'):>8}{ok('融合'):>6}")

    Path("/tmp/claude-1000/stereoab/chain_ranking.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1))

    print("\n各链达标数(max/P95/RMSE/10mm%/姿态 全过):")
    for k in ("VINS", "MASt3R", "融合"):
        n = sum(1 for r in rows if k in r["chains"] and not r["chains"][k]["fail"])
        tot = sum(1 for r in rows if k in r["chains"])
        print(f"  {k:<8} {n}/{tot}")
    print(f"  逐组最优(事后诸葛, 上界)  "
          f"{sum(1 for r in rows if r['best'] and not r['chains'][r['best']]['fail'])}/{len(rows)}")

    # 融合相对 VINS 的增益
    print("\n融合 vs VINS(全轨迹 max, mm):")
    for r in sorted(rows, key=lambda r: -(r['chains'].get('VINS', {}).get('mx', 0)
                                          - r['chains']['融合']['mx'])):
        if "VINS" not in r["chains"]:
            continue
        dv = r["chains"]["VINS"]["mx"] - r["chains"]["融合"]["mx"]
        print(f"  {r['group']:<46}{r['chains']['VINS']['mx']:>8.2f} → "
              f"{r['chains']['融合']['mx']:>7.2f}   {dv:>+7.2f}")


if __name__ == "__main__":
    main()
