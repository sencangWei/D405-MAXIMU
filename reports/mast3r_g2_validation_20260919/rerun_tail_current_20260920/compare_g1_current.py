#!/usr/bin/env python3
"""逐 cell 对照：旧代产物(fusion/<subset>/) vs 现役重跑(fusion_current/<subset>/)。

两侧都**现算**：同一个评测器、同一组门限、同一条真值，只有轨迹不同。
⚠ 只报融合链（estimate 必须落在 trajectory_fused.csv）。
"""
import sys
from pathlib import Path
sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

WF = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
TH = dict(max_ate_rmse_mm=10.0, max_ate_p95_mm=10.0, max_ate_max_mm=10.0,
          min_within_10mm_ratio=0.95, max_rotation_rmse_deg=2.0,
          min_timestamp_overlap_ratio=0.98)
GATES = [("rmse", "ate_translation_rmse_m", 1e3, "max"),
         ("p95", "ate_translation_p95_m", 1e3, "max"),
         ("max", "ate_translation_max_m", 1e3, "max"),
         ("w10%", "ate_translation_within_10mm_ratio", 1e2, "min"),
         ("rot°", "ate_rotation_rmse_deg", 1.0, "max")]


def score(traj, gt):
    if not traj.is_file():
        return None
    et, ep, eq = E.load_trajectory(traj)
    rt, rp, rq = E.load_trajectory(gt)
    ins, val, itp, iq = E.interpolate_ground_truth(et, rt, rp, rq, 0.1)
    m = E.pose_errors(ep[ins][val], eq[ins][val], itp[:, 1:], iq, 30)
    m["timestamp_overlap_ratio"] = float(val.sum() / len(val))
    a = E.acceptance(m, **TH)
    return dict(m=m, result=a["result"], fail=a.get("failures", []))


def cells():
    for b in sorted(WF.glob("*")):
        if not b.is_dir():
            continue
        for g in sorted(b.glob("group*")):
            gt = g / "lighthouse_body_ground_truth.csv"
            if not gt.is_file():
                continue
            for s in ("sparse", "tight"):
                if (g / "fusion" / s / "trajectory_fused.csv").is_file():
                    yield f"{b.name}/{g.name}/{s}", g, s, gt


rows = []
for key, g, s, gt in cells():
    old = score(g / "fusion" / s / "trajectory_fused.csv", gt)
    new = score(g / "fusion_current" / s / "trajectory_fused.csv", gt)
    rows.append((key, old, new))

hdr = f"{'cell':<46}" + "".join(f"{n:>10}{n:>10}{'Δ':>9}" for n, *_ in GATES)
print(f"{'':<46}" + "".join(f"{'旧':>10}{'新':>10}{'':>9}" for _ in GATES))
print(hdr)
print("-" * len(hdr))
for key, old, new in rows:
    line = f"{key:<46}"
    for nm, k, sc, direction in GATES:
        a = old["m"][k] * sc if old else None
        c = new["m"][k] * sc if new else None
        line += f"{a:10.2f}" if a is not None else "       ---"
        line += f"{c:10.2f}" if c is not None else "       ---"
        line += f"{c - a:+9.2f}" if (a is not None and c is not None) else "      ---"
    print(line)

print()
npass_o = sum(1 for _, o, _ in rows if o and o["result"] == "PASS")
npass_n = sum(1 for _, _, n in rows if n and n["result"] == "PASS")
ndone = sum(1 for _, _, n in rows if n)
print(f"达标: 旧 {npass_o}/{sum(1 for _,o,_ in rows if o)}   现役 {npass_n}/{ndone}")
for key, old, new in rows:
    ot = f"{old['result']:<4} {old['fail']}" if old else "未算"
    nt = f"{new['result']:<4} {new['fail']}" if new else "未跑"
    print(f"  {key:<46} 旧 {ot:<42} → 现役 {nt}")
