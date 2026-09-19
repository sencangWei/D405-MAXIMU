import sys, json
from pathlib import Path
import numpy as np
sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E
from scipy.spatial.transform import Rotation as Rot

WF = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")

def chain_on_grid(est, gt, gt_t, rq):
    """把一条链的误差插值到 GT 时间栅格上; 返回 (d_grid, ang_grid, valid)"""
    try:
        et, ep, eq = E.load_trajectory(est)
        rt, rp, _ = E.load_trajectory(gt)
    except Exception as e:
        return None
    ins, val, itp, iq = E.interpolate_ground_truth(et, rt, rp, rq, 0.1)
    if val.sum() < 10:
        return None
    P, Q, Pq, t = ep[ins][val], itp[:, 1:], eq[ins][val], et[ins][val]
    R, tr = E.rigid_align(P, Q)
    d = np.linalg.norm(P @ R.T + tr - Q, axis=1) * 1000
    ang = np.degrees((Rot.from_quat(iq).inv()
                      * (Rot.from_matrix(R) * Rot.from_quat(Pq))).magnitude())
    m = (gt_t >= t[0]) & (gt_t <= t[-1])
    dg = np.full(len(gt_t), np.nan); ag = np.full(len(gt_t), np.nan)
    dg[m] = np.interp(gt_t[m], t, d)
    ag[m] = np.interp(gt_t[m], t, ang)
    return dg, ag

def windows(mask, min_len=1):
    idx = np.where(mask)[0]
    if not len(idx): return []
    return np.split(idx, np.where(np.diff(idx) != 1)[0] + 1)

rows = []
for b in sorted(WF.glob("*")):
    if not b.is_dir(): continue
    for g in sorted(b.glob("group*")):
        gt = g / "lighthouse_body_ground_truth.csv"
        if not gt.is_file(): continue
        for sub in ("sparse", "tight"):
            ch = dict(vins=g/"docker2_slam"/"vio_corrected_stream.csv",
                      graph=g/"fusion"/sub/"mast3r"/"trajectory_graph.csv",
                      metric=g/"fusion"/sub/"mast3r"/"trajectory_imu_metric.csv",
                      fused=g/"fusion"/sub/"trajectory_fused.csv")
            if not all(p.is_file() for p in ch.values()): continue
            rt, rp, rq = E.load_trajectory(gt)
            # GT 自身动力学
            dt = np.diff(rt)
            w = np.degrees((Rot.from_quat(rq[:-1]).inv() * Rot.from_quat(rq[1:])).magnitude()) / dt
            spd = np.linalg.norm(np.diff(rp, axis=0), axis=1) / dt
            gtw = np.concatenate([[w[0]], w]); gtspd = np.concatenate([[spd[0]], spd])
            res = {}
            for k, p in ch.items():
                r = chain_on_grid(p, gt, rt, rq)
                if r is not None: res[k] = r
            if "fused" not in res: continue
            df, af = res["fused"]
            base = {k: float(np.nanmedian(v[0])) for k, v in res.items()}
            spin = df > 10.0
            wins = windows(spin)
            gid = f"{b.name}/{g.name}/{sub}"
            rows.append(dict(cell=gid, n=len(rt), base=base,
                             n_spike=int(spin.sum()),
                             spike_pct=float(spin.mean()*100),
                             maxf=float(np.nanmax(df)),
                             wins=[dict(i0=int(w[0]), i1=int(w[-1]), t0=float(rt[w[0]]-rt[0]),
                                        t1=float(rt[w[-1]]-rt[0]), n=len(w)) for w in wins],
                             gtw=gtw, gtspd=gtspd, df=df, af=af,
                             **{f"d_{k}": v[0] for k, v in res.items()}))

print(f"四链齐备且融合有超标的 cell: {sum(1 for r in rows if r['n_spike'])} / {len(rows)}\n")
np.save("/tmp/claude-1000/attrib.npy", rows, allow_pickle=True)

hdr = f"{'cell':<44}{'超标':>6}{'占比':>8}  {'融合':>7}{'VINS':>7}{'图':>7}{'图+度量':>8}   ← 超标窗内误差/该链中位数"
print(hdr); print("-"*len(hdr))
agg = []
for r in rows:
    if not r["n_spike"]:
        continue
    spin = r["df"] > 10.0
    rat = {}
    for k in ("fused", "vins", "graph", "metric"):
        dd = r.get(f"d_{k}")
        if dd is None: rat[k] = None; continue
        v = dd[spin]; v = v[~np.isnan(v)]
        rat[k] = float(np.mean(v) / r["base"][k]) if len(v) and r["base"][k] > 0 else None
    def s(k): return f"{rat[k]:>7.2f}" if rat[k] is not None else f"{'—':>7}"
    print(f"{r['cell']:<44}{r['n_spike']:>6}{r['spike_pct']:>7.2f}%{s('fused'):>7}{s('vins')}{s('graph')}{s('metric'):>8}")
    agg.append(rat)

print("\n=== 归属统计(超标窗内, 各链误差 / 自身中位数) ===")
for k, lab in (("fused","④融合"), ("metric","③图+度量"), ("graph","②MASt3R图"), ("vins","①VINS单链")):
    v = [a[k] for a in agg if a.get(k) is not None]
    if not v: continue
    up = sum(1 for x in v if x > 1.5)
    print(f"  {lab:<12} 中位 {np.median(v):>5.2f}x   抬升(>1.5x)的 cell: {up}/{len(v)}")
