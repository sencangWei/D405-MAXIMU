#!/usr/bin/env python3
"""G1(现网产物) vs G2(现役参数重跑) —— 全数据组对照。

发现: reports 目录里 12 组可测的 fusion/trajectory_fused.csv 全部是第一代参数
(global cap / adaptive_local_weight=False / scale_weight=0) 的产物,
而现役流水线用的是第二代。现役算法从未在这批数据上评过。

本脚本用 rerun_tail 以现役参数把每组重跑一遍(sparse, 另加 tight 若有),
再与 G1 产物在同一把尺子下对比。全部输出到 scratch, 不动真实产物。
"""
import json
import sys
import traceback
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
sys.path.insert(0, "/tmp/claude-1000/stereoab")
import evaluate_slam_ground_truth as E  # noqa: E402
import rerun_tail as RT  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
SCRATCH = Path("/tmp/claude-1000/stereoab/g2run")
BATCHES = [
    "20260914_validation_v10_batch",
    "20260914_validation_v10_holdout_batch2",
    "20260914_validation_v11_holdout_batch3",
    "20260915_batch5_four_videos",
    "20260915_collective_batch4",
]
LIM = dict(rmse=10.0, p95=10.0, mx=10.0, w10=95.0, rot=2.0)


def score(est, gt):
    et, ep, eq = E.load_trajectory(est)
    rt, rp, rq = E.load_trajectory(gt)
    inside, valid, interp, iq = E.interpolate_ground_truth(et, rt, rp, rq, 0.1)
    if valid.sum() < 10:
        return None
    P, Q, Pq = ep[inside][valid], interp[:, 1:], eq[inside][valid]
    R, t = E.rigid_align(P, Q)
    d = np.linalg.norm(P @ R.T + t - Q, axis=1) * 1000
    ang = np.degrees((E.Rotation.from_quat(iq).inv()
                      * (E.Rotation.from_matrix(R) * E.Rotation.from_quat(Pq))).magnitude())
    m = dict(rmse=float(np.sqrt(np.mean(d ** 2))), p95=float(np.percentile(d, 95)),
             mx=float(d.max()), w10=float(np.mean(d <= 10.0) * 100),
             rot=float(np.sqrt(np.mean(ang ** 2))))
    m["fail"] = [k for k in ("rmse", "p95", "mx") if m[k] > LIM[k]] + \
                (["w10"] if m["w10"] < LIM["w10"] else []) + \
                (["rot"] if m["rot"] > LIM["rot"] else [])
    m["pass"] = not m["fail"]
    return m


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    rows = []
    for b in BATCHES:
        for g in sorted((ROOT / b).glob("group*")):
            gid = f"{b}/{g.name}"
            if only and only not in gid:
                continue
            gt = g / "lighthouse_body_ground_truth.csv"
            if not gt.is_file():
                continue
            rec = dict(group=gid, cands={})
            for sub in ("sparse", "tight"):
                g1 = g / "fusion" / sub / "trajectory_fused.csv"
                if not (g / "fusion" / sub / "mast3r" / "trajectory_imu_metric.csv").is_file():
                    continue
                out = SCRATCH / b / g.name / sub
                try:
                    import shutil, subprocess
                    if out.exists():
                        shutil.rmtree(out)
                    for c in RT.build(g, out, {}, sub):
                        r = subprocess.run(c, capture_output=True, text=True)
                        if r.returncode:
                            raise RuntimeError(r.stderr[-800:])
                    g2p = out / sub / "trajectory_fused.csv"
                    s1 = score(g1, gt) if g1.is_file() else None
                    s2 = score(g2p, gt)
                    rec["cands"][sub] = dict(g1=s1, g2=s2)
                    tag = "重跑OK"
                except Exception as exc:  # noqa: BLE001
                    msg = str(exc)
                    if "did not pass" in msg:
                        rec["cands"][sub] = dict(rejected="VINS 验收未通过, 现役代码正确拒绝")
                        tag = "REJECT"
                    else:
                        rec["cands"][sub] = dict(error=msg[:300])
                        tag = "失败"
                        traceback.print_exc(limit=1)
                def fmt(s):
                    return (f"{s['rmse']:>6.2f}/{s['p95']:>6.2f}/{s['mx']:>6.2f}"
                            f"/{s['w10']:>6.1f}/{s['rot']:>5.2f}") if s else " " * 33 + "—"
                c = rec["cands"][sub]
                print(f"{gid:<44}{sub:<7}{tag}  G1 {fmt(c.get('g1'))}   "
                      f"G2 {fmt(c.get('g2'))}")
            rows.append(rec)

    SCRATCH.mkdir(parents=True, exist_ok=True)
    (SCRATCH / "g1_vs_g2.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    print("\n表头: rmse/p95/max/10mm%/姿态(度)")


if __name__ == "__main__":
    main()
