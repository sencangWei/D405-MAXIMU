import json
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import audit_independent_ir_solver_convergence as audit  # noqa: E402


def test_capture_returns_exact_original_lsqr_result_and_reaches_physical_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    original_binding = audit.fusion.lsqr
    sentinel = (np.array([1.0, 2.0]), 1, 7, 0.1, 0.2, 3.0, 4.0, 0.5, 6.0, None)

    def fake_lsqr(matrix, rhs, *args, **kwargs):
        return sentinel

    monkeypatch.setattr(audit.fusion, "lsqr", fake_lsqr)
    with audit.capture_fusion_lsqr() as calls:
        result = audit.physical.fusion.lsqr(np.eye(2), np.ones(2), atol=1e-10, btol=1e-10, iter_lim=5)

    assert result is sentinel
    assert audit.fusion.lsqr is fake_lsqr
    assert audit.physical.fusion is audit.fusion
    assert calls == [
        {
            "matrix_shape": [2, 2],
            "rhs_shape": [2],
            "atol": 1e-10,
            "btol": 1e-10,
            "iter_lim": 5,
            "result_type": "tuple",
            "result_length": 10,
            "unknown_result_layout": False,
            "nonfinite_fields": [],
            "istop": 1,
            "itn": 7,
            "r1norm": 0.1,
            "r2norm": 0.2,
            "anorm": 3.0,
            "acond": 4.0,
            "arnorm": 0.5,
            "xnorm": 6.0,
        }
    ]
    monkeypatch.setattr(audit.fusion, "lsqr", original_binding)


def test_capture_restores_original_binding_after_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    original = audit.fusion.lsqr

    def fake_lsqr(matrix, rhs, *args, **kwargs):
        return ()

    monkeypatch.setattr(audit.fusion, "lsqr", fake_lsqr)
    with pytest.raises(RuntimeError, match="inside"):
        with audit.capture_fusion_lsqr():
            raise RuntimeError("inside")

    assert audit.fusion.lsqr is fake_lsqr
    monkeypatch.setattr(audit.fusion, "lsqr", original)


