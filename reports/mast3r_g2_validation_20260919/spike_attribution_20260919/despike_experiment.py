#!/usr/bin/env python3
"""A 类那 5 个 cell 能不能用【时间维鲁棒性】换过来。

## 为什么是这个实验

`gate_failure_modes.py` 分出两个物种：A 类是 1–2 帧的孤立跳变（去掉最坏 5 个
样本后 5/5 全部 <10mm），B 类是一整段结构性跑偏（去掉 5 个纹丝不动）。
A 类的药方只能是**时间维**。而尾段扫描的 6 个配置里，`smoothing_s` 是**融合段**
的窗口参数，**没有一个**是"对最终轨迹去尖峰"。

## 做法

起点是扫描重跑的 `A_G2_current/<sub>/trajectory_fused_unsmoothed.csv`（[8/9] 的输出），
自己施加滤波器，再用**扫描器自己的 `score()`** 打分（口径逐位一致）：

- `raw`        不滤波（[8/9] 原始）
- `gauss_*`    高斯平滑，sigma ∈ {0.025(现役 [9/9]), 0.05, 0.1, 0.2} 秒
- `hampel_w*`  Hampel 去尖峰（中位+MAD，只替换离群点）后再 gauss 0.025
- `median_w*`  中位滤波后 gauss 0.025

**全部无监督**：只用轨迹自身，不碰真值。所以真的有用就是能落地的。

只读产物 CSV + GT，不跑管线。
"""
import json
import sys
from pathlib import Path

import numpy as np

SWEEP_DIR = Path("/tmp/claude-1000/stereoab/recover0914")
ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
REPORT = Path("/home/robot/ego_vio_humble/reports/mast3r_g2_validation_20260919")
OUT = Path(__file__).parent
TMP = OUT / "_despike_tmp.csv"

sys.path.insert(0, str(REPORT))
sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import recover0914_sweep as SW  # noqa: E402  拿到 score() 与 LIM，保证口径一致


def load_csv(path):
    a = np.genfromtxt(path, delimiter=",", names=True)
    t = np.asarray(a["t_sec"], float)
    p = np.stack([a["x"], a["y"], a["z"]], 1).astype(float)
    q = np.stack([a["qw"], a["qx"], a["qy"], a["qz"]], 1).astype(float)
    return t, p, q


def write_csv(path, t, p, q):
    out = np.column_stack([t, p, q])
    np.savetxt(path, out, delimiter=",", header="t_sec,x,y,z,qw,qx,qy,qz", comments="")


def gauss(t, x, sigma):
    """按时间加权的高斯平滑（容忍非均匀采样）。x 可以是 (n,3) 或 (n,4)。"""
    if sigma <= 0:
        return x.copy()
    out = np.empty_like(x)
    for i in range(len(t)):
        w = np.exp(-0.5 * ((t - t[i]) / sigma) ** 2)
        w[w < 1e-6] = 0.0
        if w.sum() <= 0:
            out[i] = x[i]
            continue
        blk = x[w > 0]
        ww = w[w > 0]
        if x.shape[1] == 4:  # 四元数先对齐符号再平均
            ref = x[i]
            blk = np.where((blk @ ref)[:, None] < 0, -blk, blk)
        out[i] = (blk * ww[:, None]).sum(0) / ww.sum()
    if x.shape[1] == 4:
        out /= np.linalg.norm(out, axis=1, keepdims=True)
    return out


def median_filt(x, w):
    """滑动中位（窗口 w 取奇数）。"""
    if w <= 1:
        return x.copy()
    h = w // 2
    pad = np.pad(x, ((h, h), (0, 0)), mode="edge")
    return np.stack([np.median(pad[i:i + w], axis=0) for i in range(len(x))])


