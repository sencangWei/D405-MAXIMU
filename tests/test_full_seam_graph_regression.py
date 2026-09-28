import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".planning" / "metric_window_bundle_20260928" / "run_full_seam_graph_regression.py"
spec = importlib.util.spec_from_file_location("run_full_seam_graph_regression", SCRIPT)
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)


CASE_NAMES = ["fresh1", "dev1", "dev2", "heldout1", "heldout2", "heldout3", "heldout4", "fresh2", "fresh3", "fresh4"]


def sha(path: Path) -> str:
    return runner.digest(path)


def write_csv(path: Path, times=(0.0, 0.1, 0.2)):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["t_sec", "x"])
        writer.writeheader()
        for t in times:
            writer.writerow({"t_sec": f"{t:.9f}", "x": "0"})


def rows():
    out = []
    for pair in range(29):
        for part in range(2):
            start = 40 * pair + 20 * part
            out.append({
                "window": 2 * pair + part + 1,
                "joint_pair": pair + 1,
                "indices": [start, start + 5, start + 10, start + 15, start + 20],
                "accepted": True,
                "reason": "ok",
                "endpoint_m": [0.1, 0.0, 0.0],
            })
    return out


def make_baseline(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    source = root.parent / "native.py"
    source.write_text("native")
    stereo = root / "stereo.json"
    stereo.write_text("{}")
    extra = root / "extra.json"
    extra.write_text("{}")
    for case in CASE_NAMES:
        case_dir = root / case
        case_dir.mkdir(parents=True)
        trajectory = case_dir / "trajectory.csv"
        write_csv(trajectory)
        graph_out = case_dir / "graph.csv"
        graph_cmd = ["python", str(source), "--trajectory", str(trajectory), "--stereo-report", str(stereo), "--output", str(graph_out)]
        manifest = {
            "source_sha256": {str(source.relative_to(root.parent)): sha(source)},
            "input_sha256": {str(extra): sha(extra), str(trajectory): sha(trajectory)},
            "commands": [
                ("graph", graph_cmd),
                ("body", ["python", "body", "--out", str(case_dir / "body.json")]),
                ("quality", ["python", "quality", "--out", str(case_dir / "quality.json")]),
                ("score", ["python", "score", "--out", str(case_dir / "score.json")]),
            ],
        }
        (case_dir / "manifest.json").write_text(json.dumps(manifest) + "\n")
        (case_dir / "validated_graph_command.json").write_text(json.dumps({"command": graph_cmd}) + "\n")
    return source, stereo, extra


def write_controls(path: Path, payload: Path, *, independent: bool, shifted=False):
    windows = rows()
    if shifted:
        windows[3]["accepted"] = False
        windows[3]["indices"] = [1, 2, 3, 4, 5]
    summary = {
        "external_reference_used": False,
        "source_sha256": {str(payload): sha(payload)},
        "full_seam_adapter": {
            "raw_interval_frames": 40,
            "independent_summary": independent,
            "correlated_paired_endpoints": True,
            "calibrated_covariance": False,
            "case_counts": [
                {
                    "case": case,
                    "pair_count": 29,
                    "recording_raw_frame_count": 1200 if case == "dev2" else 1199,
                    "last_endpoint_index": 1160,
                    "uncovered_tail_frames_after_last_endpoint": 39 if case == "dev2" else 38,
                }
                for case in CASE_NAMES
            ],
        },
        "cases": [
            {
                "case": case,
                "input_sha256": {str(payload): sha(payload)},
                "decoded_grayscale_frame_sha256": {"frame0": sha(payload)},
                "windows": [dict(row) for row in windows],
            }
            for case in CASE_NAMES
        ],
    }
    path.write_text(json.dumps(summary, indent=2) + "\n")


def install_environment(tmp_path, monkeypatch):
    baseline = tmp_path / "baseline"
    wrapper = tmp_path / "wrapper.py"
    wrapper.write_text("wrapper")
    source, stereo, extra = make_baseline(baseline)
    controls_payload = tmp_path / "controls_payload.txt"
    controls_payload.write_text("payload")
    joint_controls = tmp_path / "joint.json"
    indep_controls = tmp_path / "indep.json"
    write_controls(joint_controls, controls_payload, independent=False)
    write_controls(indep_controls, controls_payload, independent=True)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "BASELINE", baseline)
    monkeypatch.setattr(runner, "WRAPPER", wrapper)
    monkeypatch.setattr(runner, "CASES", CASE_NAMES)
    return baseline, wrapper, joint_controls, indep_controls, controls_payload


