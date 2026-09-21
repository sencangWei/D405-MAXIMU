#!/usr/bin/env python3
"""§32 定标问题：**09-14 的生产产物 vs 现役参数**，按官方门逐组比。

盘上四个族（同前端、只差 [8/9] 参数）——参数指纹由 fusion_report.json 读出：

  fusion/<arm>/          09-14 参数：sparse=lw0/sw0/smo8；tight=lw.35/adaptive/sw.85/smo15
  fusion_current/<arm>/  lw.25/adaptive/sw.475/smo8          （09-20 01:34）
  fusion_v2/<arm>/       lw0/sw.25/smo8  ← **正好等于现役产线脚本的参数**（09-20 18:31）
  fusion/trajectory_fused.csv   09-14 **生产产物**（候选选择 + 平滑）

官方口径（rigid_align，SE3 无尺度）：
  RMSE ≤10mm、p95 ≤10mm、max ≤10mm、within_10mm ≥0.95、rot RMSE ≤2.0°

⚠⚠ **前置：先过 tracker 分支门**（`scripts/lighthouse_tracker_branch_gate.py`）。
被 REJECT 的 take 真值切成互不相接的多段，单一 SE(3) 对齐会在段间折中并抬高整条 ATE
⇒ 那些 cell 的 max/RMSE **不可用**，混进族间比较会把结论整个带偏。本脚本按**真值干净组**
出汇总，REJECT 组单独列出。

用法: family_gate.py [族名...]
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import scipy.spatial.transform as st

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

WF = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
HERE = Path(__file__).parent
LIM = dict(rmse=10.0, p95=10.0, mx=10.0, w10=0.95, rot=2.0)


def gate(p, gtdir):
    tg, Pg, Qg = E.load_trajectory(gtdir / "lighthouse_body_ground_truth.csv")
    t, P, Q = E.load_trajectory(p)
    ins, val, plt_, qgt = E.interpolate_ground_truth(t, tg, Pg, Qg, 0.1)
    t, P, Q = t[ins][val], P[ins][val], Q[ins][val]
    G, QG = plt_[:, 1:4], qgt[val]
    if len(t) < 50:
        return None
    R, tt = E.rigid_align(P, G)
    A = P @ R.T + tt
    err = np.linalg.norm(A - G, axis=1) * 1000
    # ⚠ 姿态误差必须与官方 evaluate_slam_ground_truth.pose_errors 同口径：
    #   aligned_rotations = Rotation(R_align) * Q_est ; err = angle(qg^-1 * aligned)
    #   朴素 qa^-1*qg 会算出 ~135° 的假值（fused 姿态≡VINS 姿态×常量世界旋转）
    qa, qg = st.Rotation.from_quat(Q), st.Rotation.from_quat(QG)
    ang = np.degrees(
        (qg.inv() * (st.Rotation.from_matrix(R) * qa)).magnitude())
    rmse = float(np.sqrt((err ** 2).mean()))
    rot = float(np.sqrt((ang ** 2).mean()))
    f = []
    if rmse > LIM["rmse"]: f.append("rmse")
    if np.percentile(err, 95) > LIM["p95"]: f.append("p95")
    if err.max() > LIM["mx"]: f.append("max")
    if (err <= 10).mean() < LIM["w10"]: f.append("within10")
    if rot > LIM["rot"]: f.append("rot")
    return dict(n=len(t), rmse=rmse, p95=float(np.percentile(err, 95)),
                mx=float(err.max()), w10=float((err <= 10).mean()), rot=rot,
                res="PASS" if not f else "FAIL", fails=f)


FAMS = {
    "09-14生产":      "fusion/trajectory_fused.csv",
    "09-14臂/sparse": "fusion/sparse/trajectory_fused.csv",
    "09-14臂/tight":  "fusion/tight/trajectory_fused.csv",
    "现役v2/sparse":  "fusion_v2/sparse/trajectory_fused.csv",
    "现役v2/tight":   "fusion_v2/tight/trajectory_fused.csv",
    "current/sparse": "fusion_current/sparse/trajectory_fused.csv",
    "current/tight":  "fusion_current/tight/trajectory_fused.csv",
}


BRANCH_GATE = "/home/robot/ego_vio_humble/scripts/lighthouse_tracker_branch_gate.py"


def gt_ok(gtdir):
    """tracker 分支门。返回 (ok, 说明)。缓存到 _gt_branch_cache.json。"""
    cache = HERE / "_gt_branch_cache.json"
    cc = json.loads(cache.read_text()) if cache.exists() else {}
    key = str(gtdir)
    if key not in cc:
        prov = gtdir / "lighthouse_ground_truth_provenance.json"
        tr = json.loads(prov.read_text()).get("inputs", {}).get("tracker") if prov.exists() else None
        if not tr or not Path(tr).exists():
            cc[key] = {"ok": False, "why": "无 tracker"}
        else:
            out = subprocess.run([sys.executable, BRANCH_GATE, str(Path(tr).parent)],
                                 capture_output=True, text=True).stdout
            line = next((l for l in out.splitlines() if "分支切换" in l), "")
            cc[key] = {"ok": "PASS" in out, "why": line.strip().split("：")[-1][:40]}
        cache.write_text(json.dumps(cc, indent=1, ensure_ascii=False))
    d = cc[key]
    return d["ok"], d["why"]


def main():
    which = sys.argv[1:] or list(FAMS)
    cells = [l.split("/") for l in (HERE / ".." / "cells22.txt").resolve().read_text().split()
             if l.count("/") == 2]
    seen, groups = set(), []
    for b, g, _ in cells:
        if (b, g) not in seen:
            seen.add((b, g)); groups.append((b, g))

    out = {}
    w = 17
    print(f"{'group':<44}" + "".join(f"{k:>{w}}" for k in which))
    clean = []
    for b, g in groups:
        gtdir = WF / b / g
        ok, why = gt_ok(gtdir)
        if ok:
            clean.append(f"{b}/{g}")
        row, txt = {"_gt_ok": ok, "_gt_why": why}, []
        for k in which:
            p = gtdir / FAMS[k]
            r = gate(p, gtdir) if p.exists() else None
            row[k] = r
            txt.append("(无)" if not r else
                       f"{r['mx']:5.2f}/{r['rmse']:4.2f}/{'P' if r['res']=='PASS' else 'F'}")
        out[f"{b}/{g}"] = row
        mark = " " if ok else "✗真值"
        print(f"{b[-26:] + '/' + g:<38}{mark:<6}" + "".join(f"{c:>{w}}" for c in txt))

    print("\n列 = max(mm) / RMSE(mm) / P|F   "
          "门: RMSE·p95·max ≤10mm, within10 ≥0.95, rot ≤2.0°")
    print("✗真值 = tracker 分支门 REJECT（该组 ATE 不可用，已从下面汇总剔除）\n")
    for k in which:
        rs = [v[k] for v in out.values() if v.get(k) and v["_gt_ok"]]
        if not rs:
            continue
        np_ = sum(r["res"] == "PASS" for r in rs)
        allf = {}
        for r in rs:
            for x in r["fails"]:
                allf[x] = allf.get(x, 0) + 1
        print(f"  {k:<18} PASS {np_:>2}/{len(rs)}   "
              f"max中位 {np.median([r['mx'] for r in rs]):6.2f}  "
              f"RMSE中位 {np.median([r['rmse'] for r in rs]):5.2f}  "
              f"rot中位 {np.median([r['rot'] for r in rs]):5.2f}   失败项 {allf}")
    (HERE / "family_gate.json").write_text(
        json.dumps({"clean_groups": clean, "families": FAMS, "rows": out},
                   indent=1, ensure_ascii=False))
    print(f"\n落盘 family_gate.json（真值干净组 {len(clean)}/{len(groups)}）")


if __name__ == "__main__":
    main()