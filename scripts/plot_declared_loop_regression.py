#!/usr/bin/env python3
"""Plot repeated regression trajectories without aligning or modifying them."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "config/slam_declared_loop_regression.json"


def resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (ROOT / path).resolve()


def read_points(path: Path) -> list[tuple[float, float, float]]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        required = {"x", "y", "z"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f"trajectory lacks x/y/z columns: {path}")
        points = [
            tuple(float(row[axis]) for axis in ("x", "y", "z"))
            for row in reader
        ]
    if len(points) < 2:
        raise ValueError(f"trajectory needs at least two samples: {path}")
    return points


def collect(summary_path: Path, manifest_path: Path) -> list[dict]:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    declared = [
        *manifest.get("datasets", []),
        *manifest.get("safety_controls", []),
    ]
    expected = {item["id"]: item["expected_loop"] for item in declared}
    summary_by_id = {
        item.get("id"): item for item in summary.get("datasets", [])
    }
    unknown_ids = set(summary_by_id) - set(expected)
    if unknown_ids:
        raise ValueError(
            "summary contains unknown dataset(s): " + ", ".join(sorted(unknown_ids))
        )
    datasets = []
    for declaration in declared:
        dataset_id = declaration["id"]
        dataset = summary_by_id.get(dataset_id)
        runs = []
        errors = []
        if dataset is None:
            errors.append("dataset is missing from regression summary")
            dataset = {}
        for run in dataset.get("runs", []):
            report_value = run.get("report")
            if not report_value:
                errors.append(
                    f"run {run.get('repetition')}: run report is missing"
                )
                continue
            report_path = resolve(report_value)
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
                trajectory = report_path.parent / "vio_corrected_stream.csv"
                points = read_points(trajectory)
            except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"run {run.get('repetition')}: {exc}")
                continue
            endpoint_m = sum(
                (points[-1][axis] - points[0][axis]) ** 2 for axis in range(3)
            ) ** 0.5
            runs.append(
                {
                    "repetition": run.get("repetition"),
                    "points": points,
                    "endpoint_m": endpoint_m,
                    "automatic_loop_accepts": int(
                        report.get("automatic_loop_accepts", 0)
                    ),
                    "run_result": report.get("result"),
                }
            )
        datasets.append(
            {
                "id": dataset_id,
                "expected_loop": expected[dataset_id],
                "runs": runs,
                "errors": errors,
            }
        )
    return datasets


def render(datasets: list[dict], output_dir: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    font = font_manager.FontProperties(
        fname="/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    views = ((0, 1, "俯视 X-Y"), (0, 2, "侧视 X-Z"), (1, 2, "侧视 Y-Z"))
    colors = ("tab:blue", "tab:orange", "tab:purple")
    written = []
    index_lines = ["# 回归轨迹三视图索引", "", "轨迹未做对齐或终点修正。", ""]
    for dataset in datasets:
        if not dataset["runs"]:
            index_lines.append(f"- `{dataset['id']}`：无可画轨迹。")
            index_lines.extend(f"  - {error}" for error in dataset["errors"])
            continue
        fig, axes = plt.subplots(1, 3, figsize=(18, 6))
        for run_index, run in enumerate(dataset["runs"]):
            points = run["points"]
            color = colors[run_index % len(colors)]
            label = (
                f"run {run['repetition']}｜{1000 * run['endpoint_m']:.2f}mm｜"
                f"回环{run['automatic_loop_accepts']}｜{run['run_result']}"
            )
            for axis, (first, second, title) in zip(axes, views):
                axis.plot(
                    [point[first] for point in points],
                    [point[second] for point in points],
                    color=color,
                    linewidth=1.2,
                    label=label,
                )
                axis.scatter(
                    points[0][first], points[0][second], color=color, s=35
                )
                axis.scatter(
                    points[-1][first],
                    points[-1][second],
                    color=color,
                    marker="x",
                    s=45,
                )
                axis.set_title(title, fontproperties=font)
                axis.set_xlabel("XYZ"[first] + " (m)")
                axis.set_ylabel("XYZ"[second] + " (m)")
                axis.grid(alpha=0.3)
                axis.axis("equal")
        axes[0].legend(prop=font, fontsize=8)
        fig.suptitle(
            f"{dataset['id']}｜预声明{'闭环' if dataset['expected_loop'] else '非闭环安全样本'}｜"
            "原始世界坐标，无对齐、无终点修正",
            fontproperties=font,
            fontsize=14,
        )
        fig.tight_layout()
        destination = output_dir / f"{dataset['id']}_三视图.png"
        fig.savefig(destination, dpi=170)
        plt.close(fig)
        written.append(destination)
        index_lines.append(f"- `{dataset['id']}`：[{destination.name}]({destination.name})")
        index_lines.extend(f"  - {error}" for error in dataset["errors"])
    (output_dir / "README.md").write_text(
        "\n".join(index_lines) + "\n", encoding="utf-8"
    )
    return written


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("summary", type=Path)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    for path in render(collect(args.summary, args.manifest), args.output_dir):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
