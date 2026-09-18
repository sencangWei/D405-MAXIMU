#!/usr/bin/env python3
"""误差是尺度错, 还是形状错?

判据一: 用"相似变换"(带尺度)对齐 vs "刚体变换"(无尺度)对齐, ATE 掉多少。
        掉很多 => 尺度错(可由标定/融合的尺度项修);  几乎不掉 => 形状错(局部几何)。
判据二: ATE 与"离对齐质心的距离"的相关性 + 最优斜率(即隐含的尺度误差)。

注意: Umeyama 的 Sigma 方向写过会静默给出错误结果(以前踩过), 所以先自检
      —— 把 s 强制为 1 时必须逐位复现 E.rigid_align。
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
    """相似变换 p→q (Umeyama)。返回 s, R, t。"""
    mu_p, mu_q = p.mean(0), q.mean(0)
    a, b = p - mu_p, q - mu_q
    Sig = b.T @ a / len(p)                    # 3x3, Sigma = (1/n) Σ (q-μq)(p-μp)^T
    U, D, Vt = np.linalg.svd(Sig)
    S = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        S[-1, -1] = -1
    R = U @ S @ Vt
    var_p = (a ** 2).sum() / len(p)
    s = float(np.trace(np.diag(D) @ S) / var_p)
    t = mu_q - s * (R @ mu_p)
    return s, R, t


def selfcheck():
    rng = np.random.default_rng(0)
    p = rng.normal(size=(60, 3))
    q = p @ E.Rotation.from_euler("xyz", [0.3, -0.2, 0.5]).as_matrix().T + [1, 2, 3]
    s, R, t = sim_align(p, q)
    R2, t2 = E.rigid_align(p, q)
    assert abs(s - 1) < 1e-9, f"尺度自检失败 s={s}"
    assert np.allclose(R, R2, atol=1e-9) and np.allclose(t, t2, atol=1e-9), "刚体自检失败"


def main():
    selfcheck()
    print("自检通过: s=1 时相似对齐逐位复现 rigid_align\n")
    print(f"{'组':<44}{'刚体max':>9}{'相似max':>9}{'尺度s':>9}"
          f"{'刚体rmse':>10}{'相似rmse':>10}{'corr(d)':>9}")
    print("-" * 104)
    rows = []
    for m in sorted(SURVEY, key=lambda r: -r["mx"]):
        g = ROOT / m["group"]
        rt, rp, rq = E.load_trajectory(g / "lighthouse_body_ground_truth.csv")
        et, ep, eq = E.load_trajectory(g / "fusion" / "trajectory_fused.csv")
        inside, valid, interp, iq = E.interpolate_ground_truth(et, rt, rp, rq, 0.1)
        P, G = ep[inside][valid], interp[:, 1:]

        R, t = E.rigid_align(P, G)
        d_rig = np.linalg.norm(P @ R.T + t - G, axis=1) * 1000

        s, R2, t2 = sim_align(P, G)
        d_sim = np.linalg.norm(s * (P @ R2.T) + t2 - G, axis=1) * 1000

        dc = np.linalg.norm(G - G.mean(0), axis=1) * 1000      # 离质心距离(mm)
        corr = float(np.corrcoef(d_rig, dc)[0, 1])
        k = float(np.polyfit(dc, d_rig, 1)[0]) if dc.std() > 1e-9 else float("nan")

        rows.append(dict(group=m["group"], rigid_max=float(d_rig.max()),
                         sim_max=float(d_sim.max()), scale=s,
                         rigid_rmse=float(np.sqrt(np.mean(d_rig ** 2))),
                         sim_rmse=float(np.sqrt(np.mean(d_sim ** 2))),
                         corr_d=corr, slope=float(k)))
        print(f"{m['group']:<44}{d_rig.max():>9.2f}{d_sim.max():>9.2f}{s:>9.5f}"
              f"{np.sqrt(np.mean(d_rig**2)):>10.2f}{np.sqrt(np.mean(d_sim**2)):>10.2f}"
              f"{corr:>9.2f}")

    Path("/tmp/claude-1000/stereoab/scale_test.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1))

    dm = np.array([r["rigid_max"] - r["sim_max"] for r in rows])
    sc = np.array([r["scale"] for r in rows])
    print(f"\n相似对齐使 max 降低: 中位 {np.median(dm):.2f}mm, 均值 {dm.mean():.2f}mm")
    print(f"隐含尺度 s: 中位 {np.median(sc):.5f}  范围 [{sc.min():.5f}, {sc.max():.5f}]"
          f"  => 相对 1 的偏离 {(sc-1).mean()*100:+.3f}%")
    print(f"相似对齐后 max 达标的组: {sum(1 for r in rows if r['sim_max']<=10)}/{len(rows)}")
    print(f"刚体对齐后 max 达标的组: {sum(1 for r in rows if r['rigid_max']<=10)}/{len(rows)}")


if __name__ == "__main__":
    main()
