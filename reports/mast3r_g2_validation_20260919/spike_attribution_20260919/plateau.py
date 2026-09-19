import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E
from scipy.spatial.transform import Rotation as Rot, Slerp
WF = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
D = "20260914_validation_v11_holdout_batch3/group2"
gt = WF/D/"lighthouse_body_ground_truth.csv"

def errs(est, drop=None):
    et,ep,eq = E.load_trajectory(est); rt,rp,rq = E.load_trajectory(gt)
    ins,val,itp,iq = E.interpolate_ground_truth(et,rt,rp,rq,0.1)
    t=et[ins][val]; P=ep[ins][val].copy(); Pq=eq[ins][val].copy(); Q=itp[:,1:]
    if drop is not None and len(drop):
        for j in drop:
            a,b = max(j-1,0), min(j+1,len(P)-1)
            u = (t[j]-t[a])/(t[b]-t[a]) if t[b]!=t[a] else 0.5
            P[j] = P[a] + (P[b]-P[a])*u
            Pq[j] = Slerp([t[a],t[b]], Rot.from_quat([Pq[a],Pq[b]]))([t[j]])[0].as_quat()
    R,tr = E.rigid_align(P,Q)
    d = np.linalg.norm(P@R.T+tr-Q,axis=1)*1000
    ang = np.degrees((Rot.from_quat(iq).inv()*(Rot.from_matrix(R)*Rot.from_quat(Pq))).magnitude())
    return t,d,ang

est = WF/D/"fusion/tight/trajectory_fused.csv"
t,d,ang = errs(est)
o = np.argsort(d)[::-1]
print("=== batch3/group2/tight 误差最大的 20 个样本 ===")
print("   排名  样本号   时间(s)    误差(mm)")
for k,i in enumerate(o[:20]):
    print(f"   {k+1:>3}   {i:>6}   {t[i]:.2f}   {d[i]:>7.3f}")
print(f"\n落在 [9.0, 10.4]mm 的样本数: {np.sum((d>=9.0)&(d<=10.4))}")
print(f"落在 [9.5, 10.4]mm 的样本数: {np.sum((d>=9.5)&(d<=10.4))}")
print(f"误差 > 9mm 的最长连续段: ", end="")
bad=np.where(d>9.0)[0]
rs=np.split(bad,np.where(np.diff(bad)!=1)[0]+1) if len(bad) else []
print(max((len(s) for s in rs), default=0), "个样本")
print("\n=== 逐步插补掉最大的 k 个样本后 ===")
print(f"{'k':>4}{'max':>10}{'p95':>8}{'rmse':>8}{'w10':>9}{'rot':>8}   判定")
for k in (0,1,2,3,5,10,20):
    dr = o[:k] if k else None
    t2,dd,aa = errs(est, dr)
    f=[]
    if dd.max()>10: f.append("max")
    if np.percentile(dd,95)>10: f.append("p95")
    if np.sqrt((dd**2).mean())>10: f.append("rmse")
    if (dd<=10).mean()*100<95: f.append("w10")
    if np.sqrt((aa**2).mean())>2.0: f.append("rot")
    print(f"{k:>4}{dd.max():>10.3f}{np.percentile(dd,95):>8.3f}"
          f"{np.sqrt((dd**2).mean()):>8.3f}{(dd<=10).mean()*100:>8.4f}%"
          f"{np.sqrt((aa**2).mean()):>8.3f}   {'PASS ✓' if not f else 'FAIL '+str(f)}")
