#!/usr/bin/env python3
"""每条 cell 的【两道前置门】状态 —— 决定现役会不会根本不出产物。

1. VINS 验收门 (docker2_slam/run_acceptance.json)  —— 生产 [7/8] 之前就会拦
2. [9/9] 融合输入质量门 (input_quality_report.json) —— 现役在 [8/9] 之后评估
"""
import json
from pathlib import Path
WF = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")


def js(p):
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def cells():
    for b in sorted(WF.glob("*")):
        if not b.is_dir():
            continue
        for g in sorted(b.glob("group*")):
            if not (g / "lighthouse_body_ground_truth.csv").is_file():
                continue
            for s in ("sparse", "tight"):
                if (g / "fusion" / s / "trajectory_fused.csv").is_file():
                    yield f"{b.name}/{g.name}/{s}", g, s


print(f"{'cell':<48}{'VINS验收':>9}{'[9/9]质量门(旧)':>16}{'[9/9]质量门(现役)':>18}")
print("-" * 96)
rej = []
for key, g, s in cells():
    ra = js(g / "docker2_slam" / "run_acceptance.json") or {}
    q_old = js(g / "fusion" / s / "input_quality_report.json") or {}
    q_new = js(g / "fusion_current" / s / "input_quality_report.json") or {}
    v = ra.get("result", "缺")
    o = q_old.get("result", "—")
    n = q_new.get("result", "—")
    print(f"{key:<48}{str(v):>9}{str(o):>16}{str(n):>18}")
    if v != "PASS":
        rej.append((key, "VINS验收 " + str(ra.get("failures"))))
    if n == "REJECT":
        rej.append((key, "[9/9]质量门 REJECT " + str(q_new.get("reason") or q_new.get("failures"))))
print()
if rej:
    print("⚠ 现役会【拒绝出产物】的 cell:")
    for k, why in rej:
        print(f"   {k:<48} {why}")
else:
    print("现役对全部 cell 都会出产物。")
