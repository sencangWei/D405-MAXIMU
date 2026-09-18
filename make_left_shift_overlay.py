#!/usr/bin/env python3
"""Build a display-only overlay with the already-aligned UMI path shifted left.

The source HTML and the underlying trajectory files are never modified.  A
positive shift is defined in the source viewer's current camera view
(``yaw=-0.65``) so that it moves the green path toward screen-left.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_html", type=Path)
    parser.add_argument("output_html", type=Path)
    parser.add_argument("--shift-mm", type=float, default=10.0)
    return parser.parse_args()


def stats(robot: list[list[float]], umi: list[list[float]]) -> dict[str, float]:
    errors = []
    for r, u in zip(robot, umi):
        errors.append(math.sqrt(sum((u[k] - r[k]) ** 2 for k in range(3))) * 1000.0)
    errors.sort()
    n = len(errors)
    return {
        "points": n,
        "mean_mm": sum(errors) / n,
        "rmse_mm": math.sqrt(sum(e * e for e in errors) / n),
        "p95_mm": errors[min(n - 1, math.ceil(0.95 * n) - 1)],
        "max_mm": errors[-1],
    }


def main() -> int:
    args = parse_args()
    text = args.input_html.read_text(encoding="utf-8")
    start = text.index("const D=") + len("const D=")
    end = text.index(";function scene", start)
    data = json.loads(text[start:end])

    # Match the existing canvas viewer: x1 = cos(yaw)*X + sin(yaw)*Z.
    yaw = -0.65
    screen_left = [-math.cos(yaw), 0.0, -math.sin(yaw)]
    shift_m = args.shift_mm / 1000.0
    shifted = [
        [point[k] + shift_m * screen_left[k] for k in range(3)]
        for point in data["axis"]
    ]
    data["axis"] = shifted

    result = {
        "source_html": str(args.input_html),
        "display_only": True,
        "shift_definition": "positive shift is screen-left in the source viewer (yaw=-0.65)",
        "shift_mm": args.shift_mm,
        "shift_world_mm": [value * args.shift_mm for value in screen_left],
        "stats": stats(data["robot"], shifted),
    }

    replacement = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    rendered = text[:start] + replacement + text[end:]
    rendered = rendered.replace(
        "<h1>Docker2：整体平移叠加诊断</h1>",
        f"<h1>Docker2：绿色整体向屏幕左平移 {args.shift_mm:.1f} mm 诊断</h1>",
    )
    rendered = rendered.replace(
        "<p>原始轨迹未修改；每个面板只对 UMI 施加一个恒定平移向量，无逐帧校正、无终点约束。</p>",
        "<p>原始轨迹未修改；右图仅将绿色 UMI 轨迹整体向屏幕左平移一次，无逐帧校正、无终点约束。</p>",
    )
    rendered = re.sub(
        r"固定轴向 \+ 只平移：RMSE [0-9.]+ mm",
        f"固定轴向 + 屏幕左平移 {args.shift_mm:.1f}mm：RMSE {result['stats']['rmse_mm']:.2f} mm",
        rendered,
        count=1,
    )
    args.output_html.parent.mkdir(parents=True, exist_ok=True)
    args.output_html.write_text(rendered, encoding="utf-8")
    args.output_html.with_suffix(".json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
