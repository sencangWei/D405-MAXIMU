#!/usr/bin/env python3
"""B 类（以及 A 类的快速段）在【出错的那些固定时刻】画面/动作长什么样。

## 为什么做这个

`gate_failure_modes.py` 已证：失败时刻**可复现**（sparse/tight 两次独立跑落在同一时刻）
⇒ 那是**录制的属性**，可以定点去看。上一轮的强光假设已被否，但那是**整条 take 的
相关分析**；现在是**定点看帧**，性质不同。

## 采样什么时刻

- **B 类**：超 10mm 的**最长连续段**上取 起点/1/4/1/2/3/4/峰值 六帧（一段几秒，看它是什么）
- **A 类**：误差**峰值**那一帧（"快速段欠走"发生在哪一帧）
- **对照**：同一 take 里误差**最小**的连续区间中点（同一台相机、同一场，只差动作）

## 每帧量什么

- RGB：均值 / P99 / 过曝% / 欠曝% / **Laplacian 方差（清晰度，运动模糊的直接指标）**
- 双 IR：均值 / Laplacian 方差
- 该时刻的**真值线速度 / 角速度**（±0.15s 窗口）—— 动作有多激烈

**全部无监督即可得**（清晰度、亮度都用不到真值），真值只用来把时刻选出来 + 给动作强度。

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
REC = Path("/home/robot/umi_ego_vio_data_device2_c48df736/recordings")
OUT = Path(__file__).parent
IMGDIR = Path("/tmp/claude-1000/frames0919")
IMGDIR.mkdir(parents=True, exist_ok=True)

TOPIC_RGB, TOPIC_IR1, TOPIC_IR2 = 69, 72, 75

# 目标 cell：(标签, batch, group, 子集, 采样模式)
CELLS = [
    ("A v10/g1/tight",        "20260914_validation_v10_batch",         "group1", "tight",  "peak"),
    ("A v11b3/g1/sparse",     "20260914_validation_v11_holdout_batch3", "group1", "sparse", "peak"),
    ("A v11b3/g1/tight",      "20260914_validation_v11_holdout_batch3", "group1", "tight",  "peak"),
    ("B v10/g2/tight",        "20260914_validation_v10_batch",         "group2", "tight",  "run"),
    ("B batch5/g3/tight",     "20260915_batch5_four_videos",           "group3", "tight",  "run"),
    ("B v10b2/g1/tight",      "20260914_validation_v10_holdout_batch2", "group1", "tight",  "run"),
    ("✓ v11b3/g2/tight(PASS)", "20260914_validation_v11_holdout_batch3", "group2", "tight",  "run"),
]


# ---------------------------------------------------------------- db3 读取
def cdr_string(b, o):
    n = struct.unpack_from("<I", b, o)[0]
    o += 4
    s = b[o:o + n - 1].decode("utf-8", "replace")
    o += n
    return s, o + (-o) % 4


def parse_image(b):
    """sensor_msgs/Image 的 CDR：尾部 h*w*bytes_per_px 就是像素负载。"""
    o = 4 + 8
    _fid, o = cdr_string(b, o)
    h, w = struct.unpack_from("<II", b, o)
    o += 8
    enc, o = cdr_string(b, o)
    n = h * w * (2 if enc.upper().startswith("YUY") else 3 if enc.lower() == "rgb8" else 1)
    return h, w, enc, b[len(b) - n:]


def to_bgr(h, w, enc, raw):
    """RGB 是 YUYV、双 IR 是 8UC1（单通道），两种都要能吃。"""
    a = np.frombuffer(raw, np.uint8)
    e = enc.upper()
    if e.startswith("YUY"):
        return cv2.cvtColor(a.reshape(h, w, 2), cv2.COLOR_YUV2BGR_YUYV)
    if e in ("8UC1", "MONO8"):
        return a.reshape(h, w)
    return a.reshape(h, w, 3)[:, :, ::-1]


def img_stats(img):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    small = cv2.resize(g, (g.shape[1] // 2, g.shape[0] // 2), interpolation=cv2.INTER_AREA)
    return dict(mean=float(g.mean()),
                p99=float(np.percentile(g, 99)),
                over=float(np.mean(g > 250) * 100),
                under=float(np.mean(g < 10) * 100),
                lap=float(cv2.Laplacian(small, cv2.CV_64F).var()))


class Session:
    """一个录制：db3 + frames.csv + epoch↔monotonic 映射。"""

    def __init__(self, batch, group):
        prov = json.loads((ROOT / batch / group /
                           "lighthouse_ground_truth_provenance.json").read_text())
        self.frames_csv = Path(prov["clock_mapping"]["d405_frames"])
        self.epoch_minus_mono = float(prov["clock_mapping"]["epoch_minus_monotonic_s"])
        self.dir = self.frames_csv.parent
        self.mono = np.array([float(r["color_mono"])
                              for r in csv.DictReader(open(self.frames_csv, newline=""))])
        con = sqlite3.connect(f"file:{self.dir / 'd405_720p_rgb_stereo_ir.db3'}"
                              f"?mode=ro&immutable=1", uri=True)
        self.ids = {t: [r[0] for r in con.execute(
            "SELECT id FROM messages WHERE topic_id=? ORDER BY id", (t,))]
            for t in (TOPIC_RGB, TOPIC_IR1, TOPIC_IR2)}
        self.con = con

    def index_of(self, t_sec):
        """轨迹 t_sec → frames.csv 行号（两表逐行对应，差 0/1 行 = 33ms，不影响看段）。"""
        return int(np.argmin(np.abs(self.mono - (t_sec - self.epoch_minus_mono))))

    def frame(self, topic, i):
        i = max(0, min(i, len(self.ids[topic]) - 1))
        raw = self.con.execute("SELECT data FROM messages WHERE id=?",
                               (self.ids[topic][i],)).fetchone()[0]
        return parse_image(bytes(raw))


# ---------------------------------------------------------------- 打分与采样
def score_grid(est, gt):
    et, ep, eq = E.load_trajectory(Path(est))
    rt, rp, rq = E.load_trajectory(Path(gt))
    ins, val, itp, iq = E.interpolate_ground_truth(et, rt, rp, rq, 0.1)
    if val.sum() < 10:
        return None
    ts = et[ins][val]
    P, Q = ep[ins][val], itp[:, 1:]
    R, t = E.rigid_align(P, Q)
    err = np.linalg.norm(P @ R.T + t - Q, axis=1) * 1000
    Gq = E.Rotation.from_quat(iq)
    return ts, P, Q, err, Gq


def runs_of(mask):
    out, i = [], 0
    while i < len(mask):
        if mask[i]:
            j = i
            while j + 1 < len(mask) and mask[j + 1]:
                j += 1
            out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def motion(ts, P, Gq, t0, half=0.15):
    """t0 附近 ±half 秒的真值线速度(mm/s)与角速度(deg/s)。"""
    m = np.abs(ts - t0) <= half
    if m.sum() < 3:
        return 0.0, 0.0
    i = np.where(m)[0]
    dt = np.diff(ts[i])
    v = np.linalg.norm(np.diff(P[i], axis=0), axis=1) / dt * 1000
    q = Gq[i]
    ang = np.degrees((q[:-1].inv() * q[1:]).magnitude()) / dt
    return float(np.median(v)), float(np.median(ang))


def pick_moments(mode, ts, err):
    """返回 [(标签, 时刻)]。"""
    over = err > 10.0
    if mode == "peak":
        k = int(np.argmax(err))
        return [("峰值", float(ts[k])), ("峰值+0.5s", float(ts[min(k + 15, len(ts) - 1)])),
                ("峰值-0.5s", float(ts[max(k - 15, 0)]))]
    rr = runs_of(over)
    out = []
    if rr:
        a, b = max(rr, key=lambda r: r[1] - r[0])          # 最长超标段
        for frac, nm in ((0.0, "段首"), (0.25, "段1/4"), (0.5, "段中"),
                         (0.75, "段3/4"), (1.0, "段尾")):
            out.append((f"超标{nm}", float(ts[a + int((b - a) * frac)])))
        out.append(("超标段峰值", float(ts[a + int(np.argmax(err[a:b + 1]))])))
    # 对照：误差最小的连续 1s 区间中点
    win = max(int(1.0 / np.median(np.diff(ts))), 3)
    csum = np.concatenate([[0.0], np.cumsum(err)])
    if len(err) > win:
        means = (csum[win:] - csum[:-win]) / win
        c = int(np.argmin(means)) + win // 2
        out.append(("对照(误差最小处)", float(ts[c])))
    return out


# ---------------------------------------------------------------- 主流程
def main():
    rows = []
    for label, batch, group, sub, mode in CELLS:
        gt = ROOT / batch / group / "lighthouse_body_ground_truth.csv"
        est = ROOT / batch / group / "fusion" / sub / "trajectory_fused.csv"
        if not (gt.is_file() and est.is_file()):
            print(f"⚠ 缺文件 {label}")
            continue
        d = score_grid(est, gt)
        if d is None:
            print(f"⚠ 分不够 {label}")
            continue
        ts, P, Q, err, Gq = d
        sess = Session(batch, group)
        print()
        print("=" * 118)
        print(f"{label}    ({batch}/{group}/{sub})   max {err.max():.2f}mm  "
              f"中位 {np.median(err):.2f}mm   录制 {sess.dir.name}")
        print("-" * 118)
        print(f"{'时刻':<20}{'误差mm':>8}{'速度mm/s':>10}{'角速度°/s':>11}"
              f"{'RGB均值':>9}{'过曝%':>7}{'RGB清晰度':>11}{'IR1均值':>9}{'IR1清晰度':>11}")
        for tag, t0 in pick_moments(mode, ts, err):
            i = sess.index_of(t0)
            k = int(np.argmin(np.abs(ts - t0)))
            v, w = motion(ts, P, Gq, t0)
            try:
                h, w_, enc, raw = sess.frame(TOPIC_RGB, i)
                rgb = to_bgr(h, w_, enc, raw)
                h1, w1, e1, r1 = sess.frame(TOPIC_IR1, i)
                ir1 = to_bgr(h1, w1, e1, r1)
            except Exception as ex:
                print(f"{tag:<20} 解码失败 {ex}")
                continue
            s_rgb, s_ir1 = img_stats(rgb), img_stats(ir1)
            for img, nm in ((rgb, "RGB"), (ir1, "IR1")):
                cv2.imwrite(str(IMGDIR / f"{label.split()[0]}_{group}_{sub}_{tag}_{nm}"
                                     f"_e{err[k]:.1f}mm_i{i:04d}.png"), img)
            print(f"{tag:<20}{err[k]:8.2f}{v:10.1f}{w:11.1f}"
                  f"{s_rgb['mean']:9.1f}{s_rgb['over']:6.2f}%{s_rgb['lap']:11.1f}"
                  f"{s_ir1['mean']:9.1f}{s_ir1['lap']:11.1f}")
            rows.append(dict(cell=label, tag=tag, t=float(t0), err=float(err[k]),
                             speed_mm_s=v, angrate_deg_s=w, frame=i,
                             **{f"rgb_{a}": b for a, b in s_rgb.items()},
                             **{f"ir1_{a}": b for a, b in s_ir1.items()}))
        sess.con.close()

    # 汇总：超标时刻 vs 对照，动作与清晰度的总体差异
    print()
    print("=" * 118)
    print("汇总：超标时刻 vs 同 take 对照")
    print("-" * 118)
    print(f"{'cell':<24}{'误差mm':>9}{'速度mm/s':>11}{'角速度°/s':>11}{'RGB清晰度':>11}{'IR1清晰度':>11}")
    for label, *_ in CELLS:
        a = [r for r in rows if r["cell"] == label and r["tag"].startswith("超标")
             or (r["cell"] == label and r["tag"] == "峰值")]
        b = [r for r in rows if r["cell"] == label and r["tag"].startswith("对照")]
        if not a or not b:
            continue
        f = lambda rs, k: np.median([r[k] for r in rs])
        print(f"{label:<24}{f(a,'err'):9.2f}{f(a,'speed_mm_s'):11.1f}"
              f"{f(a,'angrate_deg_s'):11.1f}{f(a,'rgb_lap'):11.1f}{f(a,'ir1_lap'):11.1f}")
        print(f"{'   └ 对照':<24}{f(b,'err'):9.2f}{f(b,'speed_mm_s'):11.1f}"
              f"{f(b,'angrate_deg_s'):11.1f}{f(b,'rgb_lap'):11.1f}{f(b,'ir1_lap'):11.1f}")

    (OUT / "bframes.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1,
                                                 default=float))
    print(f"\n图已写 {IMGDIR}\n表已写 {OUT / 'bframes.json'}")


if __name__ == "__main__":
    main()
