#!/usr/bin/env python3
"""实测: 在最终输出阶段([4/4] 平滑之前)插入无真值去刺, 能不能把门救回来.

只读现有产物 + 写 /tmp 报告. 不碰流水线.
"""
import csv
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402
from smooth_pose_trajectory import gaussian_smooth_positions  # noqa: E402

SIGMA_S = 0.025  # [4/4] 现用值


def load_pose(path):
    return E.load_trajectory(path)


def mad_sigma(r):
    r = r[np.isfinite(r)]
    med = np.median(r)
    return med, 1.4826 * np.median(np.abs(r - med))


def ref_loo(pos, k):
    n = len(pos); r = np.full(n, np.nan); ref = np.full_like(pos, np.nan)
    for i in range(n):
        a, b = i - k, i + k
        if a >= 0 and b < n:
            w = (i - a) / (b - a)
            ref[i] = pos[a] * (1 - w) + pos[b] * w
            r[i] = np.linalg.norm(pos[i] - ref[i])
    return r, ref


def ref_gauss(t, pos, sigma_s):
    sm, _ = gaussian_smooth_positions(t, pos, sigma_s)
    return np.linalg.norm(pos - sm, axis=1), sm


def ref_median(pos, k):
    n = len(pos); ref = np.full_like(pos, np.nan)
    for i in range(n):
        lo, hi = max(0, i - k), min(n, i + k + 1)
        ref[i] = np.median(pos[lo:hi], axis=0)
    return np.linalg.norm(pos - ref, axis=1), ref


def ref_accel(pos):
    n = len(pos)
    r = np.full(n, np.nan)
    r[1:-1] = np.linalg.norm(pos[2:] - 2 * pos[1:-1] + pos[:-2], axis=1)
    return r, None


def apply_despike(t, pos, quat, kind, mult, **kw):
    if kind == "loo":
        r, ref = ref_loo(pos, kw["k"])
    elif kind == "gauss":
        r, ref = ref_gauss(t, pos, kw["sigma_s"])
    elif kind == "median":
        r, ref = ref_median(pos, kw["k"])
    elif kind == "accel":
        r, ref = ref_accel(pos)
    else:
        raise ValueError(kind)
    med, sig = mad_sigma(r)
    thr = med + mult * sig
    bad = np.isfinite(r) & (r > thr)
    newp, newq = pos.copy(), quat.copy()
    if ref is not None:
        newp[bad] = ref[bad]
    else:  # accel: 用前后各1帧线性插值
        for i in np.where(bad)[0]:
            if 0 < i < len(pos) - 1:
                newp[i] = 0.5 * (pos[i - 1] + pos[i + 1])
            else:
                bad[i] = False
    idx = np.where(bad)[0]
    # 被改的样本: 位姿用最近两个未改样本 slerp
    good = np.where(~bad)[0]
    for i in idx:
        if len(good) < 2:
            break
        j = np.searchsorted(good, i)
        a = good[max(0, j - 1)]; b = good[min(len(good) - 1, j)]
        if a == b:
            newq[i] = quat[a]; continue
        w = (t[i] - t[a]) / (t[b] - t[a])
        ra, rb = Rotation.from_quat(quat[a]), Rotation.from_quat(quat[b])
        newq[i] = (ra * Rotation.from_rotvec(w * (ra.inv() * rb).as_rotvec())).as_quat()
    return newp, newq, idx, thr


def score(t, pos, quat, gt_csv):
    rt, rp, rq = E.load_trajectory(gt_csv)
    inside, valid, interp, iq = E.interpolate_ground_truth(t, rt, rp, rq, 0.1)
    sp = pos[inside][valid]; sq = quat[inside][valid]; G = interp[:, 1:]
    R, tr = E.rigid_align(sp, G)
    ate = np.linalg.norm(sp @ R.T + tr - G, axis=1)
    ar = np.degrees((Rotation.from_quat(iq).inv()
                     * (Rotation.from_matrix(R) * Rotation.from_quat(sq))).magnitude())
    return dict(rmse=np.sqrt(np.mean(ate ** 2)) * 1000,
                p95=np.percentile(ate, 95) * 1000, mx=ate.max() * 1000,
                within10=np.mean(ate <= 0.01) * 100,
                rot=np.sqrt(np.mean(ar ** 2)))


def gate(m):
    f = []
    if m["rmse"] > 10: f.append("rmse")
    if m["p95"] > 10: f.append("p95")
    if m["mx"] > 10: f.append("max")
    if m["within10"] < 95: f.append("within10")
    if m["rot"] > 2.0: f.append("rot")
    return ("PASS" if not f else "FAIL:" + ",".join(f))


def main():
    G = Path(sys.argv[1])
    gt = G / "lighthouse_body_ground_truth.csv"
    base = G / "fusion" / "trajectory_selected_unsmoothed.csv"
    if not base.exists():
        base = G / "trajectory_selected_unsmoothed.csv"
    t, pos, quat = load_pose(base)
    print(f"===== {G.name} =====")
    print(f"输入 {base.name}  {len(t)} 样本")

    sm, _ = gaussian_smooth_positions(t, pos, SIGMA_S)
    m0 = score(t, sm, quat, gt)
    print(f"\n[基线] 仅现用高斯平滑(σ={SIGMA_S}s)")
    print(f"  改动 0 个 | RMSE {m0['rmse']:.3f} P95 {m0['p95']:.3f} "
          f"Max {m0['mx']:.3f} 10mm内 {m0['within10']:.3f}% 姿态 {m0['rot']:.3f}°  -> {gate(m0)}")

    cases = [
        ("D1 留一线性 k=1", "loo", 5, dict(k=1)),
        ("D1 留一线性 k=2", "loo", 5, dict(k=2)),
        ("D1 留一线性 k=3", "loo", 5, dict(k=3)),
        ("D2 高斯偏离 σ=0.025", "gauss", 5, dict(sigma_s=0.025)),
        ("D2 高斯偏离 σ=0.10", "gauss", 5, dict(sigma_s=0.10)),
        ("D3 中值 k=2", "median", 5, dict(k=2)),
        ("D3 中值 k=5", "median", 5, dict(k=5)),
        ("D4 二阶差分(加加速度)", "accel", 5, dict()),
        ("D4 二阶差分 严", "accel", 10, dict()),
    ]
    for name, kind, mult, kw in cases:
        try:
            np_, nq_, idx, thr = apply_despike(t, pos, quat, kind, mult, **kw)
            sm2, _ = gaussian_smooth_positions(t, np_, SIGMA_S)
            m = score(t, sm2, nq_, gt)
            print(f"\n[{name}]  阈值 {thr*1000:.3f} mm  改动 {len(idx)} 个样本")
            print(f"  RMSE {m['rmse']:.3f} P95 {m['p95']:.3f} Max {m['mx']:.3f} "
                  f"10mm内 {m['within10']:.3f}% 姿态 {m['rot']:.3f}°  -> {gate(m)}")
            if len(idx) and len(idx) <= 12:
                print(f"  改动位置: {list(idx)}")
        except Exception as exc:
            print(f"\n[{name}] 失败: {exc}")


if __name__ == "__main__":
    main()
