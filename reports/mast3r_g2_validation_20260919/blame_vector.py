#!/usr/bin/env python3
"""融合出错时, 它跟的是哪条链?

在融合误差最大的那些样本上, 比较各链的"误差向量"方向:
  e_fused 与 e_vins 同向   => 融合继承了 VINS 的误差, 该修 VINS
  e_fused 与 e_mast3r 同向 => 该修前端
  e_fused 与两者都不同向    => 融合自身引入

各链都做一次全局刚体对齐(不用局部重对齐, 那会把要查的东西洗掉)。
加权按 |e_fused|, 让"出错的地方"说话。
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
SURVEY = json.loads(Path("/tmp/claude-1000/stereoab/survey_all.json").read_text())


def err_on_grid(ct, cp, grid, rt, rp, rq):
    """把链路全局对齐到真值, 再在 grid 时刻上返回误差向量(mm)与 |e|。"""
    inside, valid, interp, iq = E.interpolate_ground_truth(ct, rt, rp, rq, 0.1)
    if valid.sum() < 10:
        return None
    t0 = ct[inside][valid]
    P, G = cp[inside][valid], interp[:, 1:]
    R, t = E.rigid_align(P, G)
    e = (P @ R.T + t - G) * 1000
    # 误差向量插值到公共 grid
    ei = np.column_stack([np.interp(grid, t0, e[:, i]) for i in range(3)])
    return ei


def main():
    rows = []
    print(f"{'组':<44}{'cos(e_f,e_V)':>13}{'cos(e_f,e_M)':>13}"
          f"{'  最差50样本':>13}{'':>2}{'谁':>6}")
    print("-" * 104)
    for m in sorted(SURVEY, key=lambda r: -r["mx"]):
        g = ROOT / m["group"]
        v = g / "docker2_slam" / "vio_corrected_stream.csv"
        ms_p = next((g / "fusion" / s / "mast3r" / "trajectory_imu_metric.csv"
                     for s in ("sparse", "tight")
                     if (g / "fusion" / s / "mast3r" / "trajectory_imu_metric.csv").is_file()),
                    None)
        if not (v.is_file() and ms_p):
            continue
        rt, rp, rq = E.load_trajectory(g / "lighthouse_body_ground_truth.csv")
        et, ep, eq = E.load_trajectory(g / "fusion" / "trajectory_fused.csv")
        inside, valid, interp, iq = E.interpolate_ground_truth(et, rt, rp, rq, 0.1)
        grid = et[inside][valid]

        eF = err_on_grid(et, ep, grid, rt, rp, rq)
        eV = err_on_grid(*E.load_trajectory(v)[:2], grid, rt, rp, rq)
        eM = err_on_grid(*E.load_trajectory(ms_p)[:2], grid, rt, rp, rq)
        if eF is None or eV is None or eM is None:
            continue

        nF = np.linalg.norm(eF, axis=1)
        k = np.argsort(-nF)[:50]                      # 融合最差的 50 个样本

        def cos(a, b, idx):
            x, y = a[idx], b[idx]
            nx, ny = np.linalg.norm(x, axis=1), np.linalg.norm(y, axis=1)
            ok = (nx > 1e-6) & (ny > 1e-6)
            if not ok.any():
                return float("nan")
            return float(np.mean((x[ok] * y[ok]).sum(1) / (nx[ok] * ny[ok])))

        cV, cM = cos(eF, eV, k), cos(eF, eM, k)
        who = "VINS" if cV > cM + 0.05 else ("MASt3R" if cM > cV + 0.05 else "都不像")
        rows.append(dict(group=m["group"], cos_vins=cV, cos_mast3r=cM, blame=who,
                         fused_mx=float(nF.max()),
                         vins_mx=float(np.linalg.norm(eV, axis=1).max()),
                         mast3r_mx=float(np.linalg.norm(eM, axis=1).max())))
        print(f"{m['group']:<44}{cV:>13.3f}{cM:>13.3f}{'':>13}  {who:>6}")

    Path("/tmp/claude-1000/stereoab/blame_vector.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1))

    from collections import Counter
    cnt = Counter(r["blame"] for r in rows)
    print(f"\n归因统计: {dict(cnt)}")
    print("\n各链在'融合最差的50个样本'处的误差模长中位(mm):")
    for key, lbl in (("fused_mx", "融合"), ("vins_mx", "VINS"), ("mast3r_mx", "MASt3R")):
        vals = [r[key] for r in rows]
        print(f"  {lbl:<7} 中位 {np.median(vals):>7.2f}   最大 {max(vals):>7.2f}")


if __name__ == "__main__":
    main()
