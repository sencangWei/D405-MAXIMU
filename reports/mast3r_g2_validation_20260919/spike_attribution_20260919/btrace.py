#!/usr/bin/env python3
"""把「看帧」得到的印象做成数：**画面本身**能不能解释误差。

## 印象（`bframes.py` 的 52 张图）

- `batch5/g3`、`v10/g2`：误差大的时刻画面**几乎全是空白木桌面**（低纹理），
  而对照帧是**瓶子充满画面**（纹理丰富）。且误差大的时刻**相机几乎静止**（1.8–3.2 mm/s）。
- `v10b2/g1`：**反过来** —— 误差大的时刻是**显示器充满画面**（终端在滚、右上角还开着
  D405 自己的实时预览），对照帧才是桌面。

两个 take 的画面结论**互相矛盾**，所以"低纹理"不是单一解释。本脚本上数。

## 三个无监督画面量（都用不到真值）

- `orb`    ORB 特征点数（半分辨率）—— 纹理/可匹配性的直接代理
- `grad`   梯度能量（Sobel 均方）—— 对比度/细节量
- `dI`     相邻帧灰度平均绝对差 —— **相机不动时画面变了多少**

`dI` 是关键：**相机静止时画面还在变 ⇒ 场景是动态的**（屏幕在滚、有人在动、反光在晃）。
SLAM/MASt3R 的静态刚体假设直接破。

## 怎么排除「动作」这个混杂因子

误差本来就与动作强相关（[[spike-attribution-20260919]]），而动作又与取景相关。
所以**在低运动帧内部分层**：只看真值线速度 < 15 mm/s 的帧，
在这个子集里再看误差与画面量的关系 —— 动作被固定在"几乎不动"上。

只读 db3 + 产物 CSV + GT，不跑管线。
"""
import csv
import json
import sqlite3
import struct
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
OUT = Path(__file__).parent

TOPIC_RGB = 69
STILL_MM_S = 15.0          # "几乎静止"的门
ORB = cv2.ORB_create(nfeatures=2000, fastThreshold=12)

CELLS = [
    ("v10/g1",    "20260914_validation_v10_batch",          "group1", "tight"),
    ("v10/g2",    "20260914_validation_v10_batch",          "group2", "tight"),
    ("v10b2/g1",  "20260914_validation_v10_holdout_batch2", "group1", "tight"),
    ("v10b2/g2",  "20260914_validation_v10_holdout_batch2", "group2", "tight"),
    ("v11b3/g1",  "20260914_validation_v11_holdout_batch3", "group1", "tight"),
    ("v11b3/g2",  "20260914_validation_v11_holdout_batch3", "group2", "tight"),
    ("batch5/g1", "20260915_batch5_four_videos",            "group1", "tight"),
    ("batch5/g2", "20260915_batch5_four_videos",            "group2", "tight"),
    ("batch5/g3", "20260915_batch5_four_videos",            "group3", "tight"),
    ("batch5/g4", "20260915_batch5_four_videos",            "group4", "tight"),
    ("coll/g1",   "20260915_collective_batch4",             "group1", "tight"),
    ("coll/g2",   "20260915_collective_batch4",             "group2", "tight"),
]


def cdr_string(b, o):
    n = struct.unpack_from("<I", b, o)[0]
    o += 4
    s = b[o:o + n - 1].decode("utf-8", "replace")
    o += n
    return s, o + (-o) % 4


def parse_image(b):
    o = 4 + 8
    _f, o = cdr_string(b, o)
    h, w = struct.unpack_from("<II", b, o)
    o += 8
    enc, o = cdr_string(b, o)
    n = h * w * (2 if enc.upper().startswith("YUY") else 1)
    return h, w, enc, b[len(b) - n:]


