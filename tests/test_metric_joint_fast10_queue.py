from __future__ import annotations

import json
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("run_metric_joint_fast10", ROOT / "scripts/run_metric_joint_fast10.py")
q = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(q)


FAST10_ORDER = [
    "20260927_ind2",
    "20260929_take02",
    "20260929_take04",
    "20260929_take07",
    "20260930_take06",
    "20260927_heldout2",
    "20260927_heldout4",
    "20260929_take01",
    "20260930_take03",
    "20260930_take04",
]


def test_phase_order_is_failures_then_fixed_passing_controls():
    _manifest, order = q.load_queue(q.FAST10, None)
    assert order == FAST10_ORDER


def test_right_dataset_resolution_includes_0930_special_paths():
    full = q.read_json(Path("config/dual_ir_regression_25_20261002.json"))
    by_id = {row["id"]: row for row in full["records"]}

    assert q.datasets(by_id["20260930_take04"])[1] == (
        q.ROOT / ".planning/frontend_observation_20260930/dual_ir_same_code_20261002/right_take4/dataset"
    )
    assert q.datasets(by_id["20260930_take06"])[1] == (
        q.ROOT / ".planning/frontend_observation_20260930/dual_ir_same_code_20261002/right_take6/dataset"
    )
    assert q.datasets(by_id["20260929_take02"])[1] == (
        q.ROOT / ".planning/dual_ir_regression_25_20261002/batch_v1/20260929_take02/right_cache/dataset"
    )


def _patch_safe_preflight(monkeypatch):
    monkeypatch.setattr(q, "freeze_config", lambda _p: {})
    monkeypatch.setattr(q, "complete_source", lambda *_a, **_k: ({}, 1199))


def test_failed_frontend_is_preserved_and_queue_continues(monkeypatch, tmp_path):
    _patch_safe_preflight(monkeypatch)
    calls: list[str] = []

    def fake_run_step(command, log, execute):
        calls.append(" ".join(map(str, command)))
        if "evaluate_metric_joint_frontend.py" in calls[-1]:
            out = Path(command[command.index("--output") + 1])
            out.mkdir(parents=True)
            (out / "summary.json").write_text(json.dumps({"status": "COMPLETED"}) + "\n")
        return 0

    def fake_terminal(path, eye, record):
        if record["id"] == "20260929_take02" and eye == "left":
            return "FRONTEND_FAILED"
        return q.FRONTEND_READY_STATUS

    monkeypatch.setattr(q, "run_step", fake_run_step)
    monkeypatch.setattr(q, "terminal_status", fake_terminal)

    out = tmp_path / "queue"
    q.main(["--run", "--output-root", str(out), "--record-id", "20260929_take02", "--record-id", "20260929_take04"])
    summary = json.loads((out / "summary.json").read_text())

    first, second = summary["records"]
    assert first["id"] == "20260929_take02"
    assert first["status"] == "COVERAGE_FAILED"
    assert first["stages"][1]["status"] == "SKIPPED_AFTER_PRIOR_FRONTEND_FAILURE"
    assert second["id"] == "20260929_take04"
    assert second["status"] == "COMPLETED"
    assert any("20260929_take04" in call for call in calls)


def test_running_reuse_refuses_before_creating_output(monkeypatch, tmp_path):
    _patch_safe_preflight(monkeypatch)
    reuse = tmp_path / "reuse/20260929_take02/left"
    reuse.mkdir(parents=True)
    (reuse / "run_manifest.json").write_text(json.dumps({"status": "RUNNING"}) + "\n")
    out = tmp_path / "new-output"

    with pytest.raises(RuntimeError, match="RUNNING"):
        q.main(["--run", "--output-root", str(out), "--reuse-root", str(tmp_path / "reuse"), "--record-id", "20260929_take02"])
    assert not out.exists()


def test_existing_output_root_is_rejected(tmp_path):
    out = tmp_path / "exists"
    out.mkdir()
    with pytest.raises(FileExistsError):
        q.main(["--output-root", str(out), "--record-id", "20260929_take02"])


def test_runner_returning_zero_without_terminal_artifacts_is_not_accepted(monkeypatch, tmp_path):
    _patch_safe_preflight(monkeypatch)
    monkeypatch.setattr(q, "run_step", lambda *_a, **_k: 0)

    q.main(["--run", "--output-root", str(tmp_path / "queue"), "--record-id", "20260929_take02", "--record-id", "20260929_take04"])
    rows = json.loads((tmp_path / "queue/summary.json").read_text())["records"]
    assert [row["id"] for row in rows] == ["20260929_take02", "20260929_take04"]
    assert [row["status"] for row in rows] == ["FRONTEND_INVALID", "FRONTEND_INVALID"]


