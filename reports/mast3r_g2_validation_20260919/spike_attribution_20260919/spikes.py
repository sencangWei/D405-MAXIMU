import json, sys
from pathlib import Path
import numpy as np
from scipy.stats import spearmanr
sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E
from scipy.spatial.transform import Rotation

WF = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
CELLS = [("20260914_validation_v10_batch","group1",["sparse","tight"]),
         ("20260914_validation_v10_batch","group2",["sparse","tight"]),
         ("20260914_validation_v11_holdout_batch3","group1",["sparse"]),
         ("20260914_validation_v11_holdout_batch3","group2",["sparse","tight"]),
         ("20260915_collective_batch4","group1",["sparse","tight"]),
         ("20260915_collective_batch4","group2",["sparse"]),
         ("20260915_batch5_four_videos","group1",["sparse","tight"]),
         ("20260915_batch5_four_videos","group2",["sparse","tight"]),
         ("20260915_batch5_four_videos","group3",["sparse","tight"]),
         ("20260915_batch5_four_videos","group4",["sparse","tight"])]

def analyse(est, gt):
    est = Path(est)
    if not est.is_file(): return None
    et, ep, eq = E.load_trajectory(est); rt, rp, rq = E.load_trajectory(gt)
    inside, valid, interp, iq = E.interpolate_ground_truth(et, rt, rp, rq, 0.1)
    if valid.sum() < 30: return None
    t = et[inside][valid]; P = ep[inside][valid]; Q = interp[:, 1:]
    R, tr = E.rigid_align(P, Q)
    d = np.linalg.norm(P @ R.T + tr - Q, axis=1) * 1000
    dt = np.diff(t); dt[dt <= 1e-6] = np.median(dt[dt > 1e-6])
    # GT 运动量(用真值算, 与算法无关)
    v = np.linalg.norm(np.diff(Q, axis=0), axis=1) / dt          # m/s
    dq = (Rotation.from_quat(iq[1:]).inv() * Rotation.from_quat(iq[:-1])).magnitude()
    w = np.degrees(dq) / dt                                       # deg/s
    dd = d[1:]
    # 集中度: 最大的 1%/5% 时刻贡献了多少平方误差
    s2 = dd ** 2; tot = s2.sum()
    o = np.argsort(s2)[::-1]
    top1 = s2[o[:max(1, int(0.01*len(dd)))]].sum() / tot
    top5 = s2[o[:max(1, int(0.05*len(dd)))]].sum() / tot
    # 尖峰回合数: 误差 > max(3×中位, 8mm) 的连续段
    thr = max(3*np.median(dd), 8.0)
    hot = dd > thr
    n_ep = int(np.sum(np.diff(hot.astype(int)) == 1) + (1 if hot[0] else 0))
    rv = spearmanr(dd, v).statistic; rw = spearmanr(dd, w).statistic
    return dict(med=float(np.median(dd)), p95=float(np.percentile(dd,95)), mx=float(dd.max()),
                top1=float(top1), top5=float(top5), n_ep=n_ep, hotfrac=float(hot.mean()),
                rho_v=float(rv), rho_w=float(rw),
                v_p95=float(np.percentile(v,95)), w_p95=float(np.percentile(w,95)),
                t_hot=[float(t[1:][i]) for i in np.where(hot)[0]][:0])

rows = []
for b, grp, subs in CELLS:
    g = WF / b / grp; gt = g / "lighthouse_body_ground_truth.csv"
    if not gt.is_file(): continue
    for sub in subs:
        m = g/"fusion"/sub/"mast3r"
        for name, p in (("②MASt3R图", m/"trajectory_graph.csv"),
                        ("④融合输出", g/"fusion"/sub/"trajectory_fused.csv")):
            r = analyse(p, gt)
            if r: rows.append(dict(cell=f"{b}/{grp}/{sub}", chain=name, **r))

print(f"{'cell':<42}{'链':<10}{'中位':>7}{'p95':>7}{'max':>7}"
      f"{'top1%占':>8}{'top5%占':>8}{'尖峰段':>7}{'超标时长':>8}{'ρ速度':>7}{'ρ角速度':>8}")
print("-"*118)
for r in rows:
    print(f"{r['cell']:<42}{r['chain']:<10}{r['med']:>7.2f}{r['p95']:>7.2f}{r['mx']:>7.2f}"
          f"{r['top1']*100:>7.1f}%{r['top5']*100:>7.1f}%{r['n_ep']:>7}{r['hotfrac']*100:>7.1f}%"
          f"{r['rho_v']:>7.2f}{r['rho_w']:>8.2f}")
Path("/tmp/claude-1000/spikes.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))

import statistics as st
print("\n=== 汇总(全部格子) ===")
for ch in ("②MASt3R图","④融合输出"):
    rs = [r for r in rows if r["chain"]==ch]
    if not rs: continue
    print(f"{ch}: top1%占 中位 {st.median(r['top1'] for r in rs)*100:.1f}% | "
          f"top5%占 中位 {st.median(r['top5'] for r in rs)*100:.1f}% | "
          f"尖峰段 中位 {st.median(r['n_ep'] for r in rs):.0f} | "
          f"超标时长 中位 {st.median(r['hotfrac'] for r in rs)*100:.1f}% | "
          f"ρ速度 中位 {st.median(r['rho_v'] for r in rs):+.2f} | "
          f"ρ角速度 中位 {st.median(r['rho_w'] for r in rs):+.2f}")
