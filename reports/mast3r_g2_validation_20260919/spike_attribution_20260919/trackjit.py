import csv, sys
import numpy as np
from pathlib import Path
from scipy.signal import savgol_filter
sys.path.insert(0,'/home/robot/ego_vio_humble/scripts')
import evaluate_slam_ground_truth as E
from scipy.spatial.transform import Rotation as Rot

rows = np.load("/tmp/claude-1000/attrib.npy", allow_pickle=True)
TRK = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_sessions/20260914_202320_validation_v10/tracker.csv")

t=[];p=[];q=[]
with TRK.open(newline="") as f:
    for r in csv.DictReader(f):
        t.append(float(r["device_time_s"]))
        p.append([float(r["px_m"]),float(r["py_m"]),float(r["pz_m"])])
        q.append([float(r["qw"]),float(r["qx"]),float(r["qy"]),float(r["qz"])])
t=np.array(t); p=np.array(p); q=np.array(q)
dt=np.median(np.diff(t))
print(f"tracker 原始: {len(t)} 样本 @ {1/dt:.1f}Hz, 时长 {t[-1]-t[0]:.1f}s")

# 平滑后残差 = 高频抖动 (mm)
ps = savgol_filter(p, 9, 2, axis=0)
jit = np.linalg.norm(p-ps, axis=1)*1000
# 角抖动 (deg)
qsm = np.zeros_like(q)
for i in range(4):
    qsm[:,i] = savgol_filter(q[:,i], 9, 2)
qsm /= np.linalg.norm(qsm,axis=1,keepdims=True)
ajit = np.degrees((Rot.from_quat(qsm).inv()*Rot.from_quat(q)).magnitude())
spd = np.linalg.norm(np.gradient(p, t, axis=0), axis=1)
w = np.degrees((Rot.from_quat(q[:-1]).inv()*Rot.from_quat(q[1:])).magnitude())/np.diff(t)
w = np.concatenate([[w[0]], w])

print(f"\n全局: 位置抖动中位 {np.median(jit):.3f}mm, 姿态抖动中位 {np.median(ajit):.4f}°")
print(f"      线速度中位 {np.median(spd)*1000:.1f}mm/s, 角速度中位 {np.median(w):.1f}°/s\n")

# 把每个 cell 的超标窗口映射到 tracker 时间
r = [x for x in rows if x["cell"].endswith("v11_holdout_batch3/group2/tight")][0]
# 需要 GT 的绝对时间: 重新读
WF=Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
print("=== 09-14 take: 超标窗 vs 全部窗, tracker 抖动 ===")
gtp = WF/r["cell"].split("/")[0].replace("validation_","") # not used
D = WF/"20260914_validation_v11_holdout_batch3/group2"
rt,_,_ = E.load_trajectory(D/"lighthouse_body_ground_truth.csv")
df = r["df"]
print(f"{'轨迹位置':>10}{'GT差(mm)':>10}{'位置抖动':>10}{'姿态抖动':>10}{'线速度':>10}{'角速度':>10}")
for wn in r["wins"]:
    i0,i1 = wn["i0"], wn["i1"]
    m = (t>=rt[i0]-0.05)&(t<=rt[i1]+0.05)
    print(f"{wn['t0']:>9.1f}s{df[i0:i1+1].max():>10.2f}{np.median(jit[m]):>9.3f}mm"
          f"{np.median(ajit[m]):>9.4f}°{np.median(spd[m])*1000:>9.1f}mm/s{np.median(w[m]):>9.1f}°/s")
base = jit<t[0]  # dummy
print(f"{'全局基线':>10}{'':>10}{np.median(jit):>9.3f}mm{np.median(ajit):>9.4f}°"
      f"{np.median(spd)*1000:>9.1f}mm/s{np.median(w):>9.1f}°/s")

print("\n=== 全 19 cell 汇总: 超标窗 vs 全局, tracker 自身抖动 ===")
rj, ra, rs, rw = [], [], [], []
for x in rows:
    if not x["n_spike"]: continue
    spin = x["df"] > 10.0
    rt2,_,_ = E.load_trajectory(WF/(x["cell"].rsplit("/",2)[0]+"/"+x["cell"].rsplit("/",2)[1])/"lighthouse_body_ground_truth.csv")
    m = np.zeros(len(t), bool)
    for i in np.where(spin)[0]:
        m |= (t>=rt2[i]-0.05)&(t<=rt2[i]+0.05)
    if m.sum()<5: continue
    rj.append(np.median(jit[m])/np.median(jit))
    ra.append(np.median(ajit[m])/np.median(ajit))
    rs.append(np.median(spd[m])/np.median(spd))
    rw.append(np.median(w[m])/np.median(w))
for lab, v in (("位置抖动",rj),("姿态抖动",ra),("线速度",rs),("角速度",rw)):
    v=np.array(v)
    print(f"  {lab:<8} 中位 {np.median(v):>5.2f}x   变糙(>1.3x)的 cell: {np.sum(v>1.3)}/{len(v)}")
