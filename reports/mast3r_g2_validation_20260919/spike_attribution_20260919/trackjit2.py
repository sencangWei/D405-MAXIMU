import csv, json, sys
import numpy as np
from pathlib import Path
from scipy.signal import savgol_filter
sys.path.insert(0,'/home/robot/ego_vio_humble/scripts')
import evaluate_slam_ground_truth as E
from scipy.spatial.transform import Rotation as Rot

WF = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
rows = np.load("/tmp/claude-1000/attrib.npy", allow_pickle=True)

def load_tracker(path):
    t=[];p=[];q=[]
    with Path(path).open(newline="") as f:
        for r in csv.DictReader(f):
            t.append(float(r["device_time_s"]))
            p.append([float(r["px_m"]),float(r["py_m"]),float(r["pz_m"])])
            q.append([float(r["qw"]),float(r["qx"]),float(r["qy"]),float(r["qz"])])
    return np.array(t), np.array(p), np.array(q)

print(f"{'cell':<44}{'GT差':>7}{'位置抖动':>9}{'基线':>8}{'倍数':>7}{'速度比':>7}")
print("-"*84)
agg=[]
for x in rows:
    if not x["n_spike"]: continue
    b,g,sub = x["cell"].split("/")
    gd = WF/b/g
    pv = gd/"lighthouse_ground_truth_provenance.json"
    if not pv.is_file(): continue
    trk = json.loads(pv.read_text()).get("inputs",{}).get("tracker")
    if not trk or not Path(trk).is_file(): continue
    t,p,q = load_tracker(trk)
    ps = savgol_filter(p, 9, 2, axis=0)
    jit = np.linalg.norm(p-ps,axis=1)*1000
    spd = np.linalg.norm(np.gradient(p,t,axis=0),axis=1)
    rt,_,_ = E.load_trajectory(gd/"lighthouse_body_ground_truth.csv")
    spin = x["df"] > 10.0
    m = np.zeros(len(t),bool)
    for i in np.where(spin)[0]:
        m |= (t>=rt[i]-0.05)&(t<=rt[i]+0.05)
    if m.sum()<5: continue
    j_sp, j_bs = np.median(jit[m]), np.median(jit)
    mx = float(np.nanmax(x["df"]))
    sr = np.median(spd[m])/np.median(spd)
    agg.append((j_sp, j_bs, mx))
    print(f"{x['cell']:<44}{mx:>7.2f}{j_sp:>8.3f}mm{j_bs:>7.3f}mm{j_sp/j_bs:>6.2f}x{sr:>6.1f}x")

print("-"*84)
a=np.array(agg)
print(f"\n★ 关键判据: 超标窗处 tracker 自身抖动 vs 我们要解释的误差")
print(f"   抖动中位 {np.median(a[:,0]):.3f}mm   最大 {a[:,0].max():.3f}mm")
print(f"   对应误差中位 {np.median(a[:,2]):.2f}mm  最大 {a[:,2].max():.2f}mm")
print(f"   → 抖动只占误差的 {np.median(a[:,0]/a[:,2])*100:.2f}% (中位), 最坏 {np.max(a[:,0]/a[:,2])*100:.2f}%")
print(f"   → 抖动确实随动作变糙: 中位 {np.median(a[:,0]/a[:,1]):.2f}x, 但绝对量级差 {np.median(a[:,2]/a[:,0]):.0f} 倍")
