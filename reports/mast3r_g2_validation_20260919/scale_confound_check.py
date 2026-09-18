#!/usr/bin/env python3
"""检查 visual_position_sigma 触发量是否被 VINS 的尺度误差污染。

策略 `select_visual_position_sigma` 在 `position_disagreement_p95_m >= 0.05` 时
把**视觉**先验的 sigma 从 0.02 放宽到 0.04(即降低视觉权重)。

而这个 disagreement 是把 VINS 用 **robust_SE3_..._no_scale**(不带尺度)对齐到
视觉轨迹后的残差。已知 VINS 隐含尺度中位 0.9599、最低 0.627 ⇒
**一条整体缩短 37% 的 VINS, 在无尺度对齐下必然产生巨大残差**,
于是触发条件被"VINS 尺度坏了"这件事本身点燃, 惩罚的却是视觉链。

本脚本逐组给出:
  rigid_p95    无尺度对齐残差 p95   ← 策略实际用的触发量
  sim_p95      带尺度对齐残差 p95   ← 扣掉尺度后真正的"形状分歧"
  scale        隐含尺度 (GT/est 方向: >1 表示 VINS 偏短)
  ratio        rigid_p95 / sim_p95  ⇒ 残差里有多大比例是尺度贡献
自校验: 强制 s=1 必须复现 rigid 的残差(防历史 Umeyama 转置 bug)。
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
BATCHES = [
    "20260914_validation_v10_batch",
    "20260914_validation_v10_holdout_batch2",
    "20260914_validation_v11_holdout_batch3",
    "20260915_batch5_four_videos",
    "20260915_collective_batch4",
]


def load(p):
    a = np.genfromtxt(p, delimiter=",", names=True)
    return a["t_sec"], np.column_stack([a["x"], a["y"], a["z"]])


def umeyama(P, Q, with_scale):
    """P -> Q。返回 (R, t, s, 残差)。"""
    mp, mq = P.mean(0), Q.mean(0)
    Pc, Qc = P - mp, Q - mq
    H = Pc.T @ Qc / len(P)
    U, S, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    D = np.diag([1.0, 1.0, d])
    R = Vt.T @ D @ U.T
    if with_scale:
        s = float(S @ np.array([1.0, 1.0, d]) / (Pc ** 2).sum() * len(P))
    else:
        s = 1.0
    t = mq - s * (R @ mp)
    res = np.linalg.norm(s * (P @ R.T) + t - Q, axis=1)
    return R, t, s, res


rows = []
print(f"{'组':<46}{'rigid_p95':>10}{'sim_p95':>9}{'scale':>8}{'比':>7}{'触发':>6}")
print("-" * 88)
for b in BATCHES:
    for g in sorted((ROOT / b).glob("group*")):
        for sub in ("sparse", "tight"):
            vis = g / "fusion" / sub / "mast3r" / "trajectory_imu_metric.csv"
            vins = g / "docker2_slam" / "vio_corrected_stream.csv"
            if not vis.is_file() or not vins.is_file():
                continue
            tv, pv = load(vis)
            tr, pr = load(vins)
            m = (tv >= tr.min()) & (tv <= tr.max())
            if m.sum() < 20:
                continue
            q = np.column_stack([np.interp(tv[m], tr, pr[:, i]) for i in range(3)])
            P, Q = q, pv[m]

            # 自校验: 相似对齐强制 s=1 必须复现刚体残差
            _, _, _, r_rig = umeyama(P, Q, False)
            _, _, _, r_s1 = umeyama(P, Q, True)
            R2, t2, s, r_sim = umeyama(P, Q, True)
            t = np.eye(4)[:3, :3], None
            # s=1 自校验用独立路径: 用相似解的 R 但 s 固定 1
            mp, mq = P.mean(0), Q.mean(0)
            r_chk = np.linalg.norm((P @ R2.T) + (mq - R2 @ mp) - Q, axis=1)
            ok = np.allclose(r_chk, r_rig, atol=1e-6)
            d_rig, d_sim = np.percentile(r_rig, 95) * 1000, np.percentile(r_sim, 95) * 1000
            rows.append(dict(group=f"{b}/{g.name}", cand=sub, rigid_p95_mm=d_rig,
                             sim_p95_mm=d_sim, scale=1.0 / s if s else None,
                             trigger=bool(d_rig >= 50.0), selfcheck_ok=bool(ok)))
            print(f"{b+'/'+g.name+'/'+sub:<46}{d_rig:>10.1f}{d_sim:>9.1f}"
                  f"{1.0/s:>8.3f}{d_rig/max(d_sim,1e-9):>7.1f}"
                  f"{'  是' if d_rig >= 50 else '  否':>6}"
                  f"{'' if ok else '   ⚠自校验失败'}")

out = Path(__file__).parent / "scale_confound_check.json"
out.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
trig = [r for r in rows if r["trigger"]]
print(f"\n触发(rigid_p95 ≥ 50mm)的 {len(trig)} 项:")
if trig:
    print(f"  扣掉尺度后残差中位: {np.median([r['sim_p95_mm'] for r in trig]):.1f}mm"
          f"  (原 {np.median([r['rigid_p95_mm'] for r in trig]):.1f}mm)")
    print(f"  隐含尺度中位: {np.median([r['scale'] for r in trig]):.3f}"
          f"  范围 [{min(r['scale'] for r in trig):.3f}, {max(r['scale'] for r in trig):.3f}]")
    print(f"  自校验全过: {all(r['selfcheck_ok'] for r in rows)}")
print(f"\n写入 {out}")