def test_run_with_telemetry_passes_consumer_args_and_records_no_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def fake_main(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(audit.consumer, "main", fake_main)
    out = tmp_path / "telemetry.json"

    assert audit.run_with_telemetry(["--manifest", "m.json", "--dataset", "rec"], out) == 0

    report = json.loads(out.read_text())
    assert seen["argv"] == ["--manifest", "m.json", "--dataset", "rec"]
    assert report["status"] == "NO_LSQR_CALLS"
    assert report["call_count"] == 0
    assert report["canonical_lsqr_binding_reached_through_physical_runner"] is True
    assert report["no_convergence_or_rank_guarantee"] is True


def test_run_with_telemetry_restores_binding_and_writes_exception_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = audit.fusion.lsqr

    def fake_main(argv):
        raise ValueError("bad input")

    monkeypatch.setattr(audit.consumer, "main", fake_main)
    out = tmp_path / "telemetry.json"

    with pytest.raises(ValueError, match="bad input"):
        audit.run_with_telemetry([], out)

    assert audit.fusion.lsqr is original
    report = json.loads(out.read_text())
    assert report["status"] == "CONSUMER_EXCEPTION_RECORDED"
    assert report["consumer_exception"] == "ValueError: bad input"


def test_nonfinite_lsqr_values_are_serialized_as_evidence_not_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = (np.array([1.0]), 2, 3, np.nan, np.inf, 5.0, 6.0, -np.inf, 8.0, None)

    def fake_lsqr(matrix, rhs, *args, **kwargs):
        return result

    def fake_main(argv):
        audit.physical.fusion.lsqr(np.eye(1), np.ones(1))
        return 0

    monkeypatch.setattr(audit.fusion, "lsqr", fake_lsqr)
    monkeypatch.setattr(audit.consumer, "main", fake_main)
    out = tmp_path / "telemetry.json"

    audit.run_with_telemetry([], out)

    report = json.loads(out.read_text())
    assert report["status"] == "RECORDED_WITH_NONFINITE_VALUES"
    assert report["calls"][0]["nonfinite_fields"] == ["r1norm", "r2norm", "arnorm"]
    assert report["calls"][0]["r1norm"] is None
    assert "converged" not in report["calls"][0]


def test_refuses_to_overwrite_existing_telemetry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "telemetry.json"
    out.write_text("already here")

    def fake_main(argv):
        raise AssertionError("consumer must not run when telemetry exists")

    monkeypatch.setattr(audit.consumer, "main", fake_main)
    with pytest.raises(FileExistsError):
        audit.run_with_telemetry([], out)


def test_writer_does_not_overwrite_file_created_after_precheck(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "telemetry.json"
    original_mkdir = Path.mkdir

    def mkdir_with_competing_writer(path, *args, **kwargs):
        original_mkdir(path, *args, **kwargs)
        out.write_text("competing writer")

    monkeypatch.setattr(Path, "mkdir", mkdir_with_competing_writer)
    with pytest.raises(FileExistsError):
        audit.write_json_new(out, {"status": "RECORDED"})
    assert out.read_text() == "competing writer"


def test_calls_are_bound_to_actual_record_and_arm_without_changing_results(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = (np.array([1.0]), 2, 3, 0.1, 0.2, 5.0, 6.0, 0.5, 8.0, None)
    arm_result = object()
    seen = []

    def fake_lsqr(matrix, rhs, *args, **kwargs):
        return sentinel

    def fake_arm(*args, **kwargs):
        seen.append((args, kwargs))
        for _ in range(2):
            assert audit.physical.fusion.lsqr(np.eye(1), np.ones(1)) is sentinel
        return arm_result

    monkeypatch.setattr(audit.fusion, "lsqr", fake_lsqr)
    monkeypatch.setattr(audit.physical, "run_solver_variant", fake_arm)
    first_args = ({"id": "take1"}, Path("baseline"), "control", Path("out1"))
    second_kwargs = {"record": {"id": "take2"}, "variant": "native"}

    with audit.capture_fusion_lsqr() as calls:
        assert audit.physical.run_solver_variant(*first_args) is arm_result
        assert audit.physical.run_solver_variant(**second_kwargs) is arm_result

    assert seen == [(first_args, {}), ((), second_kwargs)]
    assert [(call["record_id"], call["variant"], call["lsqr_call_in_arm"]) for call in calls] == [
        ("take1", "control", 1), ("take1", "control", 2),
        ("take2", "native", 1), ("take2", "native", 2),
    ]
    assert audit.physical.run_solver_variant is fake_arm
    assert audit.fusion.lsqr is fake_lsqr


def test_arm_context_and_bindings_are_restored_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = (np.array([1.0]), 2, 3, 0.1, 0.2, 5.0, 6.0, 0.5, 8.0, None)

    def fake_lsqr(matrix, rhs, *args, **kwargs):
        return sentinel

    def failing_arm(*args, **kwargs):
        audit.fusion.lsqr(np.eye(1), np.ones(1))
        raise ValueError("arm failed")

    monkeypatch.setattr(audit.fusion, "lsqr", fake_lsqr)
    monkeypatch.setattr(audit.physical, "run_solver_variant", failing_arm)
    with audit.capture_fusion_lsqr() as calls:
        with pytest.raises(ValueError, match="arm failed"):
            audit.physical.run_solver_variant({"id": "take1"}, Path("baseline"), "native")
        audit.fusion.lsqr(np.eye(1), np.ones(1))
    assert calls[0]["record_id"] == "take1"
    assert "record_id" not in calls[1]
    assert audit.physical.run_solver_variant is failing_arm
    assert audit.fusion.lsqr is fake_lsqr
