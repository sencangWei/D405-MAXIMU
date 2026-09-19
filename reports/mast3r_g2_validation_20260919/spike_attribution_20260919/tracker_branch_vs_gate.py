#!/usr/bin/env python3
"""把 tracker 位姿分支门铺到全部 cell, 与尾段扫描的精度对表。

## 为什么要对这张表

尾段配方扫描 (`recover0914_sweep.py`) 得到的最好成绩是 18 个可比 cell 里
**G2 达标 1 个**, 失败几乎全卡在 `ate_translation_max` 9.8–11mm 这个窄带上。
而 "max 卡在 ~10mm" **恰好是 tracker 换分支的签名**:
真值被切成几段后, 单一 SE(3) 对齐必须在段间折中, 整条 ATE 被抬到 ~10mm,
**与 SLAM 实际精度无关**。

⇒ 如果这批失败里有一大块是 REJECT, 那 "算法没跑通" 这个判断就要改写。

## 做法

每个 cell 的 `lighthouse_ground_truth_provenance.json` 里都记着它用的
`inputs.tracker` 与 `clock_mapping.d405_frames`。同一个 take 的 sparse/tight
共用一份 tracker ⇒ **按 (tracker, d405_session) 去重后再跑门**。

只读, 不改任何东西。SLAM 不参与判定 (slam_supervision: false)。
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
GATE = Path("/home/robot/ego_vio_humble/scripts/lighthouse_tracker_branch_gate.py")
SWEEP = Path("/tmp/claude-1000/stereoab/recover0914/recover0914_sweep.json")
OUT = Path(__file__).parent


def cells():
    """产出 (cell_id, group_dir) —— 只要有 GT provenance 就算一个 cell。"""
    for b in sorted(ROOT.glob("2026*")):
        for g in sorted(b.glob("group*")):
            p = g / "lighthouse_ground_truth_provenance.json"
            if p.is_file():
                yield f"{b.name}/{g.name}", g, p


def run_gate(tracker: Path, d405: Path | None, cache: dict):
    key = (str(tracker), str(d405))
    if key in cache:
        return cache[key]
    js = OUT / "_gate_tmp.json"
    cmd = [sys.executable, str(GATE), str(tracker.parent)]
    if d405:
        cmd += ["--d405-session", str(d405)]
    cmd += ["--json", str(js)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    res = {"exit": r.returncode}
    if js.is_file():
        j = json.loads(js.read_text())
        res["worst"] = j.get("max_affected_frame_ratio")
        res["switches"] = len(j.get("switches") or [])
        res["result"] = j.get("result")
        res["camera_frames"] = j.get("camera_frames")
        js.unlink()
    else:
        res["stderr"] = (r.stderr or r.stdout)[-200:]
    cache[key] = res
    return res


def main():
    sweep = {}
    if SWEEP.is_file():
        for row in json.loads(SWEEP.read_text()):
            m = row.get("configs", {}).get("A_G2_current", {}).get("m")
            if m:
                sweep[row["group"]] = m

    rows, cache = [], {}
    for cid, g, prov in cells():
        j = json.loads(prov.read_text())
        tk = Path(j["inputs"]["tracker"])
        d405f = Path(j["clock_mapping"]["d405_frames"])
        d405 = d405f.parent if d405f.is_file() or d405f.parent.is_dir() else None
        if not tk.is_file():
            print(f"{cid:<46} 缺 tracker {tk}")
            continue
        res = run_gate(tk, d405, cache)
        rows.append(dict(cell=cid, exit=res["exit"], worst=res.get("worst"),
                         switches=res.get("switches"),
                         camera_frames=res.get("camera_frames"),
                         tracker=str(tk), d405=str(d405) if d405 else None))

    print()
    print("=" * 96)
    print(f"{'cell':<46}{'分支门':<10}{'受影响帧比':>11}{'换分支次数':>10}{'相机帧':>9}")
    print("-" * 96)
    for r in rows:
        w = r["worst"]
        ws = f"{w * 100:10.3f}%" if isinstance(w, (int, float)) else "         —"
        sw = r["switches"] if r["switches"] is not None else "—"
        cf = r["camera_frames"] if r["camera_frames"] is not None else "—"
        verdict = "REJECT" if r["exit"] == 3 else ("PASS" if r["exit"] == 0 else f"rc={r['exit']}")
        print(f"{r['cell']:<46}{verdict:<10}{ws}{str(sw):>10}{str(cf):>9}")

    rej = [r for r in rows if r["exit"] == 3]
    noframes = [r for r in rows if r["camera_frames"] is None]
    print()
    print(f"⇒ 唯一 take 数 {len({r['tracker'] for r in rows})}, cell 数 {len(rows)}")
    print(f"⇒ 分支门 REJECT 的 cell: {len(rej)}/{len(rows)}")
    for r in rej:
        w = r["worst"]
        wtxt = f"{w * 100:.1f}%" if isinstance(w, (int, float)) else "—"
        print(f"     {r['cell']:<46} 受影响 {wtxt}  换分支 {r['switches']} 次")
    if noframes:
        print(f"⇒ ⚠ {len(noframes)} 个 cell 拿不到 d405_frames.csv ⇒ 无法把跳变映射到相机帧,"
              f" 判定降级(只扫整条 tracker)")

    if sweep:
        print()
        print("=" * 96)
        print("与尾段扫描 (A_G2_current) 对表")
        print("-" * 96)
        hit = miss = 0
        for r in rows:
            for sub in ("sparse", "tight"):
                gid = f"{r['cell']}/{sub}"
                m = sweep.get(gid)
                if not m:
                    continue
                tag = "REJECT" if r["exit"] == 3 else "PASS  "
                bad = not m["pass"]
                if bad and r["exit"] == 3:
                    hit += 1
                elif bad:
                    miss += 1
                print(f"  {gid:<50}{tag}  max {m['mx']:6.2f}  rot {m['rot']:4.2f}  "
                      f"w10 {m['w10']:5.1f}{'' if m['pass'] else '  ✗'}")
        print()
        print(f"⇒ 扫描未达标的 cell 里, 分支门 REJECT 解释了 {hit} 个, "
              f"仍无法解释 {miss} 个")

    (OUT / "tracker_branch_vs_gate.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1))
    print(f"\n已写 {OUT / 'tracker_branch_vs_gate.json'}")


if __name__ == "__main__":
    main()
