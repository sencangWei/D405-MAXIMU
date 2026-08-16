import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from plot_declared_loop_regression import collect, render


def test_collect_and_render_without_alignment(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    trajectory = run_dir / "vio_corrected_stream.csv"
    with trajectory.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["t_sec", "x", "y", "z"])
        writer.writerow([0.0, 0.0, 0.0, 0.0])
        writer.writerow([1.0, 0.02, 0.01, 0.03])
    report = run_dir / "run_acceptance.json"
    report.write_text(
        json.dumps(
            {"result": "PASS", "automatic_loop_accepts": 1}
        ),
        encoding="utf-8",
    )
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "datasets": [
                    {
                        "id": "closed",
                        "runs": [{"repetition": 1, "report": str(report)}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {"datasets": [{"id": "closed", "expected_loop": True}]}
        ),
        encoding="utf-8",
    )

    datasets = collect(summary, manifest)

    assert datasets[0]["runs"][0]["points"][-1] == (0.02, 0.01, 0.03)
    assert datasets[0]["runs"][0]["endpoint_m"] > 0.037
    images = render(datasets, tmp_path / "plots")
    assert len(images) == 1
    assert images[0].is_file()
    assert images[0].stat().st_size > 1000
    assert "原始世界坐标" not in (tmp_path / "plots/README.md").read_text(
        encoding="utf-8"
    )
    assert "未做对齐" in (tmp_path / "plots/README.md").read_text(
        encoding="utf-8"
    )


def test_missing_trajectory_does_not_prevent_other_plots(tmp_path: Path):
    good = tmp_path / "good"
    bad = tmp_path / "bad"
    good.mkdir()
    bad.mkdir()
    with (good / "vio_corrected_stream.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.writer(stream)
        writer.writerow(["t_sec", "x", "y", "z"])
        writer.writerow([0.0, 0.0, 0.0, 0.0])
        writer.writerow([1.0, 0.01, 0.0, 0.0])
    for directory in (good, bad):
        (directory / "run_acceptance.json").write_text(
            json.dumps({"result": "PASS", "automatic_loop_accepts": 1}),
            encoding="utf-8",
        )
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "datasets": [
                    {
                        "id": "closed",
                        "runs": [
                            {
                                "repetition": 1,
                                "report": str(good / "run_acceptance.json"),
                            },
                            {
                                "repetition": 2,
                                "report": str(bad / "run_acceptance.json"),
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {"datasets": [{"id": "closed", "expected_loop": True}]}
        ),
        encoding="utf-8",
    )

    datasets = collect(summary, manifest)
    images = render(datasets, tmp_path / "plots")

    assert len(images) == 1
    assert "trajectory" in datasets[0]["errors"][0]


def test_missing_declared_dataset_is_visible_in_plot_index(tmp_path: Path):
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps({"datasets": []}), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "datasets": [{"id": "closed", "expected_loop": True}],
                "safety_controls": [
                    {"id": "open_control", "expected_loop": False}
                ],
            }
        ),
        encoding="utf-8",
    )

    datasets = collect(summary, manifest)
    images = render(datasets, tmp_path / "plots")

    assert images == []
    assert len(datasets) == 2
    index = (tmp_path / "plots" / "README.md").read_text(encoding="utf-8")
    assert "closed`：无可画轨迹" in index
    assert "open_control`：无可画轨迹" in index
    assert index.count("dataset is missing from regression summary") == 2
