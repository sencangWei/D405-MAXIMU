#!/usr/bin/env python3
"""VINS 是不是"非度量尺度"的?

VINS 误差中位≈RMSE 且无结构 => 不像漂移, 像整体形状/尺度不对。
测试: 给 VINS 加尺度自由度(相似对齐), 看误差塌不塌。
  塌很多 => VINS 输出不是度量尺度, 融合里必须做尺度校正
  不塌   => VINS 是度量正确的, 只是形状差

对照: 同样测试 MASt3R 与融合, 确认基线。
附带: 三者的隐含尺度 s 与轨迹实际尺寸(排除"轨迹太小所以 mm 看着大")。
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
SURVEY = json.loads(Path("/tmp/claude-1000/stereoab/survey_all.json").read_text())


def sim_align(p, q):
    mu_p, mu_q = p.mean(0), q.mean(0)
    a, b = p - mu_p, q - mu_q
    Sig = b.T @ a / len(p)
    U, D, Vt = np.linalg.svd(Sig)
    S = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        S[-1, -1] = -1
    R = U @ S @ Vt
    s = float(np.trace(np.diag(D) @ S) / ((a ** 2).sum() / len(p)))
    return s, R, mu_q - s * (R @ mu_p)


def both(ct, cp, rt, rp, rq):
    inside, valid, interp, iq = E.interpolate_ground_truth(ct, rt, rp, rq, 0.1)
    if valid.sum() < 10:
        return None
    P, G = cp[inside][valid], interp[:, 1:]
    R, t = E.rigid_align(P, G)
    dr = np.linalg.norm(P @ R.T + t - G, axis=1) * 1000
    s, R2, t2 = sim_align(P, G)
    ds = np.linalg.norm(s * (P @ R2.T) + t2 - G, axis=1) * 1000
    return dict(r_rmse=float(np.sqrt(np.mean(dr ** 2))), r_mx=float(dr.max()),
                s_rmse=float(np.sqrt(np.mean(ds ** 2))), s_mx=float(ds.max()),
                scale=s, ext_m=float(np.linalg.norm(G.max(0) - G.min(0))))


def main():
    print(f"{'组':<42}{'链':<8}{'刚体rmse':>9}{'相似rmse':>9}{'隐含s':>9}"
          f"{'刚体max':>9}{'相似max':>9}{'真值尺寸m':>10}")
    print("-" * 108)
    rows = []
    for m in sorted(SURVEY, key=lambda r: -r["mx"]):
        g = ROOT / m["group"]
        rt, rp, rq = E.load_trajectory(g / "lighthouse_body_ground_truth.csv")
        cands = {"融合": g / "fusion" / "trajectory_fused.csv"}
        v = g / "docker2_slam" / "vio_corrected_stream.csv"
        if v.is_file():
            cands["VINS"] = v
        for sub in ("sparse", "tight"):
            p = g / "fusion" / sub / "mast3r" / "trajectory_imu_metric.csv"
            if p.is_file():
                cands["MASt3R"] = p
                break
        for nm, path in cands.items():
            ct, cp, cq = E.load_trajectory(path)
            r = both(ct, cp, rt, rp, rq)
            if not r:
                continue
            r.update(group=m["group"], chain=nm)
            rows.append(r)
            print(f"{m['group']:<42}{nm:<8}{r['r_rmse']:>9.2f}{r['s_rmse']:>9.2f}"
                  f"{r['scale']:>9.4f}{r['r_mx']:>9.2f}{r['s_mx']:>9.2f}"
                  f"{r['ext_m']:>10.2f}")

    Path("/tmp/claude-1000/stereoab/vins_scale_test.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1))

    print("\n按链汇总(相似对齐使 rmse 降低的比例):")
    for nm in ("VINS", "MASt3R", "融合"):
        rs = [r for r in rows if r["chain"] == nm]
        if not rs:
            continue
        drop = np.array([(r["r_rmse"] - r["s_rmse"]) / max(r["r_rmse"], 1e-9) for r in rs])
        sc = np.array([r["scale"] for r in rs])
        print(f"  {nm:<8} rmse 降幅 中位 {np.median(drop)*100:>6.1f}%   "
              f"隐含 s 中位 {np.median(sc):.4f}  范围 [{sc.min():.4f}, {sc.max():.4f}]")


if __name__ == "__main__":
    main()