def test_missing_evaluator_summary_marks_failed_and_continues(monkeypatch, tmp_path):
    _patch_safe_preflight(monkeypatch)
    evals = 0

    def fake_run_step(command, log, execute):
        nonlocal evals
        if "evaluate_metric_joint_frontend.py" in " ".join(map(str, command)):
            evals += 1
            if evals == 2:
                out = Path(command[command.index("--output") + 1])
                out.mkdir(parents=True)
                (out / "summary.json").write_text(json.dumps({"status": "COMPLETED"}) + "\n")
        return 0

    monkeypatch.setattr(q, "run_step", fake_run_step)
    monkeypatch.setattr(q, "terminal_status", lambda *_a, **_k: q.FRONTEND_READY_STATUS)
    q.main(["--run", "--output-root", str(tmp_path / "queue"), "--record-id", "20260929_take02", "--record-id", "20260929_take04"])
    rows = json.loads((tmp_path / "queue/summary.json").read_text())["records"]
    assert rows[0]["status"] == "EVALUATION_FAILED"
    assert rows[1]["status"] == "COMPLETED"


def test_known_reused_right_failure_skips_fresh_left(monkeypatch, tmp_path):
    _patch_safe_preflight(monkeypatch)
    reuse = tmp_path / "reuse/20260929_take02/right"
    reuse.mkdir(parents=True)
    (reuse / "run_manifest.json").write_text("{}\n")
    launched = []

    def fake_validate(path, eye, record, source_config, checkpoint):
        assert eye == "right"
        return "FRONTEND_FAILED"

    monkeypatch.setattr(q, "validate_reuse", fake_validate)
    monkeypatch.setattr(q, "run_step", lambda command, *_a: launched.append(command) or 0)
    q.main(["--run", "--output-root", str(tmp_path / "queue"), "--reuse-root", str(tmp_path / "reuse"), "--record-id", "20260929_take02"])
    row = json.loads((tmp_path / "queue/summary.json").read_text())["records"][0]
    assert row["status"] == "COVERAGE_FAILED"
    assert row["stages"][0]["status"] == "SKIPPED_AFTER_KNOWN_REUSED_FAILURE"
    assert row["stages"][1]["status"] == "FRONTEND_FAILED"
    assert launched == []


def test_guard_change_aborts_before_next_producer(monkeypatch, tmp_path):
    _patch_safe_preflight(monkeypatch)
    monkeypatch.setattr(q, "guard_changed", lambda _before: True)
    launched = []
    monkeypatch.setattr(q, "run_step", lambda command, *_a: launched.append(command) or 0)

    q.main(["--run", "--output-root", str(tmp_path / "queue"), "--record-id", "20260929_take02"])
    summary = json.loads((tmp_path / "queue/summary.json").read_text())
    assert summary["status"] == "QUEUE_ERROR_SOURCE_CHANGED"
    assert summary["records"][0]["status"] == "QUEUE_ERROR_SOURCE_CHANGED"
    assert launched == []


def test_real_guard_includes_objective_file_and_aborts_before_next_producer(monkeypatch, tmp_path):
    _patch_safe_preflight(monkeypatch)
    objective = tmp_path / "objective.py"; checkpoint = tmp_path / "checkpoint.pth"
    objective.write_text("v1\n"); checkpoint.write_text("weights\n")
    monkeypatch.setattr(q, "CODE_PATHS", {"objective": objective})
    launched: list[str] = []

    def fake_run_step(command, log, execute):
        launched.append(" ".join(map(str, command)))
        if len(launched) == 1:
            objective.write_text("v2\n")
        out = Path(command[command.index("--output") + 1])
        out.mkdir(parents=True)
        (out / "summary.json").write_text(json.dumps({"status": "COMPLETED"}) + "\n")
        return 0

    monkeypatch.setattr(q, "run_step", fake_run_step)
    monkeypatch.setattr(q, "terminal_status", lambda *_a, **_k: q.FRONTEND_READY_STATUS)
    q.main(["--run", "--output-root", str(tmp_path / "queue"), "--checkpoint", str(checkpoint), "--record-id", "20260929_take02"])
    summary = json.loads((tmp_path / "queue/summary.json").read_text())
    assert summary["status"] == "QUEUE_ERROR_SOURCE_CHANGED"
    assert len(launched) == 1
    assert summary["records"][0]["stages"][-1]["status"] == "QUEUE_ABORTED_SOURCE_CHANGED"