def scan_rgb(db3, ids):
    """一遍过出全部 RGB 帧的三个画面量。"""
    orb, grad, grays = [], [], []
    con = sqlite3.connect(f"file:{db3}?mode=ro&immutable=1", uri=True)
    for k in ids:
        h, w, enc, raw = parse_image(bytes(
            con.execute("SELECT data FROM messages WHERE id=?", (k,)).fetchone()[0]))
        a = np.frombuffer(raw, np.uint8)
        img = cv2.cvtColor(a.reshape(h, w, 2), cv2.COLOR_YUV2BGR_YUYV)
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        half = cv2.resize(g, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
        orb.append(len(ORB.detect(half, None)))
        gx = cv2.Sobel(half, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(half, cv2.CV_32F, 0, 1, ksize=3)
        grad.append(float(np.mean(gx * gx + gy * gy)))
        grays.append(cv2.resize(g, (320, 180), interpolation=cv2.INTER_AREA).astype(np.int16))
    con.close()
    G = np.stack(grays)
    dI = np.concatenate([[0.0], np.abs(np.diff(G, axis=0)).mean(axis=(1, 2))])
    return np.array(orb, float), np.array(grad, float), dI


def main():
    pooled, per_take = [], []
    for label, batch, group, sub in CELLS:
        gt = ROOT / batch / group / "lighthouse_body_ground_truth.csv"
        est = ROOT / batch / group / "fusion" / sub / "trajectory_fused.csv"
        prov = ROOT / batch / group / "lighthouse_ground_truth_provenance.json"
        if not (gt.is_file() and est.is_file() and prov.is_file()):
            print(f"⚠ 缺 {label}")
            continue
        p = json.loads(prov.read_text())["clock_mapping"]
        frames_csv = Path(p["d405_frames"])
        ep2mono = float(p["epoch_minus_monotonic_s"])
        mono = np.array([float(r["color_mono"]) for r in csv.DictReader(open(frames_csv, newline=""))])
        db3 = frames_csv.parent / "d405_720p_rgb_stereo_ir.db3"
        con = sqlite3.connect(f"file:{db3}?mode=ro&immutable=1", uri=True)
        ids = [r[0] for r in con.execute("SELECT id FROM messages WHERE topic_id=? ORDER BY id",
                                         (TOPIC_RGB,))]
        con.close()
        orb, grad, dI = scan_rgb(db3, ids)

        et, ep, eq = E.load_trajectory(est)
        rt, rp, rq = E.load_trajectory(gt)
        ins, val, itp, iq = E.interpolate_ground_truth(et, rt, rp, rq, 0.1)
        ts, P, Q, Gq = et[ins][val], ep[ins][val], itp[:, 1:], E.Rotation.from_quat(iq)
        R, t = E.rigid_align(P, Q)
        err = np.linalg.norm(P @ R.T + t - Q, axis=1) * 1000
        spd = np.concatenate([[0.0], np.linalg.norm(np.diff(P, axis=0), axis=1)
                              / np.diff(ts) * 1000])
        idx = np.array([int(np.argmin(np.abs(mono - (x - ep2mono)))) for x in ts])
        o, gr, di = orb[idx], grad[idx], dI[idx]
        still = spd < STILL_MM_S
        r = dict(cell=label, n=len(err), n_still=int(still.sum()),
                 err_med=float(np.median(err)), err_max=float(err.max()),
                 still_err_med=float(np.median(err[still])) if still.any() else None,
                 corr_all_orb=float(np.corrcoef(o, err)[0, 1]),
                 corr_all_dI=float(np.corrcoef(di, err)[0, 1]))
        if still.sum() > 20:
            r.update(corr_still_orb=float(np.corrcoef(o[still], err[still])[0, 1]),
                     corr_still_dI=float(np.corrcoef(di[still], err[still])[0, 1]))
            q = np.quantile(o[still], [1 / 3, 2 / 3])
            lo, hi = still & (o <= q[0]), still & (o > q[1])
            r.update(still_lowtex_err=float(np.median(err[lo])),
                     still_hightex_err=float(np.median(err[hi])),
                     still_lowtex_n=int(lo.sum()), still_hightex_n=int(hi.sum()))
            q2 = np.quantile(di[still], [1 / 3, 2 / 3])
            lo2, hi2 = still & (di <= q2[0]), still & (di > q2[1])
            r.update(still_dyn_err=float(np.median(err[hi2])),
                     still_static_err=float(np.median(err[lo2])))
        per_take.append(r)
        pooled.append((err, o, gr, di, spd, still))
        print(f"{label:<11} n={r['n']:<5} 静止帧 {r['n_still']:<5} "
              f"静止内中位误差 {r['still_err_med']:.2f}mm" if r["still_err_med"] is not None
              else f"{label:<11} n={r['n']:<5} 静止帧 0")

    print()
    print("=" * 110)
    print("① 全帧相关（含运动混杂）：画面量 vs 误差")
    print("-" * 110)
    print(f"{'cell':<11}{'err中位':>9}{'静态帧':>8}{'corr(orb,err)':>14}{'corr(dI,err)':>13}")
    for r in per_take:
        print(f"{r['cell']:<11}{r['err_med']:9.2f}{r['n_still']:8d}"
              f"{r['corr_all_orb']:14.3f}{r['corr_all_dI']:13.3f}")
    c = np.array([[r["corr_all_orb"], r["corr_all_dI"]] for r in per_take])
    print(f"  中位: corr(orb,err) {np.median(c[:, 0]):+.3f}   corr(dI,err) {np.median(c[:, 1]):+.3f}")

    print()
    print("=" * 110)
    print(f"② ★ 只在【几乎静止】(真值线速度<{STILL_MM_S:.0f}mm/s) 的帧内部分层 —— 动作被固定住")
    print("-" * 110)
    print(f"{'cell':<11}{'静止帧':>7}{'静止中位':>9}{'corr(orb)':>10}{'corr(dI)':>10}"
          f"{'低纹理':>9}{'高纹理':>9}{'静态画面':>10}{'动态画面':>10}")
    for r in per_take:
        if "corr_still_orb" not in r:
            print(f"{r['cell']:<11}{r['n_still']:7d}  （静止帧太少）")
            continue
        print(f"{r['cell']:<11}{r['n_still']:7d}{r['still_err_med']:9.2f}"
              f"{r['corr_still_orb']:10.3f}{r['corr_still_dI']:10.3f}"
              f"{r['still_lowtex_err']:9.2f}{r['still_hightex_err']:9.2f}"
              f"{r['still_static_err']:10.2f}{r['still_dyn_err']:10.2f}")

    ok = [r for r in per_take if "corr_still_orb" in r]
    if ok:
        A = np.array([[r["corr_still_orb"], r["corr_still_dI"],
                       r["still_lowtex_err"] - r["still_hightex_err"],
                       r["still_dyn_err"] - r["still_static_err"]] for r in ok])
        print()
        print(f"  n={len(ok)} 个 take")
        print(f"  静止帧内 corr(纹理, 误差) 中位 {np.median(A[:, 0]):+.3f}"
              f"   为负的 {int((A[:, 0] < 0).sum())}/{len(ok)}")
        print(f"  静止帧内 corr(画面变化, 误差) 中位 {np.median(A[:, 1]):+.3f}"
              f"   为正的 {int((A[:, 1] > 0).sum())}/{len(ok)}")
        print(f"  低纹理误差 − 高纹理误差 中位 {np.median(A[:, 2]):+.2f}mm"
              f"   为正(低纹理更差)的 {int((A[:, 2] > 0).sum())}/{len(ok)}")
        print(f"  动态画面误差 − 静态画面误差 中位 {np.median(A[:, 3]):+.2f}mm"
              f"   为正(动态更差)的 {int((A[:, 3] > 0).sum())}/{len(ok)}")

    (OUT / "btrace.json").write_text(json.dumps(per_take, ensure_ascii=False, indent=1,
                                                default=float))
    print(f"\n已写 {OUT / 'btrace.json'}")


if __name__ == "__main__":
    main()
