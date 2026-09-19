import importlib.util
from pathlib import Path
import numpy as np
ROOT=Path("/home/robot/ego_vio_humble")
W=ROOT/"reports/lighthouse_umi_workflow/REPRO_20260918_LSWEEP"
E12=ROOT/"reports/lighthouse_umi_workflow/20260912_034624_evaluation"
B=ROOT/"reports/lighthouse_umi_workflow/20260914_validation_v11_holdout_batch3"
spec=importlib.util.spec_from_file_location("ev",ROOT/"scripts/evaluate_slam_ground_truth.py")
ev=importlib.util.module_from_spec(spec); spec.loader.exec_module(ev)
SESS={"09-12":(W/"09-12_l0.25/f.csv",E12/"lighthouse_body_ground_truth.csv"),
      "group2_tight":(W/"group2_tight_l0.25/f.csv",B/"group2/lighthouse_body_ground_truth.csv"),
      "group1_sparse":(W/"group1_sparse_l0.25/f.csv",B/"group1/lighthouse_body_ground_truth.csv")}
for name,(traj,gt) in SESS.items():
    et,ep,_=ev.load_trajectory(traj); gt_t,gt_p,gt_q=ev.load_trajectory(gt)
    ins,val,_,_=ev.interpolate_ground_truth(et,gt_t,gt_p,gt_q,0.1)
    ts,eps=et[ins][val],ep[ins][val]
    _,_,ip,_=ev.interpolate_ground_truth(ts,gt_t,gt_p,gt_q,0.1)
    gp=ip[:,1:4]; R,t=ev.rigid_align(eps,gp)
    mm=np.linalg.norm((eps@R.T+t)-gp,axis=1)*1000.0
    t0=ts-ts[0]
    over=np.where(mm>10)[0]
    # 连续段
    runs=[]
    if len(over):
        s=over[0]; p=over[0]
        for i in over[1:]:
            if i==p+1: p=i
            else: runs.append((s,p)); s=i; p=i
        runs.append((s,p))
    print(f"\n{'='*70}\n{name}:  n={len(mm)}  RMSE={np.sqrt((mm**2).mean()):.2f}  "
          f"P95={np.percentile(mm,95):.2f}  max={mm.max():.2f}  超10mm帧数={len(over)}  连续段数={len(runs)}")
    for s,p in runs:
        dur=t0[p]-t0[s]
        print(f"   t={t0[s]:7.3f}..{t0[p]:7.3f}s  长度={p-s+1}帧  时长={dur*1000:.0f}ms  "
              f"峰={mm[s:p+1].max():.2f}mm")
        lo=max(0,s-6); hi=min(len(mm),p+7)
        prof=" ".join(f"{v:.0f}" for v in mm[lo:hi])
        print(f"      邻域(±6帧)轮廓: [{prof}]")