def test_real_guard_includes_checkpoint_and_aborts_before_next_producer(monkeypatch, tmp_path):
    _patch_safe_preflight(monkeypatch)
    objective = tmp_path / "objective.py"; checkpoint = tmp_path / "checkpoint.pth"
    objective.write_text("v1\n"); checkpoint.write_text("weights\n")
    monkeypatch.setattr(q, "CODE_PATHS", {"objective": objective})
    launched: list[str] = []

    def fake_run_step(command, log, execute):
        launched.append(" ".join(map(str, command)))
        if len(launched) == 1:
            checkpoint.write_text("new weights\n")
        out = Path(command[command.index("--output") + 1])
        out.mkdir(parents=True)
        (out / "summary.json").write_text(json.dumps({"status": "COMPLETED"}) + "\n")
        return 0

    monkeypatch.setattr(q, "run_step", fake_run_step)
    monkeypatch.setattr(q, "terminal_status", lambda *_a, **_k: q.FRONTEND_READY_STATUS)
    q.main(["--run", "--output-root", str(tmp_path / "queue"), "--checkpoint", str(checkpoint), "--record-id", "20260929_take02"])
    summary = json.loads((tmp_path / "queue/summary.json").read_text())
    assert summary["status"] == "QUEUE_ERROR_SOURCE_CHANGED"
    assert len(launched) == 1


def test_failed_reuse_must_match_record_dataset_and_code(monkeypatch, tmp_path):
    source_config = tmp_path / "source.yaml"; checkpoint = tmp_path / "checkpoint.pth"
    source_config.write_text("a: 1\n"); checkpoint.write_text("weights")
    run = tmp_path / "reuse"; run.mkdir()
    code = "codehash"
    manifest = {
        "status": "FRONTEND_FAILED", "eye": "right", "source_session": "/session",
        "input_frame_count": 1199, "source_config_sha256": q.sha(source_config),
        "checkpoint_sha256": q.sha(checkpoint), "runner_sha256": q.sha(q.ROOT / "scripts/run_experimental_metric_joint_frontend.py"),
        "code_sha256": "other-code",
    }
    context = {"dataset": "/expected/right", "paired_left_dataset": "/expected/left", "code_sha256": code}
    (run / "run_manifest.json").write_text(json.dumps(manifest) + "\n")
    (run / "context.json").write_text(json.dumps(context) + "\n")
    monkeypatch.setattr(q, "datasets", lambda _record: (Path("/expected/left"), Path("/expected/right")))
    monkeypatch.setattr(q, "complete_source", lambda *_a, **_k: ({"source_session": "/session"}, 1199))
    monkeypatch.setattr(q, "context_for_source", lambda *_a, **_k: context)

    with pytest.raises(ValueError, match="context/code binding mismatch"):
        q.validate_reuse(run, "right", {"session": "/session"}, source_config, checkpoint)


def test_reuse_source_config_hash_mismatch_refuses(monkeypatch, tmp_path):
    source_config = tmp_path / "source.yaml"
    checkpoint = tmp_path / "checkpoint.pth"
    source_config.write_text("a: 1\n")
    checkpoint.write_text("weights")
    run = tmp_path / "reuse"
    run.mkdir()
    (run / "run_manifest.json").write_text(json.dumps({
        "status": q.FRONTEND_READY_STATUS,
        "source_config_sha256": "wrong",
        "checkpoint_sha256": q.sha(checkpoint),
        "runner_sha256": q.sha(q.ROOT / "scripts/run_experimental_metric_joint_frontend.py"),
    }) + "\n")

    with pytest.raises(ValueError, match="source config hash mismatch"):
        q.validate_reuse(run, "left", {"session": "/"}, source_config, checkpoint)


def test_summary_flags_and_commands_do_not_enable_gt_or_mutate_thresholds(monkeypatch, tmp_path, capsys):
    _patch_safe_preflight(monkeypatch)
    q.main(["--output-root", str(tmp_path / "dry"), "--record-id", "20260929_take02"])
    summary = json.loads(capsys.readouterr().out)
    text = json.dumps(summary)

    assert summary["status"] == "DRY_RUN_COMPLETE"
    assert summary["development_only"] is True
    assert summary["production_promoted"] is False
    assert summary["not_full25"] is True
    assert summary["external_ground_truth_used_by_solver"] is False
    assert "--max-ate" not in text
    assert "--cap" not in text
    assert "ground-truth" not in text.lower()