def hampel(x, w, n_sigma=3.0):
    """只替换离群点：|x_i − med| > n_sigma·1.4826·MAD ⇒ 用中位替换。"""
    h = w // 2
    pad = np.pad(x, ((h, h), (0, 0)), mode="edge")
    med = np.stack([np.median(pad[i:i + w], axis=0) for i in range(len(x))])
    mad = np.stack([np.median(np.abs(pad[i:i + w] - med[i]), axis=0) for i in range(len(x))])
    bad = np.abs(x - med) > n_sigma * 1.4826 * mad
    out = x.copy()
    out[bad.any(axis=1)] = med[bad.any(axis=1)]
    return out


VARIANTS = [
    ("raw", lambda t, p, q: (p, q, 0.0)),
    ("gauss_0.025(现役)", lambda t, p, q: (p, q, 0.025)),
    ("gauss_0.05", lambda t, p, q: (p, q, 0.05)),
    ("gauss_0.1", lambda t, p, q: (p, q, 0.1)),
    ("gauss_0.2", lambda t, p, q: (p, q, 0.2)),
    ("hampel_w3", lambda t, p, q: (hampel(p, 3), q, 0.025)),
    ("hampel_w5", lambda t, p, q: (hampel(p, 5), q, 0.025)),
    ("hampel_w9", lambda t, p, q: (hampel(p, 9), q, 0.025)),
    ("median_w3", lambda t, p, q: (median_filt(p, 3), q, 0.025)),
    ("median_w5", lambda t, p, q: (median_filt(p, 5), q, 0.025)),
]


def cells():
    for p in sorted(SWEEP_DIR.glob("*/*/A_G2_current/*/trajectory_fused_unsmoothed.csv")):
        sub = p.parent.name
        grp = p.parent.parent.parent
        gt = ROOT / grp.parent.name / grp.name / "lighthouse_body_ground_truth.csv"
        if gt.is_file():
            yield f"{grp.parent.name}/{grp.name}/{sub}", p, gt


def main():
    rows = []
    for cid, unsmoothed, gt in cells():
        t, p, q = load_csv(unsmoothed)
        res = {}
        for name, fn in VARIANTS:
            pp, qq, sigma = fn(t, p, q)
            pp = gauss(t, pp, sigma)
            qq = gauss(t, qq, sigma)
            write_csv(TMP, t, pp, qq)
            res[name] = SW.score(TMP, gt)
        rows.append((cid, res))
        print(f"{cid}")

    print()
    print("=" * 108)
    hdr = f"{'cell':<44}" + "".join(f"{n.split('(')[0]:>13}" for n, _ in VARIANTS)
    print(hdr)
    print("-" * 108)
    for cid, res in rows:
        cells_txt = ""
        for name, _ in VARIANTS:
            m = res[name]
            cells_txt += f"{m['mx']:>7.2f}{'✓' if m['pass'] else '✗':>6}" if m else f"{'—':>13}"
        print(f"{cid:<44}{cells_txt}")

    print()
    print("汇总（每列：达标 cell 数 / 中位 max / max 超 10mm 的 cell 数）")
    print("-" * 108)
    for name, _ in VARIANTS:
        ms = [res[name] for _, res in rows if res[name]]
        if not ms:
            continue
        npass = sum(1 for m in ms if m["pass"])
        mx = np.array([m["mx"] for m in ms])
        rot = np.array([m["rot"] for m in ms])
        print(f"  {name:<20} 达标 {npass:2d}/{len(ms):<3}  "
              f"中位 max {np.median(mx):6.2f}  max>10 的 {int((mx > 10).sum()):2d}/{len(ms)}  "
              f"中位 rot {np.median(rot):5.2f}")

    TMP.unlink(missing_ok=True)
    (OUT / "despike_experiment.json").write_text(json.dumps(
        [(cid, {k: v for k, v in res.items()}) for cid, res in rows],
        ensure_ascii=False, indent=1, default=float))
    print(f"\n已写 {OUT / 'despike_experiment.json'}")


if __name__ == "__main__":
    main()
