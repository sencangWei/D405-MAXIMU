#!/usr/bin/env python3
"""真值 freeze-then-catchup 伪影检测 —— 全组。

为什么之前的 gt_jump_scan.py 漏了它: 那个只找**瞬时台阶**(一帧内跳变)。
tracker 的另一种失效是**先卡住再追赶**: 连续若干帧几乎不动, 随后以高于正常的
速度补回来。形状是"平台 + 爆发", 不是台阶, 所以台阶检测器看不见。

判据(不依赖任何估计轨迹, 避免循环论证):
  1. 冻结段: 连续 >=3 帧的 GT 帧位移 < 0.5 x 局部中位速率
  2. 追赶段: 紧跟其后出现 >=2 帧的位移 > 1.8 x 局部中位速率
  3. 补偿性: 冻结段实际少走的距离, 与追赶段多走的距离同量级(>50%)

三条同时成立 ⇒ 真值是"卡了又补回来", 而不是"真的停了一下"。
真实停顿不会满足第 3 条(停了就是少走, 不会超速补)。

输出同时给出: 该段若剔除后, ATE 的 max 还剩多少 —— 用于判断某组的
`ate_translation_max` 失败到底是算法缺陷还是真值伪影。
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
HALF = 150          # 局部中位速率的窗口半宽(帧)
SLOW = 0.5          # 冻结阈值(倍局部中位)
FAST = 1.8          # 追赶阈值
MIN_FREEZE = 3
MIN_BURST = 2
LOOK = 20           # 追赶段最多往后看多少帧(必须有界)


def detect(P, t):
    d = np.linalg.norm(np.diff(P, axis=0), axis=1)     # 每帧位移, 长度 N-1
    n = len(d)
    med = np.empty(n)
    for i in range(n):
        lo, hi = max(0, i - HALF), min(n, i + HALF)
        med[i] = np.median(d[lo:hi])
    slow = d < SLOW * med
    out = []
    i = 0
    while i < n:
        if not slow[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and slow[j + 1]:
            j += 1
        if j - i + 1 < MIN_FREEZE:                      # 冻结段 [i, j]
            i = j + 1
            continue
        # 参考速率: 冻结前 10 帧的局部中位(不能用冻结段自身的速率)
        lo = max(0, i - 10)
        ref = float(np.median(med[lo:i])) if i > lo else float(med[i])
        # 追赶段必须**有界**: 只往后看 LOOK 帧。
        # (无界搜索会把正常快速运动整段吃成"追赶", 并让指针跳过后续伪影)
        hi = min(n, j + 1 + LOOK)
        burst = [x for x in range(j + 1, hi) if d[x] > FAST * med[x]]
        if len(burst) < MIN_BURST:
            i = j + 1
            continue
        b0, b1 = burst[0], burst[-1]
        missed = sum(max(0.0, ref - d[x]) for x in range(i, j + 1))   # 少走的
        extra = sum(max(0.0, d[x] - ref) for x in range(b0, b1 + 1))  # 多走的
        if missed <= 1e-9 or extra / missed < 0.5:
            i = j + 1
            continue
        out.append(dict(freeze=[int(i), int(j)], burst=[int(b0), int(b1)],
                        missed_mm=missed * 1000, extra_mm=extra * 1000,
                        ratio=extra / missed, local_speed_mm=ref * 1000,
                        t0=float(t[i]), t1=float(t[min(b1 + 1, len(t) - 1)])))
        i = b1 + 1
    return out, d, med


def ate_with_mask(est, gt, drop_t):
    """剔除真值伪影时间窗后重算 ATE(只统计, 不改数据)。"""
    et, ep, eq = E.load_trajectory(est)
    rt, rp, rq = E.load_trajectory(gt)
    inside, valid, interp, iq = E.interpolate_ground_truth(et, rt, rp, rq, 0.1)
    if valid.sum() < 10:
        return None
    # 伪影窗是 GT 时间; 但要被掩掉的是**估计样本**, 所以按估计时刻判定
    # (GT 与估计样本数可以不同, 直接拿 rt 掩会广播失败)
    tt = et[inside][valid]
    keep = np.ones(len(tt), dtype=bool)
    for a, b in drop_t:
        keep &= ~((tt >= a - 0.15) & (tt <= b + 0.15))
    if keep.sum() < 10:
        return None
    P, Q, Pq = ep[inside][valid][keep], interp[keep, 1:], eq[inside][valid][keep]
    R, t = E.rigid_align(P, Q)
    d = np.linalg.norm(P @ R.T + t - Q, axis=1) * 1000
    ang = np.degrees((E.Rotation.from_quat(iq[keep]).inv()
                      * (E.Rotation.from_matrix(R)
                         * E.Rotation.from_quat(Pq))).magnitude())
    return dict(rmse=float(np.sqrt(np.mean(d ** 2))), p95=float(np.percentile(d, 95)),
                mx=float(d.max()), w10=float(np.mean(d <= 10.0) * 100),
                rot=float(np.sqrt(np.mean(ang ** 2))), n=int(keep.sum()))


rows = []
for b in BATCHES:
    for g in sorted((ROOT / b).glob("group*")):
        gt = g / "lighthouse_body_ground_truth.csv"
        if not gt.is_file():
            continue
        a = np.genfromtxt(gt, delimiter=",", names=True)
        P = np.column_stack([a["x"], a["y"], a["z"]])
        hits, d, med = detect(P, a["t_sec"])
        if not hits:
            continue
        dur = a["t_sec"][-1] - a["t_sec"][0]
        print(f"\n{b}/{g.name}   真值 {len(P)} 帧 / {dur:.1f}s   检出 {len(hits)} 段")
        for h in hits:
            print(f"   冻结 [t={h['t0']:.3f}] 帧{h['freeze'][0]}..{h['freeze'][1]}"
                  f"  追赶 帧{h['burst'][0]}..{h['burst'][1]}"
                  f"  少走 {h['missed_mm']:.1f}mm / 多走 {h['extra_mm']:.1f}mm"
                  f"  (比 {h['ratio']:.2f}, 局部速率 {h['local_speed_mm']:.2f}mm/帧)")
        row = dict(group=f"{b}/{g.name}", hits=hits)
        for sub in ("sparse", "tight"):
            f = g / "fusion" / sub / "trajectory_fused.csv"
            if not f.is_file():
                continue
            drop = [(h["t0"], h["t1"]) for h in hits]
            row[sub] = dict(raw=ate_with_mask(f, gt, []),
                            masked=ate_with_mask(f, gt, drop))
            r = row[sub]
            if r["raw"] and r["masked"]:
                print(f"   {sub:<7} 剔除伪影前 max {r['raw']['mx']:6.2f} / rmse"
                      f" {r['raw']['rmse']:5.2f}  →  剔除后 max {r['masked']['mx']:6.2f}"
                      f" / rmse {r['masked']['rmse']:5.2f}")
        rows.append(row)

outp = Path(__file__).parent / "gt_freeze_scan.json"
outp.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
n_with = len({r["group"] for r in rows})
print(f"\n{'='*100}\n检出 freeze-then-catchup 的组: {n_with}")
flip = [r for r in rows for s in ("sparse", "tight")
        if r.get(s) and r[s]["raw"] and r[s]["masked"]
        and r[s]["raw"]["mx"] > 10 >= r[s]["masked"]["mx"]]
print(f"剔除伪影后 max 由 FAIL 转 PASS 的候选: {len(flip)}")
for r in flip:
    for s in ("sparse", "tight"):
        if r.get(s) and r[s]["raw"] and r[s]["raw"]["mx"] > 10 >= r[s]["masked"]["mx"]:
            print(f"  {r['group']}/{s}: {r[s]['raw']['mx']:.2f} → {r[s]['masked']['mx']:.2f}")
print(f"\n写入 {outp}")
