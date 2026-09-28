"""Research batch command invariants and no-GT-before-all-estimates ordering."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "full_shape_batch", ROOT / ".planning/metric_window_bundle_20260928/run_full_shape_graph_regression.py")
BATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BATCH)


@pytest.mark.parametrize("variant", BATCH.VARIANTS)
def test_commands_preserve_formal_inputs_and_downstream(variant, tmp_path):
    old, target = tmp_path / "old", tmp_path / "new"
    graph = ["python", "native.py", "--trajectory", str(old / "frozen.csv"),
             "--stereo-report", str(old / "stereo.json"),
             "--expected-td-s", "-0.009109323", "--joint-max-correction-mm", "25",
             "--output", str(old / "trajectory_graph.csv")]
    stages = [("graph", graph), ("complementary", ["python", "complementary.py", str(old / "trajectory_graph.csv")]),
              ("quality", ["python", "quality.py"]), ("smooth", ["python", "smooth.py"]),
              ("score", ["python", "score.py", str(old / "trajectory_fused.csv")])]
    entry = {"variant": variant, "manifest": {"commands": stages},
             "validated_graph": graph, "old": old, "case": "fresh1"}
    commands = BATCH.commands_for(entry, target, tmp_path / "joint.json", tmp_path / "shape")
    command = commands[0][1]
    assert command[command.index("--trajectory") + 1] == str(old / "frozen.csv")
    assert command[command.index("--stereo-report") + 1] == str(old / "stereo.json")
    assert command[command.index("--expected-td-s") + 1] == "-0.009109323"
    assert command[command.index("--joint-max-correction-mm") + 1] == "25"
    assert command[command.index("--output") + 1] == str(target / "trajectory_graph.csv")
    assert graph == stages[0][1]  # no mutation of frozen commands
    if variant == "baseline":
        assert command[1] == "native.py"
        assert "--shape-window-mode" not in command
    else:
        assert command[1] == str(BATCH.WRAPPER)
        assert command[command.index("--seam-window-mode") + 1] == "joint"
        assert command[command.index("--shape-window-mode") + 1] == variant
    assert commands[1][1][-1] == str(target / "trajectory_graph.csv")


def prepare_fake_batch(monkeypatch, *, failure=None, accuracy_failure=False):
    entries = [{"case": case, "variant": variant, "input_sha256": {"input": "frozen"},
                "source_sha256": {"source": "frozen"}}
               for case in BATCH.seam_graph.CASES for variant in BATCH.VARIANTS]
    monkeypatch.setattr(BATCH, "prepare_entries", lambda *args: entries)
    monkeypatch.setattr(BATCH, "commands_for", lambda entry, target, *args:
                        [(stage, [stage, str(target)]) for stage in BATCH.STAGES])
    monkeypatch.setattr(BATCH.seam_graph, "verify_hashes", lambda hashes: None)
    monkeypatch.setattr(BATCH.seam_graph, "verify_graph_output_times", lambda command: None)
    events = []

    def run(command, cwd, log):
        stage, target = command[0], Path(command[1])
        events.append((stage, target.name, target.parent.name))
        if stage == "score":
            # Every independent estimator attempt, not just first case, ended.
            assert sum(item[0] == "graph" for item in events) == 30
            expected = 30 if failure is None else 29
            assert sum(item[0] == "smooth" for item in events) == expected
        if failure == (target.parent.name, target.name, stage):
            return 1
        if stage == "graph":
            (target / "trajectory_graph.csv").write_text("frozen camera\n")
        if stage == "smooth":
            (target / "trajectory_fused.csv").write_text("frozen body\n")
        return 3 if accuracy_failure and stage == "score" else 0

    monkeypatch.setattr(BATCH.seam_graph, "run_command", run)
    return events


def invoke(tmp_path):
    return BATCH.main(["--output", str(tmp_path / "batch"),
                       "--joint-controls", str(tmp_path / "joint.json"),
                       "--independent-controls", str(tmp_path / "independent.json"),
                       "--shape-controls", str(tmp_path / "shape")])


def test_all_thirty_final_estimates_frozen_before_any_gt_score(tmp_path, monkeypatch):
    events = prepare_fake_batch(monkeypatch)
    assert invoke(tmp_path) == 0
    first_score = next(i for i, event in enumerate(events) if event[0] == "score")
    assert first_score == 120
    status = json.loads((tmp_path / "batch/batch_status.json").read_text())
    assert len(status["cases"]) == 30
    assert all(row["completed"] and row["frozen_estimate_sha256"] for row in status["cases"])
    assert status["gt_scoring_started_after_all_estimation_attempts"] is True


def test_runtime_failure_retained_without_retry_or_silent_success(tmp_path, monkeypatch):
    events = prepare_fake_batch(monkeypatch, failure=("grouped_shape", "fresh1", "graph"))
    assert invoke(tmp_path) == 1
    status = json.loads((tmp_path / "batch/batch_status.json").read_text())
    failed = next(row for row in status["cases"] if row.get("failure_stage"))
    assert failed["failure_stage"] == "graph"
    assert failed["estimation_completed"] is False
    assert sum(stage == "graph" for stage, _, _ in events) == 30
    assert sum(stage == "score" for stage, _, _ in events) == 29


def test_accuracy_failure_exit_three_retained_but_not_runtime_failure(tmp_path, monkeypatch):
    prepare_fake_batch(monkeypatch, accuracy_failure=True)
    assert invoke(tmp_path) == 0
    status = json.loads((tmp_path / "batch/batch_status.json").read_text())
    assert all(row["completed"] and row["stages"][-1]["returncode"] == 3 for row in status["cases"])


def test_refuses_overwrite_before_any_job(tmp_path, monkeypatch):
    (tmp_path / "batch").mkdir()
    monkeypatch.setattr(BATCH, "prepare_entries", lambda *args: pytest.fail("must reject before preflight"))
    with pytest.raises(ValueError, match="overwrite"):
        invoke(tmp_path)
