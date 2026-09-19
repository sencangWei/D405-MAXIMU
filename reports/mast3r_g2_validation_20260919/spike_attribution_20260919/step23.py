import sys, numpy as np
from pathlib import Path
sys.path.insert(0,'/home/robot/ego_vio_humble/scripts')
import evaluate_slam_ground_truth as E
from scipy.spatial.transform import Rotation as Rot
WF=Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")

def score(est, gt):
    et,ep,eq=E.load_trajectory(est); rt,rp,rq=E.load_trajectory(gt)
    ins,val,itp,iq=E.interpolate_ground_truth(et,rt,rp,rq,0.1)
    if val.sum()<10: return None
    P,Q,Pq=ep[ins][val],itp[:,1:],eq[ins][val]; t=et[ins][val]
    out={}
    # 刚性(不看尺度): 形状
    R,tr=E.rigid_align(P,Q)
    d=np.linalg.norm(P@R.T+tr-Q,axis=1)*1000
    out["shape_max"]=float(d.max()); out["shape_med"]=float(np.median(d))
    # 相似(含尺度)
    s,R2,t2=E.similarity_align(P,Q)
    out["scale"]=float(s)
    out["scaled_max"]=float(np.linalg.norm(s*P@R2.T+t2-Q,axis=1).max()*1000)
    out["d"]=d; out["t"]=t; out["P"]=P
    return out

print(f"{'cell':<44}{'尺度':>8}  {'ⓐraw':>8}{'②图':>8}{'③度量':>8}{'④融合':>8}   (尺度无关 max, mm)")
print("-"*96)
agg=[]
for b in sorted(WF.glob("*")):
    if not b.is_dir(): continue
    for g in sorted(b.glob("group*")):
        if not (g/"lighthouse_body_ground_truth.csv").is_file(): continue
        gt=g/"lighthouse_body_ground_truth.csv"
        for sub in ("sparse","tight"):
            M=g/"fusion"/sub/"mast3r"
            cands={"raw":M/"trajectory_frames.csv","graph":M/"trajectory_graph.csv",
                   "metric":M/"trajectory_imu_metric.csv","fused":g/"fusion"/sub/"trajectory_fused.csv"}
            if not all(p.is_file() for p in cands.values()): continue
            sc={k:score(v,gt) for k,v in cands.items()}
            if any(v is None for v in sc.values()): continue
            gid=f"{b.name}/{g.name}/{sub}"
            print(f"{gid:<44}{sc['metric']['scale']:>8.4f}  "
                  f"{sc['raw']['shape_max']:>8.2f}{sc['graph']['shape_max']:>8.2f}"
                  f"{sc['metric']['shape_max']:>8.2f}{sc['fused']['shape_max']:>8.2f}")
            agg.append(dict(cell=gid, **{f"{k}_max":sc[k]['shape_max'] for k in sc},
                            **{f"{k}_scale":sc[k]['scale'] for k in sc}))
            if sub=="tight" and "batch3/group2" in gid:
                np.save("/tmp/claude-1000/step23_d.npy",
                        np.array([sc['graph']['t'], sc['graph']['d'], sc['metric']['d']], dtype=object),
                        allow_pickle=True)
print("-"*96)
for k,lab in (("raw","ⓐ MASt3R raw"),("graph","② MASt3R 图"),("metric","③ 图+IMU度量"),("fused","④ 融合")):
    v=[a[f"{k}_max"] for a in agg]
    s=[a[f"{k}_scale"] for a in agg]
    print(f"  {lab:<16} 尺度无关 max 中位 {np.median(v):6.2f}mm   相似尺度 中位 {np.median(s):.4f} "
          f"(范围 {min(s):.3f}–{max(s):.3f})   <1.0 的 {sum(1 for x in s if x<1.0)}/{len(s)}")
