from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


BASE = Path(__file__).resolve().parents[1]
RUNNER = BASE / "probe_fixed_stereo_scales.py"


def _load_runner(monkeypatch):
    monkeypatch.syspath_prepend(str(BASE))
    spec = importlib.util.spec_from_file_location("probe_fixed_stereo_scales", RUNNER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _prior(tmp_path: Path, job: dict, report_job: dict | None = None, report_sha: str | None = None) -> Path:
    prior = tmp_path / "prior"
    report = {
        "job": report_job if report_job is not None else job,
        "conditioning": {"frames": []},
        "images": {},
        "stereo_source": {},
        "native_backend_sha256": "unused-before-cuda",
    }
    report_path = prior / job["id"] / "report.json"
    _write_json(report_path, report)
    _write_json(prior / "summary.json", {"jobs": [{"id": job["id"], "report_sha256": report_sha or _sha(report_path)}]})
    return prior


def test_run_job_rejects_tampered_prior_report_hash_before_cuda(monkeypatch, tmp_path):
    runner = _load_runner(monkeypatch)
    monkeypatch.setattr(runner, "validate_job_bindings", lambda job: None)
    job = {"id": "case_a", "graph": str(tmp_path / "graph.pt")}
    prior = _prior(tmp_path, job, report_sha="0" * 64)

    with pytest.raises(ValueError, match="prior stereo report hash mismatch"):
        runner.run_job(job, prior, tmp_path / "out", backend=object())


def test_run_job_rejects_job_binding_mismatch_before_cuda(monkeypatch, tmp_path):
    runner = _load_runner(monkeypatch)
    monkeypatch.setattr(runner, "validate_job_bindings", lambda job: None)
    job = {"id": "case_b", "graph": str(tmp_path / "graph.pt")}
    prior = _prior(tmp_path, job, report_job={"id": "case_b", "graph": "different.pt"})

    with pytest.raises(ValueError, match="prior stereo report source mismatch"):
        runner.run_job(job, prior, tmp_path / "out", backend=object())


def test_main_rejects_gt_plan_before_build(monkeypatch, tmp_path):
    runner = _load_runner(monkeypatch)
    called = {"build": False}

    def forbidden_build():
        called["build"] = True
        raise AssertionError("build_extension must not be called")

    plan = tmp_path / "plan.json"
    _write_json(plan, {"diagnostic_only": True, "external_ground_truth_used": True, "jobs": []})
    monkeypatch.setattr(runner, "build_extension", forbidden_build)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "probe_fixed_stereo_scales.py",
            "--plan",
            str(plan),
            "--prior-trial",
            str(tmp_path / "prior"),
            "--output",
            str(tmp_path / "out"),
        ],
    )

    with pytest.raises(ValueError, match="source-only diagnostic plan required"):
        runner.main()
    assert called["build"] is False
