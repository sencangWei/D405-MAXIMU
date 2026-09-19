#!/usr/bin/env python3
"""MASt3R 生产链的【真实拓扑】与各步职责 —— 2026-09-19 实测更正。

## 为什么写这个脚本

09-19 分析尖峰归属时, 我把产物目录里的四个 trajectory_*.csv 当成了**四条平行链**:

    trajectory_frames.csv        "原始 MASt3R"
    trajectory_graph.csv         "图优化"
    trajectory_imu_metric.csv    "图+IMU度量"   <- 错
    trajectory_fused.csv         "融合"

据此得出"②→③ 让误差从 32.27 恶化到 41.81"的结论, 并去追"这一步为什么改形状"。
**两个都错了**:

1. 它们是**一条链**, 不是兄弟。读 `scripts/mast3r_slam_precision_workflow.sh` 即知:
       [6/8] align_mast3r_scale_with_imu.py  --trajectory trajectory_frames.csv
                                            --output     trajectory_imu_metric.csv
       [7/8] fuse_mast3r_stereo_imu.py      --trajectory trajectory_imu_metric.csv
                                            --output     trajectory_graph.csv
       [8/9] fuse_docker2_mast3r_complementary.py --mast3r trajectory_graph.csv
                                            --output     .../trajectory_fused_unsmoothed.csv
       [9/9] smooth_pose_trajectory.py      --input  fused_unsmoothed --output trajectory_fused.csv
   ⇒ `imu_metric` 是 `graph` 的**输入**, `graph` 是融合的输入。
   "②→③ 恶化"其实是**把管线读反了**。

2. `rigid_align` **不估尺度**, 所以它的残差**强烈依赖输入的绝对尺度**。
   原始帧轨迹比真值大 2.8 倍, 那 810mm "误差"绝大部分是"太大"贡献的, **不是形状**。
   要量形状必须先按相似变换把尺度归到 1.0 再 rigid_align。

## 本脚本量出来的三件事

1. `trajectory_imu_metric.csv` == `trajectory_frames.csv` × **一个标量**, 逐点残差 0.0000mm。
   ⇒ [6/8] 这一步**不做任何形状工作**, 它的全部作用就是乘一个数。
2. 尺度归一后的**真形状误差**: raw/imu_metric 逐位相同, graph 才真正改形状。
3. [6/8] 乘出来的轨迹**并不是度量的**: 对真值的相似尺度 0.90–1.08, 中位偏大 +5.6%。
   而同一轮里还算并喂给了 [7/8] 四份双目直接度量报告。

只用只读的产物 CSV + 官方评测器, 不跑管线, 不改任何东西。
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")


def cells():
    """遍历所有 (batch/group/sub) 且四件套齐备的 cell。"""
    for b in sorted(ROOT.glob("2026*")):
        for g in sorted(b.glob("group*")):
            if not (g / "lighthouse_body_ground_truth.csv").is_file():
                continue
            for sub in ("sparse", "tight"):
                M = g / "fusion" / sub / "mast3r"
                if (M / "trajectory_frames.csv").is_file():
                    yield f"{b.name}/{g.name}/{sub}", g, M


def pure_scale_residual(a, b):
    """b 是否是 a 的纯缩放? 返回 (k, 逐点残差 mm)。"""
    a = a - a.mean(0)
    b = b - b.mean(0)
    k = float((a * b).sum() / (a * a).sum())
    return k, np.linalg.norm(k * a - b, axis=1) * 1000


def score(P, ts, gt):
    """形状误差: 先按相似变换归一尺度, 再 rigid_align 取 max/中位。"""
    rt, rp, rq = E.load_trajectory(gt)
    ins, val, itp, iq = E.interpolate_ground_truth(ts, rt, rp, rq, 0.1)
    if val.sum() < 10:
        return None
    Pv, Q = P[ins][val], itp[:, 1:]
    s, _, _ = E.similarity_align(Pv, Q)
    R, t = E.rigid_align(Pv * s, Q)
    d = np.linalg.norm(Pv * s @ R.T + t - Q, axis=1) * 1000
    return dict(scale_to_gt=float(s), shape_max=float(d.max()),
                shape_med=float(np.median(d)))


def main():
    print("=" * 84)
    print("①  imu_metric 是否恒等于 frames × 常数 (纯缩放, 无形状工作)")
    print("=" * 84)
    ok = n = 0
    worst = 0.0
    for gid, g, M in cells():
        t1, p1, _ = E.load_trajectory(M / "trajectory_frames.csv")
        t2, p2, _ = E.load_trajectory(M / "trajectory_imu_metric.csv")
        if len(p1) != len(p2):
            print(f"  跳过 {gid}: 点数不同 {len(p1)} vs {len(p2)}")
            continue
        n += 1
        k, r = pure_scale_residual(p1, p2)
        worst = max(worst, r.max())
        if r.max() < 0.01:
            ok += 1
        else:
            print(f"  ✗ {gid:<46} k={k:.6f} 残差 max {r.max():.3f}mm")
    print(f"  ⇒ {ok}/{n} 个 cell 逐点残差 < 0.01mm (全局最差 {worst:.4f}mm)")
    print("  ⇒ [6/8] 的产出 = 输入 × 一个标量。不做形状工作。")

    print()
    print("=" * 84)
    print("②  尺度归一后的【真形状误差】(09-14 batch3/group2/tight)")
    print("=" * 84)
    target = ROOT / "20260914_validation_v11_holdout_batch3" / "group2"
    gt = target / "lighthouse_body_ground_truth.csv"
    M = target / "fusion" / "tight" / "mast3r"
    rows = [("raw  frames", M / "trajectory_frames.csv"),
            ("imu_metric", M / "trajectory_imu_metric.csv"),
            ("graph", M / "trajectory_graph.csv"),
            ("fused", target / "fusion" / "tight" / "trajectory_fused.csv")]
    for lab, p in rows:
        if not p.is_file():
            print(f"  {lab:<12} 缺 {p.name}")
            continue
        ts, P, _ = E.load_trajectory(p)
        m = score(P, ts, gt)
        print(f"  {lab:<12} 对真值尺度 {m['scale_to_gt']:.4f} "
              f"(偏大 {(1 / m['scale_to_gt'] - 1) * 100:+5.1f}%)   "
              f"形状 max {m['shape_max']:6.2f}mm  中位 {m['shape_med']:5.2f}mm")
    print("  ⇒ raw 与 imu_metric 形状【逐位相同】—— 再次证明 [6/8] 只缩放。")
    print("  ⇒ graph 才真正改形状; fused 同时修尺度。")

    print()
    print("=" * 84)
    print("③  [6/8] 产出的轨迹是否真的度量 (对真值相似尺度, 1.0 = 真度量)")
    print("=" * 84)
    out = []
    for gid, g, M in cells():
        gt = g / "lighthouse_body_ground_truth.csv"
        ts, P, _ = E.load_trajectory(M / "trajectory_imu_metric.csv")
        m = score(P, ts, gt)
        if not m:
            continue
        rep = M / "imu_scale_report.json"
        k_rep = json.loads(rep.read_text()).get("scale") if rep.is_file() else None
        out.append(dict(cell=gid, scale_to_gt=m["scale_to_gt"], k_applied=k_rep))
        flag = "" if abs(m["scale_to_gt"] - 1.0) < 0.02 else "  ← 偏"
        print(f"  {gid:<48} 对真值 {m['scale_to_gt']:.4f} "
              f"(偏大 {(1 / m['scale_to_gt'] - 1) * 100:+5.1f}%)  "
              f"k_applied={k_rep}{flag}")
    a = np.array([r["scale_to_gt"] for r in out])
    big = int((a < 1.0).sum())
    print(f"\n  ⇒ {len(a)} 个 cell: 中位 {np.median(a):.4f}, 范围 {a.min():.4f}–{a.max():.4f}")
    print(f"  ⇒ {big}/{len(a)} 个 cell 偏大 (尺度 < 1.0), "
          f"最大偏大 {(1 / a.min() - 1) * 100:.1f}%")
    print("  ⇒ [6/8] 名为『度量』, 产出却不是度量轨迹。这只是现状记录, 不是改法建议。")

    outdir = Path(__file__).parent
    (outdir / "chain_topology.json").write_text(
        json.dumps(dict(pure_scale=dict(ok=ok, n=n, worst_mm=worst),
                        metric_scale=out), ensure_ascii=False, indent=1))
    print(f"\n  已写 {outdir / 'chain_topology.json'}")


if __name__ == "__main__":
    main()
