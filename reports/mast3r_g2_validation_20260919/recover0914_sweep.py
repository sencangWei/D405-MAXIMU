#!/usr/bin/env python3
"""09-14 尾段配方 vs 现役(G2) 尾段配方 —— 全数据组对照。

背景: 09-14 当天二代配方并存, 且**都在产物里留了证**:
  v10_batch 15:59 / batch2 17:57 的 tight 候选 = lw 0.35 + adaptive + sw 0.85 + smooth 15
  v11_batch3 22:49 的 sparse/tight 都 = lw 0 + 无 adaptive + sw 0 + smooth 8
而历史扫描只试过 lw∈{0,0.125,0.25} / sw∈{0,0.475,0.70,1.00} ——
**v10 那一代(lw 0.35, sw 0.85, smooth 15)从未被测过**。本脚本补这一格。

前端/标定逐字节冻结(复用 trajectory_imu_metric.csv + 四份 stereo 报告),
只改尾段 [7][8][9] 参数。打分用官方评测器模块 evaluate_slam_ground_truth,
门限 = 官方默认 (rmse/p95/max 10mm, w10 95%, rot 2.0°)。
输出只写 /tmp scratch, 不动真实产物目录。
"""
import json
import shutil
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
sys.path.insert(0, "/home/robot/ego_vio_humble/reports/mast3r_g2_validation_20260919")
import evaluate_slam_ground_truth as E  # noqa: E402
import rerun_tail as RT  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
SCRATCH = Path("/tmp/claude-1000/stereoab/recover0914")
BATCHES = [
    "20260914_validation_v10_batch",
    "20260914_validation_v10_holdout_batch2",
    "20260914_validation_v11_holdout_batch3",
    "20260915_collective_batch4",
    "20260915_batch5_four_videos",
    "20260916_fusion_v11_umi_only_batch",
]
LIM = dict(rmse=10.0, p95=10.0, mx=10.0, w10=95.0, rot=2.0)

CONFIGS = [
    # 现役基线: lw .25 / sw .475 / adaptive / smooth 8 / cap per-node
    ("A_G2_current", {}),
    # 09-14 v10_batch+batch2 的 tight 候选配方(当日最好那代的实证参数)
    ("R1_v10_tight", dict(docker2_local_weight="0.35", docker2_scale_weight="0.85",
                          smoothing_s="15", joint_correction_cap_mode="global")),
    # 09-14 v11_batch3 的配方(当日最后一版)
    ("R2_v11_batch3", dict(docker2_local_weight="0", docker2_scale_weight="0",
                           no_adaptive_local_weight="1",
                           joint_correction_cap_mode="global")),
    # 单变量消融: 只回到 lw 0.35
    ("R3_lw035", dict(docker2_local_weight="0.35")),
    # 单变量消融: 只回到 sw 0.85
    ("R4_sw085", dict(docker2_scale_weight="0.85")),
    # 单变量消融: 只回到 smoothing 15
    ("R5_smooth15", dict(smoothing_s="15")),
]


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
    if not (g / "docker2_slam" / "vio_corrected_stream.csv").is_file():
        return False, "无 VINS 轨迹"
    if not (g / "fusion" / sub / "mast3r" / "graph_fusion_report.json").is_file():
        return False, "无 graph_fusion_report"
    if not (g / "fusion" / sub / "mast3r" / "trajectory_imu_metric.csv").is_file():
        return False, "无 trajectory_imu_metric"
    ra = g / "docker2_slam" / "run_acceptance.json"
    if ra.is_file() and json.loads(ra.read_text()).get("result") != "PASS":
        return False, "VINS 验收 FAIL(现役代码拒绝)"
    return True, ""


def f(s):
    if not s:
        return " " * 30 + "—"
    return (f"{s['rmse']:>6.2f}/{s['p95']:>6.2f}/{s['mx']:>6.2f}"
            f"/{s['w10']:>6.1f}/{s['rot']:>5.2f}{'✓' if s['pass'] else '✗'}")


def main():
    rows = []
    for b in BATCHES:
        for g in sorted((ROOT / b).glob("group*")):
            gt = g / "lighthouse_body_ground_truth.csv"
            if not gt.is_file():
                continue
            for sub in ("sparse", "tight"):
                if not (g / "fusion" / sub).is_dir():
                    continue
                ok, why = preflight(g, sub)
                gid = f"{b}/{g.name}/{sub}"
                if not ok:
                    print(f"{gid:<52} 跳过: {why}", flush=True)
                    rows.append(dict(group=gid, status="skip", why=why))
                    continue
                print(f"\n### {gid}", flush=True)
                rec = dict(group=gid, cand=sub, configs={})
                for name, ov in CONFIGS:
                    out = SCRATCH / b / g.name / name
                    if out.exists():
                        shutil.rmtree(out)
                    try:
                        res = RT.run_tail(g, out, ov, sub)
                    except Exception as e:  # noqa: BLE001
                        rec["configs"][name] = dict(error=repr(e)[:200])
                        print(f"   {name:<16} 异常 {repr(e)[:90]}", flush=True)
                        continue
                    c = dict(qgate=res.get("quality_gate"), error=res.get("error"))
                    if res.get("out"):
                        c["m"] = score(res["out"], gt)
                    rec["configs"][name] = c
                    print(f"   {name:<16} {f(c.get('m'))}"
                          f"{'  QGATE' if c.get('qgate') == 'REJECT' else ''}"
                          f"{'  ERR:' + str(c['error'])[:60] if c.get('error') else ''}",
                          flush=True)
                rows.append(rec)

    SCRATCH.mkdir(parents=True, exist_ok=True)
    (SCRATCH / "recover0914_sweep.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1))

    print("\n" + "=" * 78)
    print(f"{'配置':<18}{'可比组':>7}{'达标':>7}{'max中位':>10}{'RMSE中位':>10}")
    print("-" * 78)
    for name, _ in CONFIGS:
        ms = [r["configs"].get(name, {}).get("m") for r in rows if "configs" in r]
        ms = [m for m in ms if m]
        if not ms:
            print(f"{name:<18}{0:>7}")
            continue
        print(f"{name:<18}{len(ms):>7}{sum(1 for m in ms if m['pass']):>7}"
              f"{np.median([m['mx'] for m in ms]):>10.2f}"
              f"{np.median([m['rmse'] for m in ms]):>10.2f}")
    print("\n达标的:")
    for r in rows:
        for name, _ in CONFIGS:
            m = r.get("configs", {}).get(name, {}).get("m")
            if m and m["pass"]:
                print(f"  {r['group']:<46}{name:<16}"
                      f"rmse {m['rmse']:.2f} max {m['mx']:.2f} "
                      f"w10 {m['w10']:.1f} rot {m['rot']:.2f}")


if __name__ == "__main__":
    main()
