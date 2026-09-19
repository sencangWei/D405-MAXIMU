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

M=[]
for r in rows:
    t=float(r["t_sec"])
    if not (tt[0]<=t<=tt[-1]): continue
    g=cv2.imread(str(M3/"dataset"/r["image"]), cv2.IMREAD_GRAYSCALE)
    k=int(np.argmin(np.abs(tt-t))); j=int(np.argmin(np.abs(rt-t)))
    m=(g>=200).astype(np.uint8)
    n,l,st,_=cv2.connectedComponentsWithStats(m,8)
    blob=sum(st[i,cv2.CC_STAT_AREA] for i in range(1,n) if st[i,cv2.CC_STAT_AREA]>=50)
    gx=cv2.Sobel(g,cv2.CV_32F,1,0,ksize=3); gy=cv2.Sobel(g,cv2.CV_32F,0,1,ksize=3)
    mag=cv2.magnitude(gx,gy)
    M.append([d[k], w[j], blob, np.mean(mag<8)*100, mag.var(), g.mean()])
a=np.array(M); np.save("/tmp/claude-1000/partial.npy", a)
err,wv,blob,lowt,mv,gm = a.T

def pcorr(x,y,z,lab):
    rx=spearmanr(x,y)[0]; rxz=spearmanr(x,z)[0]; ryz=spearmanr(y,z)[0]
    p=(rx-rxz*ryz)/np.sqrt((1-rxz**2)*(1-ryz**2))
    print(f"  {lab:<34} 原始 ρ={rx:+.3f}   控制后偏相关 ρ={p:+.3f}")
    return p

print(f"{len(a)} 帧\n")
print("=== 偏相关: 拆开「动作」与「画面内容」 ===")
print(f"  内容代理 = IR 亮斑总面积 (高=特征多)")
pcorr(wv,err,blob,"角速度 → 误差  (控制 亮斑)")
pcorr(blob,err,wv,"亮斑 → 误差    (控制 角速度)")
print(f"\n  内容代理 = 低纹理像素占比")
pcorr(wv,err,lowt,"角速度 → 误差  (控制 低纹理)")
pcorr(lowt,err,wv,"低纹理 → 误差  (控制 角速度)")

print("\n=== 固定动作量, 看内容的作用 (角速度 >20°/s 的帧) ===")
m=wv>20
print(f"  高动作帧 {m.sum()} 个:")
for lab,sel in (("亮斑少 (<200px)", m&(blob<200)), ("亮斑多 (≥200px)", m&(blob>=200))):
    if sel.sum()<5: print(f"    {lab:<18} n={sel.sum()} 样本太少"); continue
    print(f"    {lab:<18} n={sel.sum():>4}  误差中位 {np.median(err[sel]):6.2f}mm  均值 {err[sel].mean():6.2f}mm")

print("\n=== 固定内容, 看动作的作用 ===")
for lab,sel in (("特征多 (亮斑≥2000px)", blob>=2000), ("特征少 (亮斑<200px)", blob<200)):
    if sel.sum()<5: continue
    lo=sel&(wv<=15); hi=sel&(wv>25)
    sl=f"低动作(≤15°/s) n={lo.sum():>4} 误差中位 {np.median(err[lo]):5.2f}mm" if lo.sum()>=5 else "低动作 n不足"
    sh=f"高动作(>25°/s) n={hi.sum():>4} 误差中位 {np.median(err[hi]):5.2f}mm" if hi.sum()>=5 else "高动作 n不足"
    print(f"  {lab:<24} {sl}   |   {sh}")
