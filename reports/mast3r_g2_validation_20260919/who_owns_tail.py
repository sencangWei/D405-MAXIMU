#!/usr/bin/env python3
"""尾巴归谁? 在峰值窗口内, 把三条链路各自独立对齐真值, 比 ATE。

  VINS 也差  => 问题在前端之前(IMU / 内参 / 时间), 不是 MASt3R 也不是融合
  MASt3R 独差 => 前端(点图/位姿求解)
  只有融合差 => 融合尾段
另附: 各链路相对真值的最优滞后 τ(峰值窗内), 看尾巴是不是"慢半拍"。
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
SURVEY = json.loads(Path("/tmp/claude-1000/stereoab/survey_all.json").read_text())


def win_ate(ct, cp, rt, rp, rq, lo, hi):
    """把链路在 [lo,hi] 时间窗内对齐真值, 返回该窗 ATE(mm)。"""
    m = (ct >= lo) & (ct <= hi)
    if m.sum() < 5:
        return None
    q = cp[m]
    inside, valid, interp, iq = E.interpolate_ground_truth(
        ct[m], rt, rp, rq, 0.1)
    if valid.sum() < 5:
        return None
    a, G = q[inside][valid], interp[:, 1:]
    R, tr = E.rigid_align(a, G)
    d = np.linalg.norm(a @ R.T + tr - G, axis=1) * 1000
    return dict(mx=float(d.max()), rmse=float(np.sqrt(np.mean(d ** 2))))


def best_lag(ct, cp, rt, rp, tm, span=0.15):
    """在 tm 附近的窗内扫滞后 τ, 返回使位移最贴合的 τ(ms)。"""
    best, bm = 0.0, None
    for tau in np.arange(-span, span + 1e-9, 0.01):
        # 用位移向量而非绝对位置(去掉了对齐这一层)
        m = (ct >= tm - 0.5) & (ct <= tm + 0.5)
        if m.sum() < 8:
            return None
        t = ct[m]; p = cp[m]
        q = np.column_stack([np.interp(t + tau, rt, rp[:, i]) for i in range(3)])
        d = np.linalg.norm(np.diff(p, axis=0) - np.diff(q, axis=0), axis=1).mean() * 1000
        if bm is None or d < bm:
            bm, best = d, float(tau)
    return (best * 1000, bm) if bm is not None else None


def main():
    out = []
    print(f"{'组':<44}{'窗内 VINS':>11}{'MASt3R':>9}{'融合':>8}   {'滞后 VINS':>11}"
          f"{'MASt3R':>9}{'融合':>8}")
    print("-" * 112)
    for m in sorted(SURVEY, key=lambda r: -r["mx"]):
        g = ROOT / m["group"]
        rt, rp, rq = E.load_trajectory(g / "lighthouse_body_ground_truth.csv")
        et, ep, eq = E.load_trajectory(g / "fusion" / "trajectory_fused.csv")
        k = m["peak_idx"]
        inside, valid, interp, iq = E.interpolate_ground_truth(et, rt, rp, rq, 0.1)
        t0 = et[inside][valid]
        tm = float(t0[k])
        lo, hi = tm - 1.0, tm + 1.0

        chains = {}
        v = g / "docker2_slam" / "vio_corrected_stream.csv"
        if v.is_file():
            t, p, _ = E.load_trajectory(v)
            chains["VINS"] = (t, p)
        for sub in ("sparse", "tight"):
            ms = g / "fusion" / sub / "mast3r" / "trajectory_imu_metric.csv"
            if ms.is_file():
                t, p, _ = E.load_trajectory(ms)
                chains["MASt3R"] = (t, p)
                break
        chains["融合"] = (et, ep)

        ate, lag = {}, {}
        for nm, (ct, cp) in chains.items():
            a = win_ate(ct, cp, rt, rp, rq, lo, hi)
            ate[nm] = a["mx"] if a else None
            L = best_lag(ct, cp, rt, rp, tm)
            lag[nm] = L[0] if L else None

        row = dict(group=m["group"], tm=tm, mx=m["mx"], ate=ate, lag=lag)
        out.append(row)
        def c(d, k, w, p=2):
            v = d.get(k)
            return f"{v:>{w}.{p}f}" if v is not None else " " * (w - 1) + "—"
        print(f"{m['group']:<44}{c(ate,'VINS',11)}{c(ate,'MASt3R',9)}"
              f"{c(ate,'融合',8)}   {c(lag,'VINS',11,0)}{c(lag,'MASt3R',9,0)}"
              f"{c(lag,'融合',8,0)}")

    Path("/tmp/claude-1000/stereoab/who_owns_tail.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1))
    print("\n左半: 峰值±1s 窗内各链路独立对齐后的 ATE 最大值(mm)")
    print("右半: 峰值处各链路相对真值的最优滞后(ms), 正=链路落后真值")


if __name__ == "__main__":
    main()
