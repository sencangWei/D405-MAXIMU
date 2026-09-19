import csv, sys
import numpy as np, cv2
from pathlib import Path
from scipy.stats import spearmanr
sys.path.insert(0,'/home/robot/ego_vio_humble/scripts')
import evaluate_slam_ground_truth as E

M3=Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow/20260914_validation_v11_holdout_batch3/group2/fusion/tight/mast3r")
D=M3.parents[2]
rows=list(csv.DictReader(open(M3/"dataset/frames.csv",newline="")))
print(f"输入帧 {len(rows)} 张, 首帧 t={rows[0]['t_sec']}")

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
    p=M3/"dataset"/r["image"]
    g=cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
    if g is None: continue
    t=float(r["t_sec"])
    if not (tt[0]<=t<=tt[-1]): continue
    k=int(np.argmin(np.abs(tt-t)))
    j=int(np.argmin(np.abs(rt-t)))
    gx=cv2.Sobel(g,cv2.CV_32F,1,0,ksize=3); gy=cv2.Sobel(g,cv2.CV_32F,0,1,ksize=3)
    mag=cv2.magnitude(gx,gy)
    M.append([d[k], g.mean(), np.mean(g>=250)*100, np.mean(g>=240)*100,
              np.mean(g<=5)*100, mag.var(), np.mean(mag<8)*100, w[j], int(r["input_index"])])
a=np.array(M); np.save("/tmp/claude-1000/ircorr.npy", a)
print(f"有效 {len(a)} 帧")
print(f"\nIR 通道实测: 均值 {a[:,1].mean():.1f}  帧内最大亮度均值 {np.mean([cv2.imread(str(M3/'dataset'/r['image']),0).max() for r in rows[:40]]):.0f}")
print(f"  ≥250 像素占比: 中位 {np.median(a[:,2]):.4f}%  最大 {a[:,2].max():.3f}%")
print(f"  ≥240 像素占比: 中位 {np.median(a[:,3]):.4f}%  最大 {a[:,3].max():.3f}%")

names=["GT角速度","IR亮度均值","IR饱和%(≥250)","IR近饱和%(≥240)","IR全黑%","IR清晰度","IR低纹理%"]
idx=[7,1,2,3,4,5,6]
print(f"\n{'因素':<16}{'Spearman ρ':>12}{'p值':>11}{'排名':>6}")
rr=[]
for c,nm in zip(idx,names):
    rho,pv=spearmanr(a[:,c],a[:,0]); rr.append((abs(rho),nm,rho,pv))
for rk,(_,nm,rho,pv) in enumerate(sorted(rr,reverse=True),1):
    print(f"{nm:<16}{rho:>12.3f}{pv:>11.2e}{rk:>6}")

print("\n=== 按饱和程度分组看误差 ===")
for lab,m in (("饱和最重 5%", a[:,2]>=np.percentile(a[:,2],95)),
              ("饱和最轻 50%", a[:,2]<=np.percentile(a[:,2],50)),
              ("全部", np.ones(len(a),bool))):
    print(f"  {lab:<14} 误差中位 {np.median(a[m,0]):6.2f}mm  均值 {a[m,0].mean():6.2f}mm  n={m.sum()}")
