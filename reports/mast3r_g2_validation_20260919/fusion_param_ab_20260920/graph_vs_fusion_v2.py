#!/usr/bin/env python3
"""★ 修正 `graph_vs_fusion.py` 的对比口径 + 回答「[8/9] 以前不是这样的啊」

## 上一版的两个缺陷

1. **比错了对象**：拿 09-14 的 `fusion/tight/trajectory_fused.csv` 去比
   **09-20** 的 `fusion_current/tight/mastr/trajectory_graph.csv`。
   而其中 2 个 cell 的 graph 恰好因 `correction_cap_mode` 变过（见
   `cap_mode_hash_check.py`）⇒ 差值里混进了「图变了」，不是纯融合差。
   **应当比同一臂自己的 `fusion/<arm>/mast3r/trajectory_graph.csv`。**
2. **只看了 tight 一条臂**，而 09-14 的 tight/sparse **参数完全不同**：

   | 批次 | sparse | tight |
   |---|---|---|
   | `20260914_validation_v10_batch`（09-14 早） | lw 0 | **lw 0.35 + adaptive, smooth 15s** |
   | `20260914_validation_v10_holdout_batch2` | lw 0 | **lw 0.35 + adaptive, smooth 15s** |
   | `20260914_validation_v11_holdout_batch3`（09-14 晚） | lw 0 | **lw 0** ← 翻转点 |
   | `20260915_*` 起 | lw 0 | lw 0 |

⇒ **用户说「以前没遇到过 [8/9] 空转」是对的**：09-14 上半天的 tight 臂
lw=0.35，融合**真的在干活**。lw 变 0 是 09-14 当天晚些时候（v11_holdout_batch3）。
"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402
import fuse_docker2_mast3r_complementary as F  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
CONFIG = Path("/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/"
              "formal_runtime_calibration/vins_config.yaml")


def run(cells=None):
    T = F.load_body_t_camera(CONFIG)
    print("每一臂：fused  vs  **同一臂自己的** graph（过 camera_to_body）\n")
    print(f"{'cell':<46}{'arm':>7}{'lw':>7}{'smooth':>7}"
          f"{'RMS差':>9}{'max差':>8}{'ratio':>8}")
    print("-" * 96)
    rows = []
    for c in sorted({f.parents[2] for f in ROOT.glob("**/fusion/*/fusion_report.json")}):
        if cells and str(c).replace(str(ROOT) + "/", "") not in cells:
            continue
        gt = c / "lighthouse_body_ground_truth.csv"
        if not gt.exists():
            continue
        tg, Pg, Qg = E.load_trajectory(gt)
        for arm in ("tight", "sparse"):
            fr = c / f"fusion/{arm}/fusion_report.json"
            fu = c / f"fusion/{arm}/trajectory_fused.csv"
            gr = c / f"fusion/{arm}/mast3r/trajectory_graph.csv"
            if not (fr.exists() and fu.exists() and gr.exists()):
                continue
            rep = json.loads(fr.read_text()).get("fusion", {})
            lw = rep.get("local_weight")

            t, P, Q = E.load_trajectory(gr)
            ins = (t >= tg[0]) & (t <= tg[-1])
            tt, PP, QQ = t[ins], P[ins], Q[ins]

            # graph → body，用与工作流一致的 VINS 姿态先验
            vins = c / "docker2_slam/vio_corrected_stream.csv"
            if vins.exists():
                tv, Pv, Qv = E.load_trajectory(vins)
                prior = E.Slerp(tv, Rotation.from_quat(Qv))(tt)
            else:
                prior = Rotation.from_quat(QQ)
            bp, _, _ = F.camera_to_body_with_body_orientation_prior(
                PP, Rotation.from_quat(QQ), T, prior)

            t0, P0, Q0 = E.load_trajectory(fu)
            ins0 = (t0 >= tt[0]) & (t0 <= tt[-1])
            ref = np.column_stack(
                [np.interp(t0[ins0], tt, bp[:, k]) for k in range(3)])
            d = np.linalg.norm(P0[ins0] - ref, axis=1)
            scale = np.sqrt((P0[ins0] ** 2).sum(axis=1)).mean()
            name = str(c).replace(str(ROOT) + "/", "")
            rms = np.sqrt((d ** 2).mean()) * 1000
            print(f"{name:<46}{arm:>7}{str(lw):>7}"
                  f"{str(rep.get('smoothing_s')):>7}{rms:>9.3f}"
                  f"{d.max()*1000:>8.3f}{rms/(scale*1000):>8.5f}")
            rows.append((name, arm, lw, rms))

    print("\n" + "=" * 96)
    print("按 lw 分组")
    print("=" * 96)
    for lo, hi, tag in ((None, 1e-9, "lw == 0（融合是直通）"),
                        (1e-9, None, "lw > 0（融合真在干活）")):
        sel = [r for r in rows
               if r[2] is not None
               and (lo is None or r[2] > lo) and (hi is None or r[2] < hi)]
        if not sel:
            continue
        v = np.array([r[3] for r in sel])
        print(f"  {tag:<26} n={len(sel):<3} "
              f"RMS差 中位 {np.median(v):8.3f} mm  最大 {v.max():8.3f} mm")
    print("\n判据：lw=0 的臂，fused 应 ≈ graph(→body)；")
    print("      lw>0 的臂，fused 应明显偏离 graph —— 那才是「融合在工作」。")


if __name__ == "__main__":
    run()