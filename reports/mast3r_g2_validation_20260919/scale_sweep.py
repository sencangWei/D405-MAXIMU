#!/usr/bin/env python3
"""`--docker2-scale-weight` 往上扫 —— 补上 §11 没覆盖的那半根轴。

动机(全部来自本目录已提交的证据):
  * 已定位的**上游瓶颈是 VINS 的度量尺度**(隐含 s 可低到 0.627, 中位 0.9599),
    而 VINS 的验收门只查自洽性、对全局尺度失明(见 §12);
  * `--docker2-scale-weight` 正是"把尺度往 VINS 这条链借多少"的旋钮;
  * §11 的 F 组只测了 **归零**(w=0) —— 结果更差(max 中位 14.57→15.64)
    ⇒ 借尺度这件事本身是赚钱的;
  * 于是唯一还没测的方向是: **借得更多会不会更好**。0.475 → 0.70 / 0.85 / 1.00。

注意 w=1.00 不等于"完全采信 VINS 的尺度": 该权重作用在 `--scale-horizon-s 1`
的**短时窗**尺度上, 动的是局部尺度而不是全局仿射 —— 所以即使 VINS 全局尺度漂了,
局部尺度仍可能比 MASt3R 的更贴 IMU。这正是要测的东西。

规矩不变: 前端/标定输入逐字节冻结, 只动融合尾段参数; 全组跑,
不许为单条轨迹调参; 全部输出到 scratch。
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
SCRATCH = Path("/tmp/claude-1000/stereoab/scale")
BATCHES = [
    "20260914_validation_v10_batch",
    "20260914_validation_v10_holdout_batch2",
    "20260914_validation_v11_holdout_batch3",
    "20260915_batch5_four_videos",
    "20260915_collective_batch4",
]
LIM = dict(rmse=10.0, p95=10.0, mx=10.0, w10=95.0, rot=2.0)

CONFIGS = [
    ("A_g2_baseline", {}),                                # 现役: 无条件借 0.475
    ("N_scale_w0.70", dict(docker2_scale_weight="0.70")),  # 借更多
    ("P_scale_w1.00", dict(docker2_scale_weight="1.00")),  # 全借(短时窗尺度)
    # Q: 代码里有、现网没启用的 `--auto-docker2-scale-weight` —— 条件化尺度投票。
    # 它只在 (双目/IMU 尺度一致 ≤3%) 且 (VINS 那一票有信息量, 跨链尺度差 ≥2%)
    # 且 (两链形状相容 ≤50mm) 且 (双目几何可靠 ≤4mm) 时才借 VINS 尺度, 否则权重取 0。
    #
    # 跑之前的预判(由已归档报告算得, 见 §13): 18 项里 auto 只在 **3 项**触发
    # (batch5/group1/sparse、group4/sparse、group4/tight), 其余 15 项退化成
    # "权重 0", 而 §11 的 F 组已实测"权重 0"在中位/最差上都更差 ⇒ Q 大概率不如 A。
    # 仍要真跑, 因为"预判"不是"实测"; 且 3 项触发点正好是跨链尺度差最大的那几项。
    ("Q_auto_scale0.475",
     dict(auto_docker2_scale_weight="1")),
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
        return False
    if not (g / "fusion" / sub / "mast3r" / "graph_fusion_report.json").is_file():
        return False
    if not (g / "fusion" / sub / "mast3r" / "trajectory_imu_metric.csv").is_file():
        return False
    ra = g / "docker2_slam" / "run_acceptance.json"
    if ra.is_file() and json.loads(ra.read_text()).get("result") != "PASS":
        return False
    return True


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
                if not preflight(g, sub):
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
                (SCRATCH / "scale.json").write_text(
                    json.dumps(rows, ensure_ascii=False, indent=1))

    print(f"\n{'='*130}\n汇总(仅统计有分数的项)")
    print(f"{'配置':<24}{'可比':>5}{'达标':>6}{'QGATE':>7}{'max中位':>9}"
          f"{'rmse中位':>10}{'max最差':>9}{'rot中位':>9}")
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
              f"{max(v['mx'] for v in sc):>9.2f}"
              f"{np.median([v['rot'] for v in sc]):>9.2f}")

    # 逐项 delta vs A: 只看有分数且同项可比的行, 直接回答"借更多尺度是赚是亏"
    print(f"\n逐项 delta (max, mm; 负 = 比基线好)")
    base = {f"{r['group']}/{r['cand']}": r["configs"]["A_g2_baseline"].get("score")
            for r in rows if "A_g2_baseline" in r["configs"]}
    for name, _ in CONFIGS:
        if name == "A_g2_baseline":
            continue
        cells = []
        for r in rows:
            k = f"{r['group']}/{r['cand']}"
            a, c = base.get(k), r["configs"].get(name, {}).get("score")
            if a and c:
                cells.append(c["mx"] - a["mx"])
        if cells:
            cells = np.array(cells)
            print(f"{name:<22} n={len(cells):<4} 改善 {int((cells < 0).sum()):>3} / "
                  f"恶化 {int((cells > 0).sum()):>3} / 持平 {int((cells == 0).sum()):>3}"
                  f"   中位 {np.median(cells):>+7.2f}   最好 {cells.min():>+7.2f}"
                  f"   最差 {cells.max():>+7.2f}")


if __name__ == "__main__":
    main()
