#!/usr/bin/env python3
"""VINS 相对米制链的**全局**尺度缺口 —— §12 建议的那条验收项的离线标定。

动机: 现有 VINS 验收门查的全是**短时相对运动**量(relative_motion_rmse 1.7–6.2mm、
p95 3.7–12.8mm, 都远低于 50mm 门), 而 `metric_scale_consistency` 只比 IMU↔双目、
**完全不含 VINS**。互补融合段的 `docker2_to_mast3r_ratio` 也是 1s 短窗量。
⇒ 整条链路没有任何一处拿 VINS 的**全局**尺度去比一个米制源。

本脚本就补这个量, 且**不碰真值**(Lighthouse 仅评测用):
    s = 相似变换(VINS 流 → MASt3R 米制轨迹) 的尺度因子
    s ≈ 1  ⇒ VINS 全局尺度与米制链一致
    s 偏离 ⇒ VINS 的**全局**延伸与米制链不符(局部仍可能很准)

用法: python3 vins_global_scale_gap.py [--json out.json]
"""
import argparse
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
BATCHES = [
    "20260914_validation_v10_batch",
    "20260914_validation_v11_holdout_batch3",
    "20260915_batch5_four_videos",
    "20260915_collective_batch4",
]
OVERLAP_MIN_S = 30.0     # 重叠太短不算
LOCAL_WINDOW_S = 1.0     # 与融合段的 --scale-horizon-s 对齐, 用于"局部"对照
LOCAL_MIN_MOVE_M = 0.005


def umeyama(P, Q):
    """求 (s, R, t) 使 (s*R@P.T).T + t ≈ Q。已用合成数据自校验(见 self_check)。"""
    mp, mq = P.mean(0), Q.mean(0)
    sigma = ((Q - mq).T @ (P - mp)) / len(P)
    U, D, Vt = np.linalg.svd(sigma)
    S = np.eye(3)
    S[2, 2] = 1.0 if np.linalg.det(U) * np.linalg.det(Vt) >= 0 else -1.0
    R = U @ S @ Vt
    s = np.trace(np.diag(D) @ S) / ((P - mp) ** 2).sum(1).mean()
    return float(s), R, mq - s * R @ mp


def apply(P, s, R, t):
    return (s * R @ P.T).T + t


def self_check():
    """造已知 s/R/t 再回收 —— 历史上这里踩过 Umeyama 转置的坑, 必须每次自校验。"""
    rng = np.random.default_rng(0)
    P = rng.normal(size=(200, 3))
    truth = Rotation.random(random_state=1)
    Q = 1.37 * (truth.as_matrix() @ P.T).T + np.array([1.0, -2.0, 0.5])
    s, R, t = umeyama(P, Q)
    err = float(np.abs(apply(P, s, R, t) - Q).max())
    ang = float(np.degrees((truth.inv() * Rotation.from_matrix(R)).magnitude()))
    if abs(s - 1.37) > 1e-9 or err > 1e-9 or ang > 1e-9:
        raise RuntimeError(f"Umeyama 自校验失败: s={s} 角={ang} 残差={err}")
    return dict(scale=1.37, recovered=s, rotation_error_deg=ang, residual_m=err)


def load(path):
    a = np.genfromtxt(path, delimiter=",", names=True)
    return a["t_sec"], np.stack([a["x"], a["y"], a["z"]], 1)


def compare(vins_path, metric_path):
    tv, pv = load(vins_path)
    tm, pm = load(metric_path)
    t0, t1 = max(tv[0], tm[0]), min(tv[-1], tm[-1])
    if t1 - t0 < OVERLAP_MIN_S:
        return None
    tt = np.arange(t0, t1, 0.05)
    V = np.stack([np.interp(tt, tv, pv[:, k]) for k in range(3)], 1)
    M = np.stack([np.interp(tt, tm, pm[:, k]) for k in range(3)], 1)
    s, R, t = umeyama(V, M)
    res = np.linalg.norm(apply(V, s, R, t) - M, axis=1)
    # 局部对照: 与 --scale-horizon-s 同宽的窗口内位移比, 取中位
    n = max(1, int(LOCAL_WINDOW_S / 0.05))
    loc = []
    for i in range(0, len(tt) - n, n):
        dv = float(np.linalg.norm(V[i + n] - V[i]))
        dm = float(np.linalg.norm(M[i + n] - M[i]))
        if dv > LOCAL_MIN_MOVE_M:
            loc.append(dm / dv)
    return dict(
        global_scale=s,
        local_scale_median=float(np.median(loc)) if loc else None,
        fit_p95_m=float(np.percentile(res, 95)),
        fit_max_m=float(res.max()),
        overlap_s=float(t1 - t0),
        samples=int(len(tt)),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    chk = self_check()
    print(f"[自校验] Umeyama 回收 s={chk['recovered']:.9f} (真值 1.37), "
          f"旋转误差 {chk['rotation_error_deg']:.2e}°, 残差 {chk['residual_m']:.2e} m\n")

    rows = []
    print(f"{'组':<42}{'档':<7}{'全局尺度':>10}{'局部比值':>10}{'拟合p95(m)':>12}"
          f"{'重叠(s)':>9}   判读")
    for b in BATCHES:
        for g in sorted((ROOT / b).glob("group*")):
            ra = g / "docker2_slam" / "run_acceptance.json"
            if not ra.is_file():
                continue
            if json.loads(ra.read_text()).get("result") != "PASS":
                continue
            for sub in ("sparse", "tight"):
                mp = g / "fusion" / sub / "mast3r" / "trajectory_imu_metric.csv"
                vp = g / "docker2_slam" / "vio_corrected_stream.csv"
                if not (mp.is_file() and vp.is_file()):
                    continue
                r = compare(vp, mp)
                if r is None:
                    continue
                r.update(group=f"{b}/{g.name}", cand=sub)
                rows.append(r)
                flag = ("★全局偏离>10%" if abs(r["global_scale"] - 1) > 0.10
                        else ("全局偏离>5%" if abs(r["global_scale"] - 1) > 0.05 else ""))
                ls = r["local_scale_median"]
                print(f"{r['group']:<42}{sub:<7}{r['global_scale']:>10.4f}"
                      f"{(ls if ls else 0):>10.4f}{r['fit_p95_m']:>12.4f}"
                      f"{r['overlap_s']:>9.1f}   {flag}")

    v = np.array([r["global_scale"] for r in rows])
    print(f"\nn={len(v)}  全局尺度: 中位 {np.median(v):.4f}  min {v.min():.4f}  "
          f"max {v.max():.4f}")
    print(f"  偏离>5% 的项 {int((np.abs(v-1)>0.05).sum())}/{len(v)};  "
          f"偏离>10% 的项 {int((np.abs(v-1)>0.10).sum())}/{len(v)}")
    print("\n注意: 本脚本只报数, 不建议阈值。门限必须在这份分布上标定, 而不是拍脑袋;"
          "\n      且 §12.1 已说明 —— 融合段对个别 VINS 全局尺度崩坏是能兜住的,"
          "\n      所以先当**诊断/告警**用, 只有当某组因此真的产出坏轨迹时才升级为硬拒。")
    if a.json:
        a.json.write_text(json.dumps(dict(self_check=chk, rows=rows),
                                     ensure_ascii=False, indent=1))
        print(f"\n已写 {a.json}")


if __name__ == "__main__":
    main()
