#!/usr/bin/env python3
"""Generate a dependency-free, mouse-rotatable 3D SLAM comparison.

External ground truth is used only to compute the same SE(3), no-scale
visualization alignment used by the precision evaluator.  It is never fed
back into any SLAM trajectory.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from evaluate_slam_ground_truth import (
    camera_trajectory_to_body,
    interpolate_ground_truth,
    load_opencv_matrix,
    load_trajectory,
    rigid_align,
)


COLORS = ("#f59e0b", "#38bdf8", "#22c55e", "#e879f9")


def highlight_segment(
    time: np.ndarray,
    points: np.ndarray,
    window_s: tuple[float, float] | None,
) -> list[list[float]]:
    if window_s is None:
        return []
    relative_time = time - time[0]
    selected = (relative_time >= window_s[0]) & (relative_time <= window_s[1])
    return points[selected].tolist()


def aligned_trace(
    name: str,
    path: Path,
    gt_time: np.ndarray,
    gt_position: np.ndarray,
    gt_quaternion: np.ndarray,
    *,
    camera_to_body: np.ndarray | None,
    color: str,
    max_gap_s: float,
    highlight_window_s: tuple[float, float] | None,
    highlight_color: str,
) -> dict[str, object]:
    time, position, quaternion = load_trajectory(path)
    if camera_to_body is not None:
        position, quaternion = camera_trajectory_to_body(
            position, quaternion, camera_to_body
        )
    inside, valid, interpolated, _ = interpolate_ground_truth(
        time, gt_time, gt_position, gt_quaternion, max_gap_s
    )
    selected = position[inside][valid]
    reference = interpolated[:, 1:]
    if len(selected) < 20:
        raise ValueError(f"{name}: fewer than 20 timestamp-overlapped samples")
    rotation, translation = rigid_align(selected, reference)
    aligned = (rotation @ position.T).T + translation
    error_mm = np.linalg.norm((rotation @ selected.T).T + translation - reference, axis=1) * 1000.0
    return {
        "name": name,
        "color": color,
        "points": aligned.tolist(),
        "highlight_points": highlight_segment(time, aligned, highlight_window_s),
        "highlight_color": highlight_color,
        "samples": int(len(selected)),
        "rmse_mm": float(np.sqrt(np.mean(error_mm**2))),
        "p95_mm": float(np.percentile(error_mm, 95.0)),
        "max_mm": float(np.max(error_mm)),
    }


def write_html(output: Path, title: str, traces: list[dict[str, object]]) -> None:
    payload = json.dumps(traces, ensure_ascii=False, separators=(",", ":"))
    html = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>__TITLE__</title>
<style>
body{margin:0;background:#17191d;color:#e8eaed;font:14px system-ui,sans-serif}header{padding:14px 20px 8px}
h1{font-size:20px;margin:0 0 6px}p{margin:0;color:#b9c0c8}.bar{display:flex;gap:14px;align-items:center;flex-wrap:wrap;padding:8px 20px}
button{padding:6px 12px}.item{display:flex;gap:5px;align-items:center}.sw{width:22px;border-top:3px solid}
.panel{margin:0 12px 14px;border:1px solid #3b424b;border-radius:7px;background:#101216;overflow:hidden}
canvas{display:block;width:100%;height:760px;cursor:grab}canvas:active{cursor:grabbing}.metrics{padding:8px 20px 16px;color:#c8cdd3}
table{border-collapse:collapse}td,th{padding:4px 14px 4px 0;text-align:right}td:first-child,th:first-child{text-align:left}
@media(max-width:900px){canvas{height:570px}}
</style></head><body><header><h1>__TITLE__</h1><p>统一为 IMU/body 原点；各算法分别做 SE(3) 刚性对齐，不缩放。拖动旋转，滚轮缩放。</p></header>
<div class="bar"><button id="reset">重置视角</button><span id="toggles"></span></div><div class="panel"><canvas id="view"></canvas></div>
<div class="metrics"><table><thead><tr><th>轨迹</th><th>样本</th><th>RMSE (mm)</th><th>P95 (mm)</th><th>最大 (mm)</th></tr></thead><tbody id="rows"></tbody></table></div>
<script>
const T=__TRACES__;const canvas=document.getElementById('view'),ctx=canvas.getContext('2d');
T.forEach((t,i)=>{t.on=true;let l=document.createElement('label');l.className='item';l.innerHTML=`<input type="checkbox" checked data-i="${i}"><i class="sw" style="color:${t.color}"></i>${t.name}`;document.getElementById('toggles').appendChild(l);document.getElementById('rows').innerHTML+=`<tr><td style="color:${t.color}">${t.name}</td><td>${t.samples}</td><td>${t.rmse_mm.toFixed(3)}</td><td>${t.p95_mm.toFixed(3)}</td><td>${t.max_mm.toFixed(3)}</td></tr>`});
document.querySelectorAll('input[type=checkbox]').forEach(e=>e.onchange=()=>{T[+e.dataset.i].on=e.checked;draw()});
const all=T.flatMap(t=>t.points),lo=[0,1,2].map(k=>Math.min(...all.map(p=>p[k]))),hi=[0,1,2].map(k=>Math.max(...all.map(p=>p[k]))),center=lo.map((v,k)=>(v+hi[k])/2),radius=Math.max(...hi.map((v,k)=>v-lo[k]),1e-6)*.58;
let yaw=-.65,pitch=.42,zoom=1,drag=false,last=[0,0];
function rot(p){let X=p[0]-center[0],Y=p[1]-center[1],Z=p[2]-center[2],cy=Math.cos(yaw),sy=Math.sin(yaw),x=cy*X+sy*Z,z=-sy*X+cy*Z,cp=Math.cos(pitch),sp=Math.sin(pitch);return[x,cp*Y-sp*z,sp*Y+cp*z]}
function proj(p,w,h){let q=rot(p),s=Math.min(w,h)*.82*zoom/radius,d=Math.max(.55,1+q[2]/radius*.22);return[w/2+q[0]*s/d,h/2-q[1]*s/d,q[2]]}
function line(points,color,w,h,width){let sorted=points.map(p=>[p,proj(p,w,h)]);ctx.strokeStyle=color;ctx.lineWidth=width;ctx.beginPath();sorted.forEach((v,i)=>i?ctx.lineTo(v[1][0],v[1][1]):ctx.moveTo(v[1][0],v[1][1]));ctx.stroke()}
function marker(p,color,cross,w,h){let q=proj(p,w,h);ctx.strokeStyle=ctx.fillStyle=color;ctx.lineWidth=2.5;if(cross){ctx.beginPath();ctx.moveTo(q[0]-6,q[1]-6);ctx.lineTo(q[0]+6,q[1]+6);ctx.moveTo(q[0]+6,q[1]-6);ctx.lineTo(q[0]-6,q[1]+6);ctx.stroke()}else{ctx.beginPath();ctx.arc(q[0],q[1],5,0,7);ctx.fill()}}
function highlight(t,w,h){if(!t.highlight_points||!t.highlight_points.length)return;line(t.highlight_points,t.highlight_color,w,h,6);t.highlight_points.forEach(p=>marker(p,t.highlight_color,false,w,h))}
function draw(){let w=canvas.clientWidth,h=canvas.clientHeight;ctx.clearRect(0,0,w,h);ctx.strokeStyle='#2d333b';ctx.lineWidth=1;for(let i=-4;i<=4;i++){let a=proj([center[0]+radius*i/4,center[1]-radius,center[2]],w,h),b=proj([center[0]+radius*i/4,center[1]+radius,center[2]],w,h);ctx.beginPath();ctx.moveTo(a[0],a[1]);ctx.lineTo(b[0],b[1]);ctx.stroke()}T.filter(t=>t.on).forEach((t,i)=>{line(t.points,t.color,w,h,i?1.8:2.4);marker(t.points[0],t.color,false,w,h);marker(t.points[t.points.length-1],t.color,true,w,h);highlight(t,w,h)})}
function resize(){let d=devicePixelRatio||1;canvas.width=canvas.clientWidth*d;canvas.height=canvas.clientHeight*d;ctx.setTransform(d,0,0,d,0,0);draw()}canvas.onpointerdown=e=>{drag=true;last=[e.clientX,e.clientY];canvas.setPointerCapture(e.pointerId)};canvas.onpointermove=e=>{if(!drag)return;let dx=e.clientX-last[0],dy=e.clientY-last[1];last=[e.clientX,e.clientY];yaw+=dx*.01;pitch=Math.max(-1.45,Math.min(1.45,pitch+dy*.01));draw()};canvas.onpointerup=e=>{drag=false;canvas.releasePointerCapture(e.pointerId)};canvas.onwheel=e=>{e.preventDefault();zoom=Math.max(.2,Math.min(6,zoom*Math.exp(-e.deltaY*.001)));draw()};document.getElementById('reset').onclick=()=>{yaw=-.65;pitch=.42;zoom=1;draw()};addEventListener('resize',resize);resize();
</script></body></html>'''.replace("__TITLE__", title).replace("__TRACES__", payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")


def parse_named_path(value: str) -> tuple[str, Path]:
    name, separator, path = value.partition("=")
    if not separator or not name or not path:
        raise argparse.ArgumentTypeError("expected NAME=PATH")
    return name, Path(path)


def parse_time_window(value: str) -> tuple[float, float]:
    try:
        start_text, end_text = value.split(",", 1)
        start, end = float(start_text), float(end_text)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected START_S,END_S") from error
    if start < 0.0 or end < start:
        raise argparse.ArgumentTypeError("highlight window must satisfy 0 <= start <= end")
    return start, end


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ground-truth", type=Path, required=True)
    parser.add_argument("--trajectory", type=parse_named_path, action="append", default=[])
    parser.add_argument("--camera-trajectory", type=parse_named_path, action="append", default=[])
    parser.add_argument("--body-camera-yaml", type=Path)
    parser.add_argument("--body-camera-key", default="body_T_cam0")
    parser.add_argument("--max-interpolation-gap-s", type=float, default=0.1)
    parser.add_argument("--highlight-window-s", type=parse_time_window)
    parser.add_argument("--title", default="SLAM 三维轨迹对比")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.trajectory and not args.camera_trajectory:
        parser.error("at least one trajectory is required")
    if args.camera_trajectory and args.body_camera_yaml is None:
        parser.error("--camera-trajectory requires --body-camera-yaml")

    gt_time, gt_position, gt_quaternion = load_trajectory(args.ground_truth)
    traces: list[dict[str, object]] = [
        {
            "name": "Lighthouse GT",
            "color": "#f8fafc",
            "points": gt_position.tolist(),
            "highlight_points": highlight_segment(
                gt_time, gt_position, args.highlight_window_s
            ),
            "highlight_color": "#ef4444",
            "samples": int(len(gt_position)),
            "rmse_mm": 0.0,
            "p95_mm": 0.0,
            "max_mm": 0.0,
        }
    ]
    body_camera = (
        load_opencv_matrix(args.body_camera_yaml, args.body_camera_key)
        if args.body_camera_yaml is not None
        else None
    )
    specifications = [
        *((name, path, None) for name, path in args.trajectory),
        *((name, path, body_camera) for name, path in args.camera_trajectory),
    ]
    for index, (name, path, conversion) in enumerate(specifications):
        traces.append(
            aligned_trace(
                name,
                path,
                gt_time,
                gt_position,
                gt_quaternion,
                camera_to_body=conversion,
                color=COLORS[index % len(COLORS)],
                max_gap_s=args.max_interpolation_gap_s,
                highlight_window_s=args.highlight_window_s,
                highlight_color="#fde047",
            )
        )
    write_html(args.output, args.title, traces)
    summary = [
        {
            key: value
            for key, value in trace.items()
            if key not in {"points", "highlight_points"}
        }
        for trace in traces
    ]
    print(
        json.dumps(
            {"result": "PASS", "output": str(args.output.resolve()), "traces": summary},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
