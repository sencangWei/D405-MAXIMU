#!/usr/bin/env python3
"""§34 普查：GT 真值窗是否覆盖完整相机窗。

背景（§34.1）：官方评测器 `evaluate_slam_ground_truth.py` 有一条
`--min-timestamp-overlap-ratio 0.98`（默认值，`lighthouse_umi_precision_workflow.sh:221` 显式传），
overlap = `len(选中的 GT 位置样本) / len(估计时间戳)`。只要估计轨迹铺满相机窗，
而 GT 没铺满，overlap 必然 < 0.98 ⇒ **无论精度多好都 FAIL**。

根因：GT 由 `apply_lighthouse_aprilgrid_calibration.py --query-times <估计轨迹>` 生成
（`lighthouse_umi_precision_workflow.sh:111-127`），query 时刻**按估计的时间戳采样**；
tracker 会话的单调时窗若短于相机窗，末端那段 query 就取不到 tracker 位姿。

判据：`tracker.csv` 的 `host_monotonic_ns` 时窗末端 − `d405_frames.csv` 的
`sensor_event_mono` 时窗末端。负值 ⇒ tracker 比相机先结束 ⇒ 缺 GT 位姿。

产物：`gt_window_census.json`（本脚本输出，供 README §34 引用）。
用法: python3 gt_window_census.py [--root /home/robot/ego_vio_humble/reports/lighthouse_umi_workflow]
"""
import argparse
import csv
import json
from pathlib import Path

CAM_COL = "sensor_event_mono"      # d405_frames.csv
TRK_COL = "host_monotonic_ns"      # tracker.csv（ns ⇒ /1e9）


def window_ends(csv_path: Path, col: str, scale: float):
    lo = hi = None
    with open(csv_path) as fh:
        for row in csv.DictReader(fh):
            raw = row.get(col)
            if raw in (None, ""):
                continue
            try:
                v = float(raw) * scale
            except ValueError:
                continue
            lo = v if lo is None else min(lo, v)
            hi = v if hi is None else max(hi, v)
    return lo, hi


def census(root: Path):
    out = []
    for prov in sorted(root.glob("*/*/lighthouse_ground_truth_provenance.json")):
        group_dir = prov.parent
        rec = {"group": str(group_dir.relative_to(root))}
        try:
            p = json.load(open(prov))
        except Exception as exc:                                  # noqa: BLE001
            rec["error"] = f"provenance unreadable: {exc}"
            out.append(rec)
            continue
        rec["gt_overlap_from_provenance"] = p.get("timestamp_overlap_ratio")
        rec["gt_samples_written"] = p.get("samples_written")
        rec["gt_query_samples"] = p.get("query_samples")

        # 两个 csv 的路径都记在 provenance.inputs 里（不在组目录下）：
        #   inputs.d405_frames = <session>/d405_frames.csv
        #   inputs.tracker     = reports/lighthouse_umi_sessions/<session>/tracker.csv
        inputs = p.get("inputs", {})
        frames_csv = Path(inputs.get("d405_frames", ""))
        tracker_csv = Path(inputs.get("tracker", ""))
        if not frames_csv.exists() or not tracker_csv.exists():
            rec["error"] = f"csv missing: frames={frames_csv.exists()} tracker={tracker_csv.exists()}"
            out.append(rec)
            continue

        cam_lo, cam_hi = window_ends(frames_csv, CAM_COL, 1.0)
        trk_lo, trk_hi = window_ends(tracker_csv, TRK_COL, 1e-9)   # ns → s
        rec.update({
            "cam_window": [cam_lo, cam_hi],
            "tracker_window": [trk_lo, trk_hi],
            "tracker_end_minus_cam_end_s": (trk_hi - cam_hi) if None not in (trk_hi, cam_hi) else None,
            "missing_gt_samples": (rec.get("gt_query_samples") or 0) - (rec.get("gt_samples_written") or 0),
        })
        out.append(rec)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
    ap.add_argument("--output", default=None)
    args = ap.parse_args()
    root = Path(args.root)
    rows = census(root)
    dest = Path(args.output) if args.output else Path(__file__).parent / "gt_window_census.json"
    dest.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")

    print(f"{'group':<44}{'末-末(s)':>10}{'overlap':>10}  结果")
    for r in rows:
        d = r.get("tracker_end_minus_cam_end_s")
        ov = r.get("gt_overlap_from_provenance")
        ds = f"{d:+.2f}" if d is not None else "  ?"
        os_ = f"{ov:.4f}" if ov is not None else "  ?"
        verdict = ""
        if ov is not None:
            verdict = "过不了 overlap 门" if ov < 0.98 else "ok"
            if d is not None and d < 0:
                verdict += "  ← tracker 短"
        print(f"{r['group']:<44}{ds:>10}{os_:>10}  {verdict}")
    print(f"\n{len(rows)} 组 → {dest}")


if __name__ == "__main__":
    main()