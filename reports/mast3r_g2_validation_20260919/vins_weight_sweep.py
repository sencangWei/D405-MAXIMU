#!/usr/bin/env python3
"""VINS 分支取舍扫描 —— 跨全组, 回答"VINS 分支是帮忙还是添乱"。

动机(来自本次根因排查): VINS 是三条链里最差的(中位 RMSE 24.21mm),
隐含尺度中位 0.9599 低至 0.627, 而现役 G2 用 --docker2-local-weight 0.25
真的依赖了这条分支, 并因此触发 [9/9] 输入质量门 REJECT 6 组。
那么把 VINS 权重降下来, 是既提精度又少 REJECT, 还是反而变差?

规矩: 不许为单条轨迹调参 ⇒ 本扫描在全组上跑, 且前端/标定输入逐字节冻结,
      只动融合尾段参数。全部输出到 scratch。

同时记录每组的质量门状态(REJECT/OK) —— 这决定产物能不能出厂。
"""
import json
import shutil
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
sys.path.insert(0, str(Path(__file__).parent))
import evaluate_slam_ground_truth as E  # noqa: E402
import rerun_tail as RT  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
SCRATCH = Path("/tmp/claude-1000/stereoab/vsw")
BATCHES = [
    "20260914_validation_v10_batch",
    "20260914_validation_v10_holdout_batch2",
    "20260914_validation_v11_holdout_batch3",
    "20260915_batch5_four_videos",
    "20260915_collective_batch4",
]
LIM = dict(rmse=10.0, p95=10.0, mx=10.0, w10=95.0, rot=2.0)

# 只动这两项; 其余保持现役 G2。
CONFIGS = [
    ("A_g2_w0.250_sig0.008", {}),                                  # 现役基线
    ("B_w0.000_sig0.008", dict(docker2_local_weight="0")),         # 完全不信 VINS 位置
    ("C_w0.125_sig0.008", dict(docker2_local_weight="0.125")),     # 折中
    ("D_g2_w0.250_sig0.020", dict(relative_motion_sigma_m="0.02")),  # 放松 VINS 运动约束
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
        return False, "VINS 验收 FAIL"
    return True, ""


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    rows = []
    for b in BATCHES:
        for g in sorted((ROOT / b).glob("group*")):
            gt = g / "lighthouse_body_ground_truth.csv"
            if not gt.is_file():
                continue
            for sub in ("sparse", "tight"):
                if not (g / "fusion" / sub).is_dir():
                    continue
                gid = f"{b}/{g.name}"
                if only and only not in gid:
                    continue
                ok, why = preflight(g, sub)
                if not ok:
                    continue
                rec = dict(group=gid, cand=sub, configs={})
                for name, ov in CONFIGS:
                    out = SCRATCH / name / b / g.name
                    if out.exists():
                        shutil.rmtree(out)
                    res = RT.run_tail(g, out, ov, sub)
                    e = dict(qgate=res.get("quality_gate"), error=res.get("error"))
                    if res["out"]:
                        e["score"] = score(res["out"], gt)
                    rec["configs"][name] = e
                    s = e.get("score")
                    tag = "QGATE" if e["qgate"] == "REJECT" else ("OK" if s else "失败")
                    txt = (f"rmse {s['rmse']:>6.2f} p95 {s['p95']:>6.2f} "
                           f"max {s['mx']:>6.2f} w10 {s['w10']:>5.1f} rot {s['rot']:>4.2f}"
                           f" {'PASS' if s['pass'] else '    '}") if s else "—"
                    print(f"{gid:<44}{sub:<7}{name:<22}{tag:<7}{txt}", flush=True)
                rows.append(rec)
                SCRATCH.mkdir(parents=True, exist_ok=True)
                (SCRATCH / "vsw.json").write_text(
                    json.dumps(rows, ensure_ascii=False, indent=1))

    print(f"\n{'='*130}\n汇总(仅统计有分数的项)")
    print(f"{'配置':<24}{'可比':>5}{'达标':>6}{'QGATE':>7}{'max中位':>9}"
          f"{'rmse中位':>10}{'max最差':>9}")
    for name, _ in CONFIGS:
        vals = [(r["configs"][name].get("score"), r["configs"][name].get("qgate"))
                for r in rows if name in r["configs"]]
        sc = [v for v, _ in vals if v]
        qg = sum(1 for _, q in vals if q == "REJECT")
        if not sc:
            continue
        print(f"{name:<24}{len(sc):>5}{sum(1 for v in sc if v['pass']):>6}{qg:>7}"
              f"{np.median([v['mx'] for v in sc]):>9.2f}"
              f"{np.median([v['rmse'] for v in sc]):>10.2f}"
              f"{max(v['mx'] for v in sc):>9.2f}")


if __name__ == "__main__":
    main()
