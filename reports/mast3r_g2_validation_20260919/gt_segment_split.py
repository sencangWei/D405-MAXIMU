#!/usr/bin/env python3
"""量化"真值被分支切换切段"对 ATE 的贡献。

做法: 同一个估计轨迹、同一份真值, 算两遍 ATE ——
  (a) 整条一次 SE(3) 对齐(官方口径, 即 precision.md 用的那个);
  (b) 在分支切换时刻把真值切段, **每段各自 SE(3) 对齐**。
(b) 是诊断量, 不是官方指标: 它把"真值自己断成两截"这件事从 ATE 里剥掉,
剩下的才是 SLAM 的实际误差。两者之差 = 真值切段造的假。

用法: gt_segment_split.py <group_dir> <est_csv> [<est_csv> ...]
"""
import csv
import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

LIM = dict(rmse=10.0, p95=10.0, mx=10.0, w10=95.0, rot=2.0)


def metrics(d_err_mm, ang_deg):
    return dict(rmse=float(np.sqrt(np.mean(d_err_mm ** 2))),
                p95=float(np.percentile(d_err_mm, 95)),
                mx=float(d_err_mm.max()),
                w10=float(np.mean(d_err_mm <= 10.0) * 100),
                rot=float(np.sqrt(np.mean(ang_deg ** 2))))


def pose_err(P, Q, Pq, R, t, iq):
    d = np.linalg.norm(P @ R.T + t - Q, axis=1) * 1000
    ang = np.degrees((Rotation.from_quat(iq).inv()
                      * (Rotation.from_matrix(R) * Rotation.from_quat(Pq))).magnitude())
    return d, ang


def main():
    gdir, ests = Path(sys.argv[1]), sys.argv[3:]
    prov = json.loads((gdir / "lighthouse_ground_truth_provenance.json").read_text())
    sess = Path(prov["inputs"]["tracker"]).parent
    gate_path = Path(sys.argv[2]) if sys.argv[2].endswith(".json") else None
    if gate_path is None:
        print(f"用法: gt_segment_split.py <group_dir> <gate.json> <est_csv> ...\n"
              f"先跑: lighthouse_tracker_branch_gate.py {sess} --json /tmp/br.json")
        return
    gate = json.loads(gate_path.read_text())
    if gate.get("switches"):
        pass
    else:
        print(f"{sess.name}: 0 次切换, 无需分段")
        return
    # 时间域换算: 门的切换时刻是 tracker 的 host_monotonic_s, 真值/估计是 unix epoch。
    # 偏移量取 provenance 的 clock_mapping.epoch_minus_monotonic_s(同一条 take 的
    # 相机-主机时钟映射), 不要写死。
    off = prov["clock_mapping"]["epoch_minus_monotonic_s"]
    cuts = [s["host_monotonic_s"] + off for s in gate["switches"]]
    print(f"组 {gdir.name} / 会话 {sess.name}: {len(cuts)} 次分支切换 "
          f"@ {[f'{c:.1f}' for c in cuts]}, 最坏影响 {gate['max_affected_frame_ratio']*100:.1f}% 相机帧\n")

    for est in ests:
        et, ep, eq = E.load_trajectory(Path(est))
        rt, rp, rq = E.load_trajectory(gdir / "lighthouse_body_ground_truth.csv")
        inside, valid, interp, iq = E.interpolate_ground_truth(et, rt, rp, rq, 0.1)
        if valid.sum() < 10:
            print(f"{est}: 有效样本不足")
            continue
        P, Q, Pq = ep[inside][valid], interp[:, 1:], eq[inside][valid]
        T = et[inside][valid]

        # (a) 整条一次对齐
        R, t = E.rigid_align(P, Q)
        d, a = pose_err(P, Q, Pq, R, t, iq)
        whole = metrics(d, a)

        # (b) 按切换时刻切段, 每段各自对齐
        seg_id = np.searchsorted(np.asarray(cuts), T)
        d2, a2 = np.zeros_like(d), np.zeros_like(a)
        segs = []
        for s in np.unique(seg_id):
            m = seg_id == s
            if m.sum() < 10:
                continue
            Rs, ts = E.rigid_align(P[m], Q[m])
            ds, as_ = pose_err(P[m], Q[m], Pq[m], Rs, ts, iq[m])
            d2[m], a2[m] = ds, as_
            segs.append((int(s), int(m.sum()), float(np.sqrt(np.mean(ds ** 2))),
                         float(ds.max())))
        # 未覆盖到的点(短段)退回整条解, 免得把 NaN 混进统计
        miss = (d2 == 0) & (d > 0)
        d2[miss], a2[miss] = d[miss], a[miss]
        split = metrics(d2, a2)

        def verdict(m):
            f = [k for k in ("rmse", "p95", "mx") if m[k] > LIM[k]]
            if m["w10"] < LIM["w10"]:
                f.append("w10")
            if m["rot"] > LIM["rot"]:
                f.append("rot")
            return "PASS" if not f else "FAIL(" + ",".join(f) + ")"

        print(f"--- {Path(est).parent.parent.parent.name}/{Path(est).parent.parent.name} ---")
        for lbl, m in (("整条一次对齐 (官方口径)", whole), ("按分支分段各自对齐 (诊断)", split)):
            print(f"  {lbl:<28} rmse {m['rmse']:>6.2f}  p95 {m['p95']:>6.2f}  "
                  f"max {m['mx']:>6.2f}  w10 {m['w10']:>5.1f}%  rot {m['rot']:>4.2f}   {verdict(m)}")
        print(f"  => 真值切段造的假: rmse {whole['rmse']-split['rmse']:+.2f}  "
              f"max {whole['mx']-split['mx']:+.2f}  rot {whole['rot']-split['rot']:+.2f}")
        for s, n, r, mx in segs:
            print(f"     段{s}: {n:>5} 点  rmse {r:>6.2f}  max {mx:>6.2f}")
        print()


if __name__ == "__main__":
    main()
