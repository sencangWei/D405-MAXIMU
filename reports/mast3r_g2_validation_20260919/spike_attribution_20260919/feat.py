import csv, sys
import numpy as np, cv2
from pathlib import Path
from scipy.stats import spearmanr
sys.path.insert(0,'/home/robot/ego_vio_humble/scripts')
import evaluate_slam_ground_truth as E
M3=Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow/20260914_validation_v11_holdout_batch3/group2/fusion/tight/mast3r")
D=M3.parents[2]
rows=list(csv.DictReader(open(M3/"dataset/frames.csv",newline="")))
rt,rp,rq=E.load_trajectory(D/"lighthouse_body_ground_truth.csv")
et,ep,eq=E.load_trajectory(D/"fusion/tight/trajectory_fused.csv")
ins,val,itp,iq=E.interpolate_ground_truth(et,rt,rp,rq,0.1)
R,tr=E.rigid_align(ep[ins][val],itp[:,1:])
d=np.linalg.norm(ep[ins][val]@R.T+tr-itp[:,1:],axis=1)*1000
tt=et[ins][val]
dt=np.diff(rt)
w=np.degrees((E.Rotation.from_quat(rq[:-1]).inv()*E.Rotation.from_quat(rq[1:])).magnitude())/dt
w=np.concatenate([[w[0]],w])
fast=cv2.FastFeatureDetector_create(threshold=25, nonmaxSuppression=True)

M=[]
for r in rows:
    t=float(r["t_sec"])
    if not (tt[0]<=t<=tt[-1]): continue
    g=cv2.imread(str(M3/"dataset"/r["image"]), cv2.IMREAD_GRAYSCALE)
    k=int(np.argmin(np.abs(tt-t))); j=int(np.argmin(np.abs(rt-t)))
    kp=fast.detect(g,None)
    # 特征在画面上的空间散布(去掉聚成一堆的假特征): 5x9 网格里有特征的格子数
    grid=set()
    for p in kp: grid.add((int(p.pt[1]//144), int(p.pt[0]//142)))
    M.append([d[k], w[j], len(kp), len(grid), g.mean(), g.std()])
a=np.array(M); np.save("/tmp/claude-1000/feat.npy",a)
err,wv,nkp,ngr,gm,gs = a.T
def pc(x,y,z,lab):
    rx=spearmanr(x,y)[0]; rxz=spearmanr(x,z)[0]; ryz=spearmanr(y,z)[0]
    p=(rx-rxz*ryz)/np.sqrt((1-rxz**2)*(1-ryz**2))
    print(f"  {lab:<32} 原始 ρ={rx:+.3f}   控制后 {p:+.3f}"); return p
print(f"{len(a)} 帧")
print(f"\n特征数: 中位 {np.median(nkp):.0f}  范围 {nkp.min():.0f}–{nkp.max():.0f}")
print(f"覆盖格子数(5x9=45): 中位 {np.median(ngr):.0f}\n")
print("=== 偏相关 ===")
pc(nkp,err,wv,"特征数 → 误差   (控制 角速度)")
pc(wv,err,nkp,"角速度 → 误差   (控制 特征数)")
pc(ngr,err,wv,"覆盖格子 → 误差 (控制 角速度)")
pc(wv,err,ngr,"角速度 → 误差   (控制 覆盖格子)")
print("\n=== 分层: 固定动作量看特征数 ===")
for lab,m in (("低动作 ≤15°/s",wv<=15),("中动作 15-25°/s",(wv>15)&(wv<=25)),("高动作 >25°/s",wv>25)):
    if m.sum()<20: continue
    q1,q3=np.percentile(nkp[m],33),np.percentile(nkp[m],67)
    lo=m&(nkp<=q1); hi=m&(nkp>=q3)
    print(f"  {lab:<18} n={m.sum():>4} | 特征最少1/3 误差中位 {np.median(err[lo]):5.2f}mm | 特征最多1/3 {np.median(err[hi]):5.2f}mm")
print("\n=== 分层: 固定特征数看动作 ===")
for lab,m in (("特征少 ≤1/3",nkp<=np.percentile(nkp,33)),("特征多 ≥2/3",nkp>=np.percentile(nkp,67))):
    lo=m&(wv<=15); hi=m&(wv>25)
    if lo.sum()<10 or hi.sum()<10: print(f"  {lab:<14} 样本不足"); continue
    print(f"  {lab:<14} 低动作 {np.median(err[lo]):5.2f}mm (n={lo.sum()})  →  高动作 {np.median(err[hi]):5.2f}mm (n={hi.sum()})  变化 {np.median(err[hi])-np.median(err[lo]):+.2f}mm")
