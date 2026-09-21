#!/usr/bin/env python3
"""§29 huber 判决的**证据生成器**：读 `out/<cell>/sparse/{w0.6,h030}/tail/` 落一份可审计的 json。

`out/` 被 .gitignore 忽略 ⇒ 判决里引用的每个数字都必须在被跟踪的路径上有出处。
本脚本产 `huber_tail_gate.json`，含两部分：

1. **指标对账**：两侧 `precision.json` 的逐键差（按 [[report-fusion-not-vins-accuracy]]
   先核对 `estimate` 指向 `trajectory_fused.csv`，确保比的是融合链不是 VINS 单链）。
2. **场口径传递率**：§23.3 的教训 —— `ΔMAX` 是非线性泛函，不能与逐帧位移相除。
   所以把两条融合轨迹放在**同一 gauge**（基线自己的 Sim(3)）里逐帧相减，得到
   `|Δfused|`，再与前端侧的 `|Δ|@峰` 比，才叫传递率。

⚠ 口径：这里用 Sim(3) 对齐，而评测器用 `alignment: SE3_estimate`（刚体）⇒ 两者 MAX
数值不同（13.440 vs 12.581mm）。**只在同脚本内部可比，不要跨口径引用。**

用法: huber_tail_gate.py [batch group arm]   （默认 §29 那一格）
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

HERE = Path(__file__).parent
ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
REF, VAR = "w0.6", "h030"


def main():
    batch, group, arm = (sys.argv[1:4] + ["20260914_validation_v10_batch",
                                          "group1", "sparse"])[:3]
    tg, Pg, Qg = E.load_trajectory(ROOT / batch / group / "lighthouse_body_ground_truth.csv")

    def rd(tag):
        f = HERE / "out" / batch / group / arm / tag / "trajectory_frames.csv"
        t, P, Q = E.load_trajectory(f)
        ins, val, plt_, _ = E.interpolate_ground_truth(t, tg, Pg, Qg, 0.1)
        P, Q, t = P[ins][val], Q[ins][val], t[ins][val]
        return t, P, Q, plt_[:, 1:4]

    def rd_fused(tag):
        f = HERE / "out" / batch / group / arm / tag / "tail" / "trajectory_fused.csv"
        t, P, Q = E.load_trajectory(f)
        ins, val, plt_, _ = E.interpolate_ground_truth(t, tg, Pg, Qg, 0.1)
        return t[ins][val], P[ins][val], plt_[:, 1:4]

    out = {"cell": [batch, group, arm], "ref": REF, "var": VAR}

    # ---- 1. 指标对账 ----
    metrics = {}
    for tag in (REF, VAR):
        p = HERE / "out" / batch / group / arm / tag / "tail" / "precision.json"
        d = json.loads(p.read_text())
        # ★ 必须确认比的是融合链，不是 VINS 单链。
        assert Path(d["estimate"]).name == "trajectory_fused.csv", d["estimate"]
        metrics[tag] = d
    keys = [k for k, v in metrics[REF].items() if isinstance(v, (int, float))]
    out["metrics"] = {k: {"ref": metrics[REF][k], "var": metrics[VAR].get(k),
                          "delta": (metrics[VAR].get(k) - metrics[REF][k])
                          if isinstance(metrics[VAR].get(k), (int, float)) else None}
                      for k in keys}
    out["result"] = {t: metrics[t]["result"] for t in (REF, VAR)}
    out["failures"] = {t: metrics[t]["failures"] for t in (REF, VAR)}

    # ---- 2. 场口径传递率 ----
    # ⚠ 两条链的行数不同（融合 1743 vs 前端 ~1799）⇒ **帧索引不可直接配对**。
    #   必须按**时间戳**重采样到公共网格再逐点相减，否则会拿「融合在帧469」除以
    #   「前端在帧448」（正是本脚本第一版的错）。
    def rd_fused(tag):
        f = HERE / "out" / batch / group / arm / tag / "tail" / "trajectory_fused.csv"
        t, P, Q = E.load_trajectory(f)
        ins, val, plt_, _ = E.interpolate_ground_truth(t, tg, Pg, Qg, 0.1)
        return t[ins][val], P[ins][val], plt_[:, 1:4]

    tb, Pb, gtb = rd_fused(REF)
    tv, Pv, _ = rd_fused(VAR)
    # 各自用自己的基线做 Sim(3)，把两条放到同一个米制 GT 系里（gauge 一致）。
    s, R, tt = E.similarity_align(Pb, gtb)
    Ab, Av = s * (Pb @ R.T) + tt, s * (Pv @ R.T) + tt
    eb, ev = np.linalg.norm(Ab - gtb, axis=1) * 1000, np.linalg.norm(Av - gtb, axis=1) * 1000
    k = int(np.argmax(eb))

    # 前端（同样是米制：用前端自己的 Sim3 对齐真值）
    ft, fP, _fQ, fgt = rd(REF)
    gt_, gP, _gQ, _ = rd(VAR)
    fs, fR, ftt = E.similarity_align(fP, fgt)
    Fb, Fv = fs * (fP @ fR.T) + ftt, fs * (gP @ fR.T) + ftt

    # 公共时间网格 = 两条链时间范围的交
    lo, hi = max(tb.min(), ft.min()), min(tb.max(), ft.max())
    grid = np.arange(lo, hi, 0.01)
    assert grid.size > 100, "两条链时间范围几乎不重叠"

    def resample(t, A):
        return np.stack([np.interp(grid, t, A[:, i]) for i in range(3)], axis=1)

    Db = resample(tb, Ab) - resample(ft, Fb)  # 基线：融合 − 前端（应≈该级的传递误差）
    Df = (resample(ft, Fv) - resample(ft, Fb)) * 1000.0  # 前端侧位移场（mm）
    Do = (resample(tb, Av) - resample(tb, Ab)) * 1000.0  # 融合侧位移场（mm）
    fmag, omag = np.linalg.norm(Df, axis=1), np.linalg.norm(Do, axis=1)
    gi = int(np.argmax(np.linalg.norm(resample(tb, Ab) - resample(tb, gtb), axis=1)))
    u = (resample(tb, Ab) - resample(tb, gtb))
    u = u[gi] / max(np.linalg.norm(u[gi]), 1e-9)
    proj = (Do @ (-u))
    # ★ 尺子说的「鼓包」是大位移区 ⇒ 单看全场中位比会被安静段主导。
    #   额外报「融合侧位移最大的那个点」上的比值，是离尺子最近的口径。
    oi = int(np.argmax(omag))
    bulge = omag > 0.1  # 只取位移 >0.1mm 的点（约等于鼓包所在量级）

    out["field"] = {
        "note": "两条链按**时间戳**重采样到公共 0.01s 网格后逐点相减；帧索引不可直接配对",
        "grid_samples": int(grid.size),
        "sim3_scale_ref_fused": float(s),
        "sim3_scale_ref_frontend": float(fs),
        "fused_max_mm": {REF: float(eb.max()), VAR: float(ev.max()),
                         "delta": float(ev.max() - eb.max())},
        "fused_peak_frame": k,
        "fused_field_disp_mm": {"median": float(np.median(omag)),
                                "max": float(omag.max()), "argmax": int(np.argmax(omag))},
        "frontend_field_disp_mm": {"median": float(np.median(fmag)),
                                   "max": float(fmag.max()), "argmax": int(np.argmax(fmag))},
        "proj_at_fused_peak_mm": float(proj[gi]),
        "fused_err_mean_mm": {"ref": float(eb.mean()), "var": float(ev.mean()),
                              "delta": float(ev.mean() - eb.mean())},
        "transfer_pointwise_median": float(np.median(omag / np.maximum(fmag, 1e-12))),
        "transfer_at_fused_disp_max": float(omag[oi] / max(fmag[oi], 1e-12)),
        "transfer_median_ratio_on_bulge": float(
            np.median(omag[bulge]) / max(np.median(fmag[bulge]), 1e-12)) if bulge.any() else None,
        "bulge_points": int(bulge.sum()),
        "ratio_max_over_max": float(omag.max() / max(fmag.max(), 1e-12)),
    }
    p = HERE / "huber_tail_gate.json"
    p.write_text(json.dumps(out, indent=1, ensure_ascii=False))

    f = out["field"]
    print(f"cell={batch}/{group}/{arm}   {REF} vs {VAR}")
    print(f"  result: {out['result']}   failures: {out['failures']}")
    for kk in ("ate_translation_max_m", "ate_translation_p95_m", "ate_translation_rmse_m",
               "ate_translation_within_10mm_ratio", "ate_translation_within_5mm_ratio",
               "ate_rotation_rmse_deg"):
        m = out["metrics"].get(kk)
        if m:
            print(f"  {kk:38s} {m['ref']:.6g} -> {m['var']:.6g}  Δ{m['delta']:+.4g}")
    print(f"  公共网格 {f['grid_samples']} 点（0.01s）")
    print(f"  融合场位移: 中位 {f['fused_field_disp_mm']['median']:.4f}mm  "
          f"最大 {f['fused_field_disp_mm']['max']:.4f}mm")
    print(f"  前端场位移: 中位 {f['frontend_field_disp_mm']['median']:.4f}mm  "
          f"最大 {f['frontend_field_disp_mm']['max']:.4f}mm")
    print(f"  proj@融合峰 {f['proj_at_fused_peak_mm']:+.4f}mm")
    print(f"  ★传递率: 逐点中位比 {f['transfer_pointwise_median']:.4f}×  "
          f"最大/最大 {f['ratio_max_over_max']:.4f}×  "
          f"鼓包段({f['bulge_points']}点) {f['transfer_median_ratio_on_bulge']:.4f}×")
    print(f"  落盘 {p.name}")


if __name__ == "__main__":
    main()