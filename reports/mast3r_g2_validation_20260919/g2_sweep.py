#!/usr/bin/env python3
"""G1(现网产物) vs G2(现役融合尾段参数) 全数据组对照 —— 最终版。

前置: 前端/标定输入逐字节冻结(复用各组现成的 trajectory_imu_metric.csv 与
      四个 stereo 报告), 只改融合尾段 [7][8][9] 的参数。所以本对照回答的是
      "现役融合尾段参数 vs 09-14 第一代参数", 不是整条流水线。

分组判定:
  PASS      五项门全过
  REJECT    现役代码因 VINS 验收未通过而拒绝(正确行为)
  QGATE     [9/9] 输入质量门拒绝, 但仍给融合产物打分
  失败       其它错误
"""
import json
import shutil
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
sys.path.insert(0, "/tmp/claude-1000/stereoab")
import evaluate_slam_ground_truth as E  # noqa: E402
import rerun_tail as RT  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
SCRATCH = Path("/tmp/claude-1000/stereoab/g2final")
BATCHES = [
    "20260914_validation_v10_batch",
    "20260914_validation_v10_holdout_batch2",
    "20260914_validation_v11_holdout_batch3",
    "20260915_batch5_four_videos",
    "20260915_collective_batch4",
    "20260916_fusion_v11_umi_only_batch",
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


def preflight(g, sub):
    """返回 (可否重跑, 原因)。"""
    if not (g / "docker2_slam" / "vio_corrected_stream.csv").is_file():
        return False, "无 VINS 轨迹"
    if not (g / "fusion" / sub / "mast3r" / "graph_fusion_report.json").is_file():
        return False, "无 graph_fusion_report(旧产物不完整)"
    if not (g / "fusion" / sub / "mast3r" / "trajectory_imu_metric.csv").is_file():
        return False, "无 trajectory_imu_metric"
    ra = g / "docker2_slam" / "run_acceptance.json"
    if ra.is_file() and json.loads(ra.read_text()).get("result") != "PASS":
        return False, "VINS 验收 FAIL(现役代码拒绝)"
    return True, ""


def main():
    rows = []
    print(f"{'组':<44}{'候选':<7}{'状态':<9}"
          f"{'G1 rmse/p95/max/w10/rot':>36}   {'G2 rmse/p95/max/w10/rot':>34}")
    print("-" * 142)
    for b in BATCHES:
        for g in sorted((ROOT / b).glob("group*")):
            gt = g / "lighthouse_body_ground_truth.csv"
            if not gt.is_file():
                continue
            for sub in ("sparse", "tight"):
                if not (g / "fusion" / sub).is_dir():
                    continue
                ok, why = preflight(g, sub)
                g1p = g / "fusion" / sub / "trajectory_fused.csv"
                if not ok:
                    print(f"{b+'/'+g.name:<44}{sub:<7}{'跳过':<9}{why}")
                    rows.append(dict(group=f"{b}/{g.name}", cand=sub, status="skip",
                                     why=why))
                    continue
                out = SCRATCH / b / g.name
                if out.exists():
                    shutil.rmtree(out)
                res = RT.run_tail(g, out, {}, sub)
                rec = dict(group=f"{b}/{g.name}", cand=sub,
                           quality_gate=res.get("quality_gate"),
                           error=res.get("error"))
                if res["out"]:
                    rec["g1"] = score(g1p, gt) if g1p.is_file() else None
                    rec["g2"] = score(res["out"], gt)
                    st = "QGATE" if res.get("quality_gate") == "REJECT" else "OK"
                else:
                    st = "失败"
                rows.append(rec)

                def f(s):
                    return (f"{s['rmse']:>6.2f}/{s['p95']:>6.2f}/{s['mx']:>6.2f}"
                            f"/{s['w10']:>6.1f}/{s['rot']:>5.2f}{'✓' if s['pass'] else '✗'}"
                            ) if s else " " * 34 + "—"
                print(f"{b+'/'+g.name:<44}{sub:<7}{st:<9}{f(rec.get('g1')):>36}   "
                      f"{f(rec.get('g2')):>34}")
                if rec.get("error"):
                    print(f"      └ {rec['error'][:150]}")

    SCRATCH.mkdir(parents=True, exist_ok=True)
    (SCRATCH / "g2_sweep.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))

    both = [r for r in rows if r.get("g1") and r.get("g2")]
    print(f"\n{'='*142}\n可对照 {len(both)} 项")
    print(f"  G1 达标 {sum(1 for r in both if r['g1']['pass'])}   "
          f"G2 达标 {sum(1 for r in both if r['g2']['pass'])}")
    print(f"  G2 使 max 下降 {sum(1 for r in both if r['g2']['mx'] < r['g1']['mx'])} / "
          f"上升 {sum(1 for r in both if r['g2']['mx'] > r['g1']['mx'])}")
    print(f"  G2 使 RMSE 下降 {sum(1 for r in both if r['g2']['rmse'] < r['g1']['rmse'])} / "
          f"上升 {sum(1 for r in both if r['g2']['rmse'] > r['g1']['rmse'])}")
    print("\n达标的:")
    for r in rows:
        for k in ("g1", "g2"):
            if r.get(k) and r[k]["pass"]:
                print(f"  {r['group']}/{r['cand']}  {k.upper()}  "
                      f"rmse {r[k]['rmse']:.2f} p95 {r[k]['p95']:.2f} "
                      f"max {r[k]['mx']:.2f} w10 {r[k]['w10']:.1f} rot {r[k]['rot']:.2f}")


if __name__ == "__main__":
    main()
