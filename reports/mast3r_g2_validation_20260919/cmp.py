import numpy as np
G=np.loadtxt('/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow/20260916_fusion_v11_umi_only_batch/group1/fusion/mast3r/mast3r_logs/dataset_full.txt')
Bd=np.loadtxt('/home/robot/ego_vio_humble/reports/original_v1_baseline/group1/frontend/mast3r_logs/dataset_full.txt')
print("shape", G.shape, Bd.shape)
pg, pb = G[:,1:4], Bd[:,1:4]
print("\n=== 前 6 帧 (好 / 坏) ===")
for i in range(6):
    print(f"  f{i}: 好={pg[i]}  坏={pb[i]}")
print("\n=== 平移轨迹统计 (mm) ===")
for nm,p in (("好",pg),("坏",pb)):
    step=np.linalg.norm(np.diff(p,axis=0),axis=1)*1000
    print(f"  {nm}: 总路程={step.sum():.2f}  最大单帧={step.max():.3f}  中位单帧={np.median(step):.4f}  末端位移={np.linalg.norm(p[-1]-p[0])*1000:.2f}")
d=np.linalg.norm(pg-pb,axis=1)*1000
print(f"\n=== 好坏逐帧位置差 (mm) ===\n  中位={np.median(d):.3f} 均值={d.mean():.3f} P95={np.percentile(d,95):.3f} 最大={d.max():.3f} @f{int(d.argmax())}")
print("  首10帧:",np.round(d[:10],4))
print("  第100/500/1000/1798帧:",[round(float(d[i]),3) for i in (100,500,1000,1798)])
qg,qb=G[:,3:7],Bd[:,3:7]
dg=np.degrees(2*np.arccos(np.clip(np.abs(np.sum(qg*qb,axis=1)),0,1)))
print(f"\n=== 姿态差 (deg): 中位={np.median(dg):.4f} P95={np.percentile(dg,95):.4f} 最大={dg.max():.4f}")
