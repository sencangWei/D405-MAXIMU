#!/usr/bin/env python3
"""Generate a rigidly aligned 3-D overlay for trajectory-shape diagnosis.

This tool deliberately changes neither input trajectory.  It estimates one
SE(3) transform (Kabsch, no scale and no endpoint constraint) from paired
samples, then reports the residual shape error and writes a rotatable HTML
viewer plus static diagnostic plots.
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
    required = {"t_sec", "umi_x", "umi_y", "umi_z", "robot_actual_x", "robot_actual_y", "robot_actual_z"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"matched CSV缺少字段: {path}")
    times = np.asarray([float(row["t_sec"]) for row in rows], dtype=float)
    umi = np.asarray([[float(row[f"umi_{axis}"]) for axis in "xyz"] for row in rows], dtype=float)
    robot = np.asarray(
        [[float(row[f"robot_actual_{axis}"]) for axis in "xyz"] for row in rows],
        dtype=float,
    )
    if not np.all(np.isfinite(np.c_[times, umi, robot])):
        raise ValueError("matched CSV包含非有限值")
    order = np.argsort(times, kind="stable")
    times, umi, robot = times[order], umi[order], robot[order]
    keep = np.r_[True, np.diff(times) > 0.0]
    return times[keep], umi[keep], robot[keep]


def kabsch(source: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    source_center = source.mean(axis=0)
    target_center = target.mean(axis=0)
    covariance = (source - source_center).T @ (target - target_center)
    u, _, vt = np.linalg.svd(covariance)
    rotation = vt.T @ u.T
    if np.linalg.det(rotation) < 0.0:
        vt[-1] *= -1.0
        rotation = vt.T @ u.T
    translation = target_center - rotation @ source_center
    return rotation, translation


def summary(values_m: np.ndarray) -> dict[str, float | int]:
    values_mm = np.asarray(values_m, dtype=float) * 1000.0
    return {
        "count": int(values_mm.size),
        "rmse_mm": float(np.sqrt(np.mean(values_mm * values_mm))),
        "mean_mm": float(np.mean(values_mm)),
        "median_mm": float(np.median(values_mm)),
        "p95_mm": float(np.percentile(values_mm, 95)),
        "max_mm": float(np.max(values_mm)),
        "within_10mm_percent": float(np.mean(values_mm <= 10.0) * 100.0),
        "within_20mm_percent": float(np.mean(values_mm <= 20.0) * 100.0),
    }


def time_bin_summaries(times: np.ndarray, residual_m: np.ndarray, width_s: float = 10.0) -> list[dict]:
    """Summarize shape residual by elapsed-time bins without altering samples."""
    relative = times - times[0]
    duration = float(relative[-1])
    bins: list[dict] = []
    for index in range(max(1, int(np.ceil(duration / width_s)))):
        start = index * width_s
        end = min((index + 1) * width_s, duration)
        if index == 0:
            mask = relative < end if end == duration else relative < end
        elif index == int(np.ceil(duration / width_s)) - 1:
            mask = (relative >= start) & (relative <= duration)
        else:
            mask = (relative >= start) & (relative < end)
        if not np.any(mask):
            continue
        bins.append({
            "start_s": float(start),
            "end_s": float(end),
            **summary(residual_m[mask]),
        })
    return bins


def _html_payload(robot: np.ndarray, umi_raw: np.ndarray, umi_aligned: np.ndarray, error_mm: np.ndarray, metrics: dict) -> str:
    payload = {
        "robot": robot.tolist(),
        "umiRaw": umi_raw.tolist(),
        "umiAligned": umi_aligned.tolist(),
        "errorMm": error_mm.tolist(),
        "metrics": metrics,
    }
    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>固定 SE(3) 轨迹重合诊断</title>
<style>
body{margin:0;background:#15171b;color:#e8eaed;font:14px system-ui,sans-serif}
header{padding:16px 20px 8px}h1{font-size:20px;margin:0 0 6px}p{margin:4px 0;color:#bdc4cc}
.bar{display:flex;gap:16px;align-items:center;flex-wrap:wrap;padding:8px 20px}.bar label{cursor:pointer}
button{padding:5px 10px;background:#2b3038;color:#eee;border:1px solid #555;border-radius:4px}
.panel{margin:0 16px 16px;background:#1f2329;border:1px solid #3c444e;border-radius:6px;overflow:hidden}
.panel h2{font-size:15px;font-weight:500;padding:10px 12px;margin:0}.metrics{padding:8px 12px;color:#cad0d7}
canvas{display:block;width:100%;height:720px;background:#0e1013;cursor:grab}canvas:active{cursor:grabbing}
.sw{display:inline-block;width:22px;border-top:3px solid currentColor;vertical-align:middle;margin:0 5px 0 0}
</style></head><body>
<header><h1>固定 SE(3) 轨迹重合诊断</h1>
<p>只消除整体坐标系平移/旋转；不修改原始轨迹、不使用终点约束、不做非刚性拉伸。拖动旋转，滚轮缩放。</p></header>
<div class="bar"><button id="reset">重置视角</button><label><input id="raw" type="checkbox"> 显示未对齐 UMI</label>
<span style="color:#f4f4f5"><i class="sw"></i>机械臂 actual TCP</span><span style="color:#22c55e"><i class="sw"></i>UMI（固定 SE(3) 后）</span><span style="color:#5b8def"><i class="sw"></i>UMI（原始）</span></div>
<section class="panel"><h2 id="title"></h2><canvas id="view"></canvas><div class="metrics" id="metrics"></div></section>
<script>
const D=__PAYLOAD__;
const canvas=document.getElementById('view'),ctx=canvas.getContext('2d');
let yaw=-0.65,pitch=0.42,zoom=1,drag=false,last=[0,0];
const all=D.robot.concat(D.umiAligned),lo=[0,1,2].map(k=>Math.min(...all.map(p=>p[k]))),hi=[0,1,2].map(k=>Math.max(...all.map(p=>p[k]))),ctr=lo.map((v,k)=>(v+hi[k])/2),radius=Math.max(...hi.map((v,k)=>v-lo[k]),1e-6)*0.58;
function rot(p){let X=p[0]-ctr[0],Y=p[1]-ctr[1],Z=p[2]-ctr[2],cy=Math.cos(yaw),sy=Math.sin(yaw),x1=cy*X+sy*Z,z1=-sy*X+cy*Z,cp=Math.cos(pitch),sp=Math.sin(pitch);return[x1,cp*Y-sp*z1,sp*Y+cp*z1]}
function proj(p,w,h){let q=rot(p),sc=Math.min(w,h)*.82*zoom/radius,de=Math.max(.65,1+q[2]/radius*.18);return[w/2+q[0]*sc/de,h/2-q[1]*sc/de]}
function line(P,col,w,h,width=1.8){ctx.strokeStyle=col;ctx.lineWidth=width;ctx.beginPath();P.forEach((p,i)=>{let q=proj(p,w,h);i?ctx.lineTo(q[0],q[1]):ctx.moveTo(q[0],q[1])});ctx.stroke()}
function mark(p,col,cross,w,h){let q=proj(p,w,h);ctx.strokeStyle=ctx.fillStyle=col;ctx.lineWidth=3;if(cross){ctx.beginPath();ctx.moveTo(q[0]-8,q[1]-8);ctx.lineTo(q[0]+8,q[1]+8);ctx.moveTo(q[0]+8,q[1]-8);ctx.lineTo(q[0]-8,q[1]+8);ctx.stroke()}else{ctx.beginPath();ctx.arc(q[0],q[1],7,0,Math.PI*2);ctx.fill()}}
function draw(){let w=canvas.clientWidth,h=canvas.clientHeight;ctx.clearRect(0,0,w,h);ctx.strokeStyle='#303741';ctx.lineWidth=1;for(let i=-4;i<=4;i++){let a=proj([ctr[0]+radius*i/4,ctr[1]-radius,ctr[2]],w,h),b=proj([ctr[0]+radius*i/4,ctr[1]+radius,ctr[2]],w,h);ctx.beginPath();ctx.moveTo(a[0],a[1]);ctx.lineTo(b[0],b[1]);ctx.stroke()}if(document.getElementById('raw').checked)line(D.umiRaw,'#5b8def',w,h,1.2);line(D.robot,'#f4f4f5',w,h,2.0);line(D.umiAligned,'#22c55e',w,h,2.0);mark(D.robot[0],'#16a34a',false,w,h);mark(D.robot[D.robot.length-1],'#ef4444',true,w,h);mark(D.umiAligned[0],'#86efac',false,w,h);mark(D.umiAligned[D.umiAligned.length-1],'#f472b6',true,w,h)}
function resize(){let z=devicePixelRatio||1;canvas.width=canvas.clientWidth*z;canvas.height=canvas.clientHeight*z;ctx.setTransform(z,0,0,z,0,0);draw()}
canvas.onpointerdown=e=>{drag=true;last=[e.clientX,e.clientY];canvas.setPointerCapture(e.pointerId)};canvas.onpointermove=e=>{if(!drag)return;let dx=e.clientX-last[0],dy=e.clientY-last[1];last=[e.clientX,e.clientY];yaw+=dx*.01;pitch=Math.max(-1.45,Math.min(1.45,pitch+dy*.01));draw()};canvas.onpointerup=e=>{drag=false;canvas.releasePointerCapture(e.pointerId)};canvas.onwheel=e=>{e.preventDefault();zoom=Math.max(.25,Math.min(5,zoom*Math.exp(-e.deltaY*.001)));draw()};window.onresize=resize;document.getElementById('raw').onchange=draw;document.getElementById('reset').onclick=()=>{yaw=-.65;pitch=.42;zoom=1;draw()};
document.getElementById('title').textContent=`${D.metrics.points} 个时间配对点｜固定 SE(3) 对齐后 RMSE ${D.metrics.residual.rmse_mm.toFixed(2)} mm｜P95 ${D.metrics.residual.p95_mm.toFixed(2)} mm`;
document.getElementById('metrics').textContent=`绿色为固定刚体对齐后的 UMI；全段 ≤10 mm：${D.metrics.residual.within_10mm_percent.toFixed(2)}%，≤20 mm：${D.metrics.residual.within_20mm_percent.toFixed(2)}%。`;
resize();
</script></body></html>""".replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def write_plots(out: Path, robot: np.ndarray, aligned: np.ndarray, error_mm: np.ndarray, times: np.ndarray) -> None:
    # The host can have both pip and Ubuntu Matplotlib on sys.path.  Use the
    # system interpreter with user-site packages disabled, as the product
    # evaluator does, so Axes3D is deterministic and no runtime environment
    # change is needed for the caller.
    payload_path = out / ".rigid_overlay_plot_payload.npz"
    np.savez(payload_path, robot=robot, aligned=aligned, error_mm=error_mm, times=times)
    plot_code = r'''
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
payload = np.load(sys.argv[1])
out = sys.argv[2]
robot, aligned = payload["robot"], payload["aligned"]
error_mm, times = payload["error_mm"], payload["times"]
fig = plt.figure(figsize=(11, 8), dpi=160)
ax = fig.add_subplot(111, projection="3d")
ax.plot(robot[:, 0], robot[:, 1], robot[:, 2], color="#202124", linewidth=1.4, label="Robot actual TCP")
ax.plot(aligned[:, 0], aligned[:, 1], aligned[:, 2], color="#1aaf5d", linewidth=1.4, label="UMI (rigid SE(3))")
ax.scatter(*robot[0], color="#16a34a", s=45, label="Start")
ax.scatter(*robot[-1], color="#dc2626", s=55, marker="x", label="End")
ax.set_xlabel("X (m)"); ax.set_ylabel("Y (m)"); ax.set_zlabel("Z (m)")
ax.set_title(f"Rigid SE(3) overlay | RMSE {np.sqrt(np.mean(error_mm**2)):.2f} mm | P95 {np.percentile(error_mm,95):.2f} mm")
ax.legend(loc="upper left")
fig.tight_layout(); fig.savefig(out + "/rigid_aligned_overlay_3d.png"); plt.close(fig)
fig, ax = plt.subplots(figsize=(12, 4.8), dpi=160)
rel_t = times - times[0]
ax.plot(rel_t, error_mm, color="#2563eb", linewidth=1.0, label="3-D residual")
ax.axhline(10.0, color="#dc2626", linestyle="--", linewidth=1.0, label="10 mm")
ax.fill_between(rel_t, 0, error_mm, color="#93c5fd", alpha=0.25)
ax.set_xlabel("Relative time (s)"); ax.set_ylabel("Residual after alignment (mm)"); ax.grid(alpha=.25); ax.legend()
ax.set_title("Shape residual after rigid SE(3) alignment")
fig.tight_layout(); fig.savefig(out + "/rigid_aligned_residual_timeline.png"); plt.close(fig)
'''
    env = os.environ.copy()
    env["PYTHONNOUSERSITE"] = "1"
    completed = subprocess.run(
        ["/usr/bin/python3", "-c", plot_code, str(payload_path), str(out)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    try:
        payload_path.unlink()
    except OSError:
        pass
    if completed.returncode != 0:
        raise RuntimeError(f"静态三维图生成失败: {completed.stderr.strip()}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matched-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    times, umi, robot = load_matched(args.matched_csv.resolve())
    if len(times) < 3:
        raise SystemExit("时间配对点少于3个")
    rotation, translation = kabsch(umi, robot)
    aligned = (rotation @ umi.T).T + translation
    residual_vector = aligned - robot
    residual_norm = np.linalg.norm(residual_vector, axis=1)
    metrics = {
        "schema": "rigid_overlay_shape_diagnostic_v1",
        "input_matched_csv": str(args.matched_csv.resolve()),
        "points": int(len(times)),
        "time_start_epoch_s": float(times[0]),
        "time_end_epoch_s": float(times[-1]),
        "duration_s": float(times[-1] - times[0]),
        "alignment": "single Kabsch SE(3), no scale, no endpoint constraint, diagnostic only",
        "rotation_umi_to_robot": rotation.tolist(),
        "translation_m_umi_to_robot": translation.tolist(),
        "residual": summary(residual_norm),
        "residual_axis_robot_frame": {
            axis: summary(np.abs(residual_vector[:, index])) for index, axis in enumerate("xyz")
        },
        "residual_time_bins_10s": time_bin_summaries(times, residual_norm),
        "residual_signed_mean_mm_robot_frame": (residual_vector.mean(axis=0) * 1000.0).tolist(),
        "residual_signed_std_mm_robot_frame": (residual_vector.std(axis=0) * 1000.0).tolist(),
        "interpretation": (
            "固定刚体变换只能消除整体坐标系差异；剩余误差代表时间配对、刚体外参、"
            "机器人跟随/建模或SLAM形状差异，不能据此宣称绝对TCP精度。"
        ),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "rigid_overlay_shape_diagnostic.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "rigid_overlay_shape_diagnostic.html").write_text(
        _html_payload(robot, umi, aligned, residual_norm * 1000.0, metrics), encoding="utf-8"
    )
    write_plots(args.output_dir, robot, aligned, residual_norm * 1000.0, times)
    report = [
        "# 固定 SE(3) 轨迹重合诊断",
        "",
        "本报告只做一次固定刚体坐标对齐，不修改原始轨迹，不使用终点答案，不做非刚性变形。",
        "",
        f"- 时间配对点：{len(times)}",
        f"- 时间范围：{times[-1] - times[0]:.3f} s",
        f"- 对齐后 RMSE：{metrics['residual']['rmse_mm']:.3f} mm",
        f"- 对齐后 P95：{metrics['residual']['p95_mm']:.3f} mm",
        f"- 对齐后最大值：{metrics['residual']['max_mm']:.3f} mm",
        f"- 全段 ≤10 mm 比例：{metrics['residual']['within_10mm_percent']:.2f}%",
        f"- 全段 ≤20 mm 比例：{metrics['residual']['within_20mm_percent']:.2f}%",
        "",
        "## 按时间查看形状差异",
        "",
        "以下分段仍使用同一个固定 SE(3) 变换；只把残差按时间分桶，不对轨迹做局部变形。",
        "",
        "| 相对时间 | 点数 | RMSE | P95 | ≤10 mm |",
        "|---:|---:|---:|---:|---:|",
        *[
            f"| {item['start_s']:.1f}–{item['end_s']:.1f} s | {item['count']} | "
            f"{item['rmse_mm']:.2f} mm | {item['p95_mm']:.2f} mm | "
            f"{item['within_10mm_percent']:.1f}% |"
            for item in metrics["residual_time_bins_10s"]
        ],
        "",
        "## 结论",
        "",
        "如果只允许一个固定平移/旋转，整段轨迹不能维持在 1 cm 内；超过 1 cm 的部分是形状/时间/执行链残差，不能靠继续平移把它消除。",
        "图中的绿色轨迹是诊断用固定 SE(3) 对齐结果，黑色轨迹仍是机械臂原始 actual TCP。",
        "",
        "## 文件",
        "",
        "- `rigid_overlay_shape_diagnostic.html`：可拖动旋转的三维叠加图",
        "- `rigid_aligned_overlay_3d.png`：静态三维图",
        "- `rigid_aligned_residual_timeline.png`：残差随时间图",
        "- `rigid_overlay_shape_diagnostic.json`：变换矩阵与完整统计",
    ]
    (args.output_dir / "rigid_overlay_shape_diagnostic.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
