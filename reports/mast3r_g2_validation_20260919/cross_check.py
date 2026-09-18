#!/usr/bin/env python3
"""三链互校: 到底谁离群?

VINS(滤波式 IMU+相机) 与 MASt3R(学习特征点图 SLAM) 是两个不同算法,
彼此之间近似独立。若它们俩互相贴合得远比各自贴合真值好
  => 真值是那个带独立噪声的量(10mm 门落在真值分辨极限之内)
若它们互相之间也就那么差
  => 我们的估计器才是噪声源, 该继续优化 SLAM

同时给出"融合 vs GT"作对照(融合由前两者派生, 不算独立, 只作参考)。
单位 mm。三组数都用同一个刚体对齐口径, 可直接比大小。
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
SURVEY = json.loads(Path("/tmp/claude-1000/stereoab/survey_all.json").read_text())


def pair(a, b, is_gt=False):
    """a 对齐到 b。返回 rmse/p95/max(mm)。b 若是真值走插值口径。"""
    if is_gt:
        bt, bp, bq = b
        inside, valid, interp, iq = E.interpolate_ground_truth(a[0], bt, bp, bq, 0.1)
        if valid.sum() < 10:
            return None
        P, Q = a[1][inside][valid], interp[:, 1:]
    else:
        tt = np.union1d(a[0], b[0])
        tt = tt[(tt >= max(a[0][0], b[0][0])) & (tt <= min(a[0][-1], b[0][-1]))]
        if len(tt) < 10:
            return None
        P = np.column_stack([np.interp(tt, a[0], a[1][:, i]) for i in range(3)])
        Q = np.column_stack([np.interp(tt, b[0], b[1][:, i]) for i in range(3)])
    R, t = E.rigid_align(P, Q)
    d = np.linalg.norm(P @ R.T + t - Q, axis=1) * 1000
    return dict(rmse=float(np.sqrt(np.mean(d ** 2))),
                p95=float(np.percentile(d, 95)), mx=float(d.max()))


def main():
    rows = []
    print(f"{'组':<44}{'V↔M rmse':>10}{'V↔GT':>8}{'M↔GT':>8}{'融合↔GT':>9}"
          f"   {'V↔M max':>9}{'V↔GT max':>10}{'M↔GT max':>10}")
    print("-" * 112)
    for m in sorted(SURVEY, key=lambda r: -r["mx"]):
        g = ROOT / m["group"]
        gt = E.load_trajectory(g / "lighthouse_body_ground_truth.csv")
        fu = E.load_trajectory(g / "fusion" / "trajectory_fused.csv")
        v = g / "docker2_slam" / "vio_corrected_stream.csv"
        ms_p = None
        for sub in ("sparse", "tight"):
            p = g / "fusion" / sub / "mast3r" / "trajectory_imu_metric.csv"
            if p.is_file():
                ms_p = p
                break
        if not (v.is_file() and ms_p):
            print(f"{m['group']:<44}   (缺 VINS 或 MASt3R, 跳过)")
            continue
        V = E.load_trajectory(v)
        M = E.load_trajectory(ms_p)

        r = dict(group=m["group"], vm=pair(V, M), vgt=pair(V, gt, True),
                 mgt=pair(M, gt, True), fgt=pair(fu, gt, True))
        rows.append(r)

        def c(k, f, w, p=2):
            return f"{r[k][f]:>{w}.{p}f}" if r[k] else " " * (w - 1) + "—"

        print(f"{m['group']:<44}{c('vm','rmse',10)}{c('vgt','rmse',8)}"
              f"{c('mgt','rmse',8)}{c('fgt','rmse',9)}   "
              f"{c('vm','mx',9)}{c('vgt','mx',10)}{c('mgt','mx',10)}")

    Path("/tmp/claude-1000/stereoab/cross_check.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1))

    for f in ("rmse", "p95", "mx"):
        vm = np.array([r["vm"][f] for r in rows if r["vm"]])
        vg = np.array([r["vgt"][f] for r in rows if r["vgt"]])
        mg = np.array([r["mgt"][f] for r in rows if r["mgt"]])
        fg = np.array([r["fgt"][f] for r in rows if r["fgt"]])
        print(f"\n{f:>4}:  V↔M 中位 {np.median(vm):>7.2f}   V↔GT 中位 {np.median(vg):>7.2f}"
              f"   M↔GT 中位 {np.median(mg):>7.2f}   融合↔GT 中位 {np.median(fg):>7.2f}")
    print("\n若 V↔M 中位 << V↔GT 且 << M↔GT  => 两条独立链抱团, 真值离群。")


if __name__ == "__main__":
    main()
