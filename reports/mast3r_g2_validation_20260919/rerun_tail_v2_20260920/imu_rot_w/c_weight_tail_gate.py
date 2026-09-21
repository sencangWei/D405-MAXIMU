#!/usr/bin/env python3
"""§24.4 尾链判决：C-as-weight 在**融合后**动没动门。

Stage A（`c_weight_stage_a.py`）量的是前端 `[1/8]`：Cw000 在 v10/g1 sparse 上把
鼓包推了 6.61mm，其中 1.91mm 沿误差方向。但门卡的是**融合后**的
`ate_translation_max`，所以必须看 Stage B。

§21/§23 给的先验：`[6/8]→[7/8]` 传递率 **0.33×**，`[7/8]→[8/9]` **1.04×**
⇒ 预期融合后只剩 ~0.6mm，**填不上 2.58mm 的缺口**。
⚠ 但 §23.5 边界① 明说那个 0.33× 是在 **keyframe 布点类**扰动上量的，
**加权类**可能不同 —— 本脚本正是去测这件事。

⇒ 两种可能的读数，都算有价值的判决：
  * 融合后 ≈0.6mm（≈0.33×）⇒ §23 的收缩律对加权类也成立，此杠杆太小
  * 融合后 ≫0.6mm ⇒ 收缩律**依扰动类型而异**，是结构性发现

官方门（evaluate_slam_ground_truth）：RMSE/p95/max ≤10mm、within_10mm ≥0.95、rot ≤2.0°

用法: c_weight_tail_gate.py <batch> <group> <arm> <tag...>
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
GATE = {"ate_translation_rmse_m": 0.010, "ate_translation_p95_m": 0.010,
        "ate_translation_max_m": 0.010}
MIN_RATIO = ("ate_translation_within_10mm_ratio", 0.95)
ROT = ("ate_rotation_rmse_deg", 2.0)


def main():
    if len(sys.argv) < 5:
        print(__doc__)
        return 1
    batch, group, arm, tags = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]

    rows = []
    print(f"尾链门判决  cell={batch}/{group}/{arm}\n")
    print(f"{'tag':>8}{'RMSE':>9}{'p95':>9}{'MAX':>10}{'w10':>8}{'rot°':>8}  门")
    print("-" * 62)
    for t in tags:
        f = HERE / "out" / batch / group / arm / t / "tail" / "precision.json"
        if not f.exists():
            print(f"{t:>8}   (无 precision.json)")
            continue
        d = json.loads(f.read_text())
        # ⚠ 口径：先确认这是融合链不是单链（[[report-fusion-not-vins-accuracy]]）
        est = d.get("estimate") or d.get("alignment", "")
        fails = [k for k, lim in GATE.items() if d.get(k, 0) > lim]
        if d.get(MIN_RATIO[0], 1) < MIN_RATIO[1]:
            fails.append(MIN_RATIO[0])
        if d.get(ROT[0], 0) > ROT[1]:
            fails.append(ROT[0])
        rows.append(dict(tag=t, **{k: d.get(k) for k in
                                   ("ate_translation_rmse_m", "ate_translation_p95_m",
                                    "ate_translation_max_m",
                                    "ate_translation_within_10mm_ratio",
                                    "ate_rotation_rmse_deg")}, fails=fails))
        print(f"{t:>8}{d['ate_translation_rmse_m']*1000:>9.2f}"
              f"{d['ate_translation_p95_m']*1000:>9.2f}"
              f"{d['ate_translation_max_m']*1000:>10.2f}"
              f"{d['ate_translation_within_10mm_ratio']:>8.4f}"
              f"{d['ate_rotation_rmse_deg']:>8.3f}  "
              f"{'PASS' if not fails else 'FAIL:' + ','.join(fails)}")
        if est:
            # 只报文件名 —— 确认这确实是**融合链**而不是单链
            # （[[report-fusion-not-vins-accuracy]]：新目录的单链报告也叫 precision.json）
            print(f"{'':>8}  估计文件 = {Path(str(est)).name if '/' in str(est) else est}")

    if len(rows) >= 2:
        base = rows[0]
        print(f"\n■ 相对基线 {base['tag']} 的融合后位移（mm）")
        for r in rows[1:]:
            dm = (r["ate_translation_max_m"] - base["ate_translation_max_m"]) * 1000
            dr = (r["ate_translation_rmse_m"] - base["ate_translation_rmse_m"]) * 1000
            pad = 10.0 - r["ate_translation_max_m"] * 1000
            print(f"  {r['tag']:>8}  ΔMAX {dm:+7.2f}   ΔRMSE {dr:+6.3f}   "
                  f"MAX={r['ate_translation_max_m']*1000:6.2f} "
                  f"{'✅过门' if pad >= 0 else f'距门还差 {abs(pad):.2f}mm'}")
    (HERE / "c_weight_tail_gate.json").write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())