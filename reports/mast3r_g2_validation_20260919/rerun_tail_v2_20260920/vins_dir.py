#!/usr/bin/env python3
"""选一条 cell 该用哪份 VINS 轨迹 —— 复原 09-14 的选择规则。

## 为什么需要它

`20260914_validation_v10_holdout_batch2/group1` 与 `group2` 有**两份** VINS 产物：

| 目录 | group1 | group2 |
|---|---|---|
| `docker2_slam/` | `FAIL ['corrected_trajectory_jump']` | **空目录**（那次跑什么都没产出） |
| `docker2_slam_rate0p5/` | **PASS** | **PASS** |

09-14 的 `graph_fusion_report.json` 里 `inputs.relative_motion_trajectory` 记的是
**`docker2_slam_rate0p5/vio_corrected_stream.csv`**，`source_validation.source_result = PASS`
—— 也就是说 09-14 对这两条正是回退到 0.5× 速率那份跑的。

而 `rerun_tail_current_20260920/rerun_one.sh` / `rerun_tail_v2_20260920/*` 把路径写死成
`$G/docker2_slam/...` ⇒ [7/8] `validate_relative_motion_report` 抛
`input report did not pass`、[8/9] `docker2_position_policy` 抛
`Docker2 run is not safe for complementary fusion`。
**那不是产品路径被阻断，是测试台架选错了目录。**（其它 9 条 cell 只有一份，且 PASS ⇒ 规则不动它们。）

## 规则

优先 `docker2_slam/`（且其验收 PASS 且无 failures）；否则找 `docker2_slam_*` 里
第一份验收 PASS 的；都没有则返回 None（该 cell 确实跑不了）。
"""
import json
from pathlib import Path

# 与 fuse_mast3r_stereo_imu.py / fuse_docker2_mast3r_complementary.py 同一判据：
# result == PASS 且 runtime_watchdog.failures 为空。
def acceptance_ok(report: Path) -> bool:
    try:
        d = json.loads(report.read_text(encoding="utf-8"))
    except Exception:                                              # noqa: BLE001
        return False
    return d.get("result") == "PASS" and not d.get("runtime_watchdog", {}).get("failures")


def pick_vins_dir(cell: Path):
    """→ (traj_csv, acceptance_json) 或 None。"""
    cands = [cell / "docker2_slam"] + sorted(
        p for p in cell.glob("docker2_slam_*") if p.is_dir())
    for d in cands:
        traj, rep = d / "vio_corrected_stream.csv", d / "run_acceptance.json"
        if traj.exists() and rep.exists() and acceptance_ok(rep):
            return traj, rep
    return None


if __name__ == "__main__":
    import sys
    root = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
    for c in sorted(p for p in root.glob("*/*") if p.is_dir()):
        r = pick_vins_dir(c)
        print(f"{str(c).replace(str(root)+'/', ''):<48} "
              f"{r[0].parent.name if r else 'NONE'}")
    sys.exit(0)