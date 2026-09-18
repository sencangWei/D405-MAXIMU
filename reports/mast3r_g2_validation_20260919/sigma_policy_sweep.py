#!/usr/bin/env python3
"""融合尾段"可疑策略"跨全组扫描 —— 直接检验 §5 里标为"方向存疑"的两条策略。

背景(全部来自本目录已提交的证据):
  * MASt3R 是更好的那条链(中位 RMSE 15.91mm), VINS 更差(24.21mm);
  * 但 VINS 与视觉分歧 p95 ≥ 50mm 时, `select_visual_position_sigma`
    把**视觉** sigma 从 0.02 放宽到 0.04 —— 即分歧时反而**降低 MASt3R 的权重**,
    与上面的证据方向相反;
  * 同时 VINS 的**度量尺度不可信**(隐含 s 可低到 0.627, 中位 0.9599),
    而 `--docker2-scale-weight` 默认 0.475, 把尺度往这条链上借。

外加一条诊断项: G2 在若干组的**姿态**上比 G1 差(如 `batch5/group4/sparse`
1.90°→2.58°), 而 G1 与 G2 在**第 [7] 步**的唯一差别是 `--joint-correction-cap-mode`,
所以单变量把它退回 global, 看姿态回退是不是它造成的。

再加一组: **姿态节点密度** `--orientation-node-stride`(默认 10, 即 3Hz 姿态节点)。
动机是 36 个(代×项)里 22 项姿态超门, 且 `v11b3/group2/sparse` **只差姿态**
(2.08 / 门 2.0) —— 姿态是当前最卡边的约束, 而这条正是控制姿态节点密度的旋钮。

本扫描就测这几件事。规矩不变: 前端/标定输入逐字节冻结,
只动融合尾段参数; 全组跑, 不许为单条轨迹调参; 全部输出到 scratch。
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
SCRATCH = Path("/tmp/claude-1000/stereoab/spol")
BATCHES = [
    "20260914_validation_v10_batch",
    "20260914_validation_v10_holdout_batch2",
    "20260914_validation_v11_holdout_batch3",
    "20260915_batch5_four_videos",
    "20260915_collective_batch4",
]
LIM = dict(rmse=10.0, p95=10.0, mx=10.0, w10=95.0, rot=2.0)

CONFIGS = [
    ("A_g2_baseline", {}),                                   # 现役基线(同时当确定性对照)
    ("E_no_auto_vis_sigma",                                  # 关掉"分歧时降视觉权重"
     dict(no_auto_visual_position_sigma="1")),
    ("F_scale_w0",                                           # 尺度不向 VINS 借
     dict(docker2_scale_weight="0")),
    ("G_E+F",                                                # 两条一起关
     dict(no_auto_visual_position_sigma="1", docker2_scale_weight="0")),
    ("H_cap_global",                                         # 单变量回退 G1 的 cap 模式
     dict(joint_correction_cap_mode="global")),
    # I/J: 姿态节点密度。36 项里 22 项姿态超门, 而 v11b3/group2/sparse 只差姿态
    # (2.08 / 门 2.0) ⇒ 姿态是当前最"卡边"的约束, 值得单独摸一下它的杆杠。
    ("I_ori_stride5", dict(orientation_node_stride="5")),
    ("J_ori_stride20", dict(orientation_node_stride="20")),
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
                (SCRATCH / "spol.json").write_text(
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


if __name__ == "__main__":
    main()