def test_variant_graph_command_injects_wrapper_only_for_seam_modes(tmp_path):
    old = tmp_path / "old"
    new = tmp_path / "new"
    command = [
        "python", str(old / "native.py"),
        "--trajectory", str(old / "trajectory.csv"),
        "--stereo-report", str(old / "stereo.json"),
        "--output", str(old / "graph.csv"),
    ]
    controls = tmp_path / "summary.json"

    baseline = runner.graph_command(command, old, new, "baseline", None, "fresh1")
    joint = runner.graph_command(command, old, new, "joint", controls, "fresh1")

    assert baseline[1] == str(new / "native.py")
    assert baseline[baseline.index("--trajectory") + 1] == str(old / "trajectory.csv")
    assert baseline[baseline.index("--stereo-report") + 1] == str(old / "stereo.json")
    assert baseline[-1] == str(new / "graph.csv")
    assert joint[1] == str(runner.WRAPPER)
    assert joint[-6:] == [
        "--seam-window-controls", str(controls),
        "--seam-window-case", "fresh1",
        "--seam-window-mode", "joint",
    ]
    downstream = runner.remap_command(
        ["python", str(old / "body.py"), "--trajectory", str(old / "graph.csv"), "--out", str(old / "body.json")],
        old,
        new,
    )
    assert downstream[downstream.index("--trajectory") + 1] == str(new / "graph.csv")
    assert downstream[-1] == str(new / "body.json")


def test_main_runs_all_graphs_before_downstream_and_records_rc3(tmp_path, monkeypatch):
    _, _, joint_controls, indep_controls, _ = install_environment(tmp_path, monkeypatch)
    calls = []

    def fake_run(command, cwd, log_path):
        calls.append((log_path.parent.parent.name, log_path.parent.name, log_path.stem))
        log_path.write_text("log")
        if log_path.stem == "graph":
            source = Path(command[command.index("--trajectory") + 1])
            out = Path(command[command.index("--output") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(source.read_text())
        return 3 if log_path.stem == "score" else 0

    monkeypatch.setattr(runner, "run_command", fake_run)

    assert runner.main(["--output", str(tmp_path / "out"), "--joint-controls", str(joint_controls), "--independent-controls", str(indep_controls)]) == 0

    graph_calls = [call for call in calls if call[2] == "graph"]
    non_graph_calls = [call for call in calls if call[2] != "graph"]
    assert len(graph_calls) == 30
    assert len(non_graph_calls) == 90
    assert calls[:30] == graph_calls
    status = json.loads((tmp_path / "out" / "batch_status.json").read_text())
    assert status["gt_scoring_started_after_all_graphs"] is True
    assert len(status["cases"]) == 30
    assert any(stage["returncode"] == 3 for case in status["cases"] for stage in case["stages"])


def test_preflight_changed_hash_prevents_output_and_subprocess(tmp_path, monkeypatch):
    _, _, joint_controls, indep_controls, payload = install_environment(tmp_path, monkeypatch)
    payload.write_text("changed")
    monkeypatch.setattr(runner, "run_command", lambda *args, **kwargs: pytest.fail("subprocess should not run"))

    with pytest.raises(ValueError, match="digest invalid|Frozen"):
        runner.main(["--output", str(tmp_path / "out"), "--joint-controls", str(joint_controls), "--independent-controls", str(indep_controls)])
    assert not (tmp_path / "out").exists()


def test_control_underlying_hash_rechecked_from_each_entry_before_graph(tmp_path, monkeypatch):
    _, _, joint_controls, indep_controls, payload = install_environment(tmp_path, monkeypatch)
    original_load_entries = runner.load_entries

    def stale_entries(joint, indep):
        entries = original_load_entries(joint, indep)
        payload.write_text("changed after entry prep")
        return entries

    monkeypatch.setattr(runner, "load_entries", stale_entries)
    monkeypatch.setattr(runner, "run_command", lambda *args, **kwargs: pytest.fail("subprocess should not run"))

    with pytest.raises(ValueError, match="Frozen"):
        runner.main(["--output", str(tmp_path / "out"), "--joint-controls", str(joint_controls), "--independent-controls", str(indep_controls)])
    assert not (tmp_path / "out").exists()


def test_rejects_shifted_refused_schedule_and_hash_conflict(tmp_path, monkeypatch):
    baseline, _, joint_controls, indep_controls, payload = install_environment(tmp_path, monkeypatch)
    write_controls(joint_controls, payload, independent=False, shifted=True)
    with pytest.raises(ValueError, match="schedule"):
        runner.load_entries(joint_controls, indep_controls)

    write_controls(joint_controls, payload, independent=False)
    first_manifest = baseline / "fresh1" / "manifest.json"
    manifest = json.loads(first_manifest.read_text())
    graph = json.loads((baseline / "fresh1" / "validated_graph_command.json").read_text())["command"]
    stereo = graph[graph.index("--stereo-report") + 1]
    manifest["input_sha256"][stereo] = "0" * 64
    first_manifest.write_text(json.dumps(manifest) + "\n")
    with pytest.raises(ValueError, match="hash conflict"):
        runner.load_entries(joint_controls, indep_controls)


def test_graph_timestamp_mismatch_is_rejected(tmp_path, monkeypatch):
    _, _, joint_controls, indep_controls, _ = install_environment(tmp_path, monkeypatch)

    def fake_run(command, cwd, log_path):
        log_path.write_text("log")
        if log_path.stem == "graph":
            out = Path(command[command.index("--output") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            write_csv(out, times=(0.0, 9.0))
        return 0

    monkeypatch.setattr(runner, "run_command", fake_run)
    with pytest.raises(ValueError, match="rows/timestamps"):
        runner.main(["--output", str(tmp_path / "out"), "--joint-controls", str(joint_controls), "--independent-controls", str(indep_controls)])
