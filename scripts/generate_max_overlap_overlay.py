#!/usr/bin/env python3
"""Place two recorded trajectories at maximum geometric overlap for diagnosis.

Unlike time-paired evaluation, this tool ignores timestamp-to-timestamp
correspondence and fits one fixed SE(3) transform with trimmed point-to-point
ICP.  It changes only the rendered UMI copy; neither source trajectory is
rewritten and no endpoint constraint or non-rigid warp is used.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
from pathlib import Path

import numpy as np


def load_matched(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError(f"matched CSV没有数据行: {path}")
    fields = set(rows[0])
    # Relative-evaluation CSVs use the historical sensor/URDF names.  The
    # TCP-mapped evaluator writes the same geometry under explicit TCP names.
    # Both are valid inputs for this *diagnostic-only* geometric overlay.
    if {"umi_x", "umi_y", "umi_z", "robot_actual_x", "robot_actual_y", "robot_actual_z"}.issubset(fields):
        umi_fields = tuple(f"umi_{axis}" for axis in "xyz")
        robot_fields = tuple(f"robot_actual_{axis}" for axis in "xyz")
    elif {"umi_tcp_x", "umi_tcp_y", "umi_tcp_z", "robot_tcp_x", "robot_tcp_y", "robot_tcp_z"}.issubset(fields):
        umi_fields = tuple(f"umi_tcp_{axis}" for axis in "xyz")
        robot_fields = tuple(f"robot_tcp_{axis}" for axis in "xyz")
    else:
        raise ValueError(f"matched CSV缺少支持的轨迹字段: {path}")
    times = np.asarray([float(row["t_sec"]) for row in rows], dtype=float)
    umi = np.asarray([[float(row[field]) for field in umi_fields] for row in rows], dtype=float)
    robot = np.asarray([[float(row[field]) for field in robot_fields] for row in rows], dtype=float)
    order = np.argsort(times, kind="stable")
    times, umi, robot = times[order], umi[order], robot[order]
    keep = np.r_[True, np.diff(times) > 0.0]
    return times[keep], umi[keep], robot[keep]


def kabsch(source: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    source_center = source.mean(axis=0)
    target_center = target.mean(axis=0)
    u, _, vt = np.linalg.svd((source - source_center).T @ (target - target_center))
    rotation = vt.T @ u.T
    if np.linalg.det(rotation) < 0.0:
        vt[-1] *= -1.0
        rotation = vt.T @ u.T
    return rotation, target_center - rotation @ source_center


def apply(points: np.ndarray, rotation: np.ndarray, translation: np.ndarray) -> np.ndarray:
    return (rotation @ points.T).T + translation


def nearest(source: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    distances_sq = ((source[:, None, :] - target[None, :, :]) ** 2).sum(axis=2)
    indices = distances_sq.argmin(axis=1)
    return indices, np.sqrt(distances_sq[np.arange(len(source)), indices])


def icp(source: np.ndarray, target: np.ndarray, trim_fraction: float = 0.5) -> tuple[np.ndarray, np.ndarray, int]:
    rotation, translation = kabsch(source, target)
    keep_count = max(3, int(len(source) * trim_fraction))
    for iteration in range(100):
        aligned = apply(source, rotation, translation)
        indices, distances = nearest(aligned, target)
        keep = np.argsort(distances)[:keep_count]
        next_rotation, next_translation = kabsch(source[keep], target[indices[keep]])
        change = np.linalg.norm(next_rotation - rotation) + np.linalg.norm(next_translation - translation)
        rotation, translation = next_rotation, next_translation
        if change < 1e-10:
            break
    return rotation, translation, iteration + 1


def stats(aligned: np.ndarray, target: np.ndarray) -> dict[str, float | int]:
    _, forward = nearest(aligned, target)
    _, reverse = nearest(target, aligned)
    distances_mm = np.r_[forward, reverse] * 1000.0
    return {
        "symmetric_count": int(distances_mm.size),
        "symmetric_rmse_mm": float(np.sqrt(np.mean(distances_mm**2))),
        "symmetric_median_mm": float(np.median(distances_mm)),
        "symmetric_p95_mm": float(np.percentile(distances_mm, 95)),
        "symmetric_max_mm": float(np.max(distances_mm)),
        "symmetric_within_10mm_percent": float(np.mean(distances_mm <= 10.0) * 100.0),
        "umi_to_robot_rmse_mm": float(np.sqrt(np.mean(forward**2)) * 1000.0),
        "robot_to_umi_rmse_mm": float(np.sqrt(np.mean(reverse**2)) * 1000.0),
    }


def html_payload(robot: np.ndarray, umi_raw: np.ndarray, umi_aligned: np.ndarray, report: dict) -> str:
    payload = {
        "robot": robot.tolist(),
        "umiRaw": umi_raw.tolist(),
        "umiAligned": umi_aligned.tolist(),
        "report": report,
    }
    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>几何最大重合轨迹</title><style>
body{margin:0;background:#15171b;color:#e8eaed;font:14px system-ui,sans-serif}header{padding:16px 20px 8px}h1{font-size:20px;margin:0 0 6px}p{margin:4px 0;color:#bdc4cc}.bar{display:flex;gap:16px;align-items:center;flex-wrap:wrap;padding:8px 20px}.bar label{cursor:pointer}button{padding:5px 10px;background:#2b3038;color:#eee;border:1px solid #555;border-radius:4px}.panel{margin:0 16px 16px;background:#1f2329;border:1px solid #3c444e;border-radius:6px;overflow:hidden}.panel h2{font-size:15px;font-weight:500;padding:10px 12px;margin:0}canvas{display:block;width:100%;height:720px;background:#0e1013;cursor:grab}canvas:active{cursor:grabbing}.sw{display:inline-block;width:22px;border-top:3px solid currentColor;vertical-align:middle;margin:0 5px 0 0}
</style></head><body><header><h1>几何最大重合轨迹</h1><p>白色=机械臂原始 TCP；绿色=UMI 经过一次最大重合刚体放置后的轨迹。拖动旋转，滚轮缩放；不按时间点强行配对，不改变任何原始轨迹。</p></header>
<div class="bar"><button id="reset">重置视角</button><label><input id="raw" type="checkbox"> 显示未放置的 UMI</label><span style="color:#f4f4f5"><i class="sw"></i>机械臂 actual TCP</span><span style="color:#22c55e"><i class="sw"></i>UMI 最大重合放置</span><span style="color:#5b8def"><i class="sw"></i>UMI 原始</span></div>
<section class="panel"><h2>同一三维视图</h2><canvas id="view"></canvas></section><script>
const D=__PAYLOAD__,canvas=document.getElementById('view'),ctx=canvas.getContext('2d');let yaw=-.65,pitch=.42,zoom=1,drag=false,last=[0,0];const all=D.robot.concat(D.umiAligned),lo=[0,1,2].map(k=>Math.min(...all.map(p=>p[k]))),hi=[0,1,2].map(k=>Math.max(...all.map(p=>p[k]))),ctr=lo.map((v,k)=>(v+hi[k])/2),radius=Math.max(...hi.map((v,k)=>v-lo[k]),1e-6)*.58;
function rot(p){let X=p[0]-ctr[0],Y=p[1]-ctr[1],Z=p[2]-ctr[2],cy=Math.cos(yaw),sy=Math.sin(yaw),x1=cy*X+sy*Z,z1=-sy*X+cy*Z,cp=Math.cos(pitch),sp=Math.sin(pitch);return[x1,cp*Y-sp*z1,sp*Y+cp*z1]};function proj(p,w,h){let q=rot(p),sc=Math.min(w,h)*.82*zoom/radius,de=Math.max(.65,1+q[2]/radius*.18);return[w/2+q[0]*sc/de,h/2-q[1]*sc/de]};function line(P,col,w,h,width=2){ctx.strokeStyle=col;ctx.lineWidth=width;ctx.beginPath();P.forEach((p,i)=>{let q=proj(p,w,h);i?ctx.lineTo(q[0],q[1]):ctx.moveTo(q[0],q[1])});ctx.stroke()};function mark(p,col,cross,w,h){let q=proj(p,w,h);ctx.strokeStyle=ctx.fillStyle=col;ctx.lineWidth=3;if(cross){ctx.beginPath();ctx.moveTo(q[0]-8,q[1]-8);ctx.lineTo(q[0]+8,q[1]+8);ctx.moveTo(q[0]+8,q[1]-8);ctx.lineTo(q[0]-8,q[1]+8);ctx.stroke()}else{ctx.beginPath();ctx.arc(q[0],q[1],7,0,Math.PI*2);ctx.fill()}};function draw(){let w=canvas.clientWidth,h=canvas.clientHeight;ctx.clearRect(0,0,w,h);ctx.strokeStyle='#303741';ctx.lineWidth=1;for(let i=-4;i<=4;i++){let a=proj([ctr[0]+radius*i/4,ctr[1]-radius,ctr[2]],w,h),b=proj([ctr[0]+radius*i/4,ctr[1]+radius,ctr[2]],w,h);ctx.beginPath();ctx.moveTo(a[0],a[1]);ctx.lineTo(b[0],b[1]);ctx.stroke()}if(document.getElementById('raw').checked)line(D.umiRaw,'#5b8def',w,h,1.2);line(D.robot,'#f4f4f5',w,h,2.2);line(D.umiAligned,'#22c55e',w,h,2.2);mark(D.robot[0],'#16a34a',false,w,h);mark(D.robot[D.robot.length-1],'#ef4444',true,w,h)};function resize(){let z=devicePixelRatio||1;canvas.width=canvas.clientWidth*z;canvas.height=canvas.clientHeight*z;ctx.setTransform(z,0,0,z,0,0);draw()};canvas.onpointerdown=e=>{drag=true;last=[e.clientX,e.clientY];canvas.setPointerCapture(e.pointerId)};canvas.onpointermove=e=>{if(!drag)return;let dx=e.clientX-last[0],dy=e.clientY-last[1];last=[e.clientX,e.clientY];yaw+=dx*.01;pitch=Math.max(-1.45,Math.min(1.45,pitch+dy*.01));draw()};canvas.onpointerup=e=>{drag=false;canvas.releasePointerCapture(e.pointerId)};canvas.onwheel=e=>{e.preventDefault();zoom=Math.max(.25,Math.min(5,zoom*Math.exp(-e.deltaY*.001)));draw()};window.onresize=resize;document.getElementById('raw').onchange=draw;document.getElementById('reset').onclick=()=>{yaw=-.65;pitch=.42;zoom=1;draw()};resize();
</script></body></html>""".replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def write_static_plot(out: Path, robot: np.ndarray, aligned: np.ndarray, report: dict) -> None:
    payload_path = out / ".max_overlap_payload.npz"
    np.savez(payload_path, robot=robot, aligned=aligned)
    plot_code = r'''
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
payload=np.load(sys.argv[1]); out=sys.argv[2]; robot,aligned=payload["robot"],payload["aligned"]
fig=plt.figure(figsize=(11,8),dpi=160); ax=fig.add_subplot(111,projection="3d")
ax.plot(robot[:,0],robot[:,1],robot[:,2],color="#202124",linewidth=1.5,label="Robot actual TCP")
ax.plot(aligned[:,0],aligned[:,1],aligned[:,2],color="#16a34a",linewidth=1.5,label="UMI maximum-overlap placement")
ax.scatter(*robot[0],color="#16a34a",s=42,label="Start"); ax.scatter(*robot[-1],color="#dc2626",s=52,marker="x",label="End")
ax.set_xlabel("X (m)"); ax.set_ylabel("Y (m)"); ax.set_zlabel("Z (m)"); ax.set_title("Maximum geometric overlap (fixed SE(3))")
ax.legend(loc="upper left"); fig.tight_layout(); fig.savefig(out+"/max_overlap_overlay_3d.png"); plt.close(fig)
'''
    env = os.environ.copy()
    env["PYTHONNOUSERSITE"] = "1"
    completed = subprocess.run(["/usr/bin/python3", "-c", plot_code, str(payload_path), str(out)], capture_output=True, text=True, env=env)
    try:
        payload_path.unlink()
    except OSError:
        pass
    if completed.returncode:
        raise RuntimeError(f"静态图生成失败: {completed.stderr.strip()}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matched-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--trim-fraction",
        type=float,
        default=0.5,
        help=(
            "每轮 ICP 保留的最近点比例，范围 (0,1]；默认 0.5 抑制局部离群，"
            "需要最大化整条曲线重合时可设为 1.0"
        ),
    )
    args = parser.parse_args()
    if not 0.0 < args.trim_fraction <= 1.0:
        raise SystemExit("--trim-fraction 必须在 (0, 1] 内")
    times, umi, robot = load_matched(args.matched_csv.resolve())
    if len(times) < 3:
        raise SystemExit("有效轨迹点少于3个")
    rotation, translation, iterations = icp(
        umi, robot, trim_fraction=args.trim_fraction
    )
    aligned = apply(umi, rotation, translation)
    report = {
        "schema": "maximum_overlap_overlay_v1",
        "input_matched_csv": str(args.matched_csv.resolve()),
        "points": int(len(times)),
        "duration_s": float(times[-1] - times[0]),
        "method": "trimmed point-to-point ICP; symmetric nearest-neighbor objective; timestamps ignored for geometry fit",
        "trim_fraction": float(args.trim_fraction),
        "iterations": iterations,
        "rotation_umi_to_robot": rotation.tolist(),
        "translation_m_umi_to_robot": translation.tolist(),
        "overlap": stats(aligned, robot),
        "interpretation": "仅用于把两条曲线按几何最大重合放在同一视图；不替代时间对齐的EVO/ATE，也不修改原始轨迹。",
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "max_overlap_overlay.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "max_overlap_overlay.html").write_text(html_payload(robot, umi, aligned, report), encoding="utf-8")
    write_static_plot(args.output_dir, robot, aligned, report)
    md = [
        "# 几何最大重合轨迹诊断", "",
        "白色为机械臂原始 actual TCP，绿色为 UMI 经过一次固定 SE(3) 最大重合放置后的轨迹。原始轨迹未修改；不使用首尾答案、不做非刚性变形。", "",
        f"- 点数：{len(times)}", f"- 原始时间范围：{times[-1] - times[0]:.3f} s", f"- ICP 迭代：{iterations}",
        f"- 对称最近点 RMSE：{report['overlap']['symmetric_rmse_mm']:.3f} mm",
        f"- 对称最近点 P95：{report['overlap']['symmetric_p95_mm']:.3f} mm",
        f"- 对称最近点 ≤10 mm：{report['overlap']['symmetric_within_10mm_percent']:.2f}%", "",
        "几何拟合忽略时间点对应关系，适合观察两条曲线本身哪里不同；它不是绝对精度或 EVO 指标。", "",
        "- `max_overlap_overlay.html`：可旋转三维叠加图", "- `max_overlap_overlay_3d.png`：静态三维图", "- `max_overlap_overlay.json`：变换与重合统计",
    ]
    (args.output_dir / "max_overlap_overlay.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
