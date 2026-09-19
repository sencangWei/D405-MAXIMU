import csv, sqlite3, sys
import numpy as np, cv2
from pathlib import Path
from scipy.stats import spearmanr
sys.path.insert(0,'/home/robot/ego_vio_humble/scripts')
import evaluate_slam_ground_truth as E

DB3="/home/robot/umi_ego_vio_data_device2_c48df736/recordings/d405_720p_rgb_stereo_ir_20260914_202348/d405_720p_rgb_stereo_ir.db3"
FR ="/home/robot/umi_ego_vio_data_device2_c48df736/recordings/d405_720p_rgb_stereo_ir_20260914_202348/d405_frames.csv"
D=Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow/20260914_validation_v11_holdout_batch3/group2")

mono=np.array([float(r["color_mono"]) for r in csv.DictReader(open(FR,newline=""))])
OFF=1789388630.979520-mono[0]
con=sqlite3.connect(f"file:{DB3}?mode=ro&immutable=1",uri=True)
ids=[r[0] for r in con.execute("SELECT id FROM messages WHERE topic_id=69 ORDER BY id")]

rt,rp,rq=E.load_trajectory(D/"lighthouse_body_ground_truth.csv")
et,ep,eq=E.load_trajectory(D/"fusion/tight/trajectory_fused.csv")
ins,val,itp,iq=E.interpolate_ground_truth(et,rt,rp,rq,0.1)
R,tr=E.rigid_align(ep[ins][val],itp[:,1:])
d=np.linalg.norm(ep[ins][val]@R.T+tr-itp[:,1:],axis=1)*1000
tt=et[ins][val]
# GT 动作量
dt=np.diff(rt)
w=np.degrees((E.Rotation.from_quat(rq[:-1]).inv()*E.Rotation.from_quat(rq[1:])).magnitude())/dt
w=np.concatenate([[w[0]],w])
spd=np.concatenate([[0],np.linalg.norm(np.diff(rp,axis=0),axis=1)/dt])

M=[]
for i in range(0,len(ids),2):
    b=bytes(con.execute("SELECT data FROM messages WHERE id=?",(ids[i],)).fetchone()[0])
    Y=np.frombuffer(b[len(b)-1843200:],np.uint8)[0::2].reshape(720,1280).astype(np.float32)
    gx=cv2.Sobel(Y,cv2.CV_32F,1,0,ksize=3); gy=cv2.Sobel(Y,cv2.CV_32F,0,1,ksize=3)
    mag=cv2.magnitude(gx,gy)
    wall=mono[i]+OFF
    if not (rt[0]<=wall<=rt[-1]): continue
    k=int(np.argmin(np.abs(tt-wall)))
    if abs(tt[k]-wall)>0.05: continue
    j=int(np.argmin(np.abs(rt-wall)))
    M.append([d[k], Y.mean(), np.mean(Y>235)*100, np.mean(Y<16)*100,
              mag.var(), np.mean(mag<8)*100, w[j], spd[j]*1000])
a=np.array(M); np.save("/tmp/claude-1000/imgcorr.npy", a)
print(f"{len(a)} 帧\n")

names=["GT角速度","GT线速度","亮度均值","过曝%","欠曝%","清晰度","低纹理%"]
idx  =[6,7,1,2,3,4,5]
res=[]
for c,nm in zip(idx,names):
    rho,pv=spearmanr(a[:,c],a[:,0])
    res.append((abs(rho),nm,rho,pv))
print(f"{'因素':<10}{'Spearman ρ':>12}{'p值':>11}{'|ρ|排名':>9}")
for rk,(ar,nm,rho,pv) in enumerate(sorted(res,reverse=True),1):
    print(f"{nm:<10}{rho:>12.3f}{pv:>11.2e}{rk:>9}")
print(f"\n误差 vs 角速度:  ρ={spearmanr(a[:,6],a[:,0])[0]:+.3f}")
print(f"最大误差样本: {a[:,0].max():.2f}mm @ 角速度 {a[np.argmax(a[:,0]),6]:.1f}°/s")
