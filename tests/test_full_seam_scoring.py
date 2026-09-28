import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".planning" / "metric_window_bundle_20260928" / "score_full_seam_controls.py"
spec = importlib.util.spec_from_file_location("score_full_seam_controls", SCRIPT)
score = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = score
spec.loader.exec_module(score)

CASES = ["dev1", "dev2", "heldout1", "heldout2", "heldout3", "heldout4", "fresh1", "fresh2", "fresh3", "fresh4"]


def write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def make_controls(root: Path, *, pairs=29):
    root.mkdir(parents=True, exist_ok=True)
    source = root / "source.py"
    source.write_text("source")
    input_file = root / "input.txt"
    input_file.write_text("input")
    case_counts = [
        {
            "case": name,
            "pair_count": pairs,
            "joint_window_count": 2 * pairs,
            "independent_window_count": 2 * pairs,
            "recording_raw_frame_count": 1200,
            "raw_count_loaded_pairs_prefix": 1161,
            "last_endpoint_index": 1160,
            "uncovered_tail_frames_after_last_endpoint": 39,
        }
        for name in CASES
    ]
    adapter = {
        "raw_interval_frames": 40,
        "case_counts": case_counts,
        "correlated_paired_endpoints": True,
        "calibrated_covariance": False,
        "independent_summary": False,
    }
    cases = []
    for name in CASES:
        windows = []
        for window in range(1, 2 * pairs + 1):
            windows.append({
                "window": window,
                "indices": [0, 5, 10, 15, 20],
                "accepted": window % 5 != 0,
                "reason": "ok" if window % 5 != 0 else "model_consistency_failed",
            })
        cases.append({
            "case": name,
            "windows": windows,
            "input_sha256": {str(input_file): score.sha(input_file)},
            "decoded_grayscale_frame_sha256": {"left:1": "abc"},
        })
    summary = {
        "cases": cases,
        "source_sha256": {str(source): score.sha(source)},
        "external_reference_used": False,
        "full_seam_adapter": adapter,
    }
    controls = root / "controls"
    write_json(controls / "summary.json", summary)
    independent = json.loads(json.dumps(summary))
    independent["full_seam_adapter"]["independent_summary"] = True
    write_json(controls / "independent_summary.json", independent)
    return controls, source, input_file


def fake_eval_from_summary(summary_path: Path, scale: float):
    summary = json.loads(summary_path.read_text())
    cases = []
    for case in summary["cases"]:
        rows = []
        for row in case["windows"]:
            out = {"window": row["window"], "accepted": row["accepted"], "reason": row["reason"]}
            if row["accepted"] and row["window"] % 7 != 0:
                out.update(scored=True, optimized_local_error_mm=scale + row["window"])
            rows.append(out)
        cases.append({"case": case["case"], "windows": rows})
    return {
        "cases": cases,
        "source_sha256": {str(score.OLD_SCORE): score.sha(score.OLD_SCORE)},
        "external_reference_used_in_estimation": False,
        "external_reference_used_in_evaluation": True,
    }


def test_validation_rejects_missing_or_bad_schema_and_digest(tmp_path):
    controls, source, _ = make_controls(tmp_path)
    (controls / "independent_summary.json").unlink()
    with pytest.raises(ValueError, match="both summary"):
        score.main(["--controls", str(controls)])

    controls, source, _ = make_controls(tmp_path / "bad")
    data = json.loads((controls / "summary.json").read_text())
    data["full_seam_adapter"]["raw_interval_frames"] = 20
    write_json(controls / "summary.json", data)
    with pytest.raises(ValueError, match="interval40"):
        score.main(["--controls", str(controls)])

    controls, source, _ = make_controls(tmp_path / "digest")
    source.write_text("changed")
    with pytest.raises(ValueError, match="digest invalid"):
        score.main(["--controls", str(controls)])

    controls, _, _ = make_controls(tmp_path / "missing")
    data = json.loads((controls / "summary.json").read_text())
    data["source_sha256"] = {}
    write_json(controls / "summary.json", data)
    with pytest.raises(ValueError, match="source_sha256"):
        score.main(["--controls", str(controls)])

    controls, _, _ = make_controls(tmp_path / "missinginput")
    data = json.loads((controls / "summary.json").read_text())
    data["cases"][0]["input_sha256"] = {}
    write_json(controls / "summary.json", data)
    with pytest.raises(ValueError, match="input_sha256"):
        score.main(["--controls", str(controls)])


def test_mocked_oldscore_invocation_copy_and_scored_intersection(tmp_path, monkeypatch):
    controls, _, _ = make_controls(tmp_path)
    calls = []

    def fake_run(controls_dir, output):
        calls.append((controls_dir, output))
        scale = 10.0 if controls_dir == controls else 20.0
        write_json(output, fake_eval_from_summary(controls_dir / "summary.json", scale))

    monkeypatch.setattr(score, "run_old_score", fake_run)

    assert score.main(["--controls", str(controls)]) == 0

    assert calls == [
        (controls, controls / "local_joint_evaluation.json"),
        (controls / "independent_view", controls / "local_independent_evaluation.json"),
    ]
    assert (controls / "independent_view" / "summary.json").read_bytes() == (controls / "independent_summary.json").read_bytes()
    report = json.loads((controls / "full_seam_local_score_summary.json").read_text())
    assert report["coverage"]["cases"] == 10
    assert report["coverage"]["mutually_scored"] > 0
    assert report["coverage"]["joint_refusals"] > 0
    assert report["coverage"]["joint_unscored_endpoints"] > 0
    assert report["coverage"]["joint_accepted_but_unscored"] > 0
    assert report["coverage"]["independent_accepted_but_unscored"] > 0
    assert report["coverage"]["pair_count"] == 290
    assert report["adapter_tail_frames"] == {case: 39 for case in CASES}
    assert report["cases"][0]["accepted_joint_pairs"] > 0
    assert report["cases"][0]["endpoint_reason_counts"]["model_consistency_failed"] > 0
    assert report["paired_joint"]["count"] == report["paired_independent"]["count"]
    assert report["paired_improved"] == report["paired_joint"]["count"]
    assert "NOT ATE" in report["warning"]
    assert "correlated" in report["warning"]
    assert "calibrated covariance" in report["warning"]
    assert str(SCRIPT.resolve()) in report["source_sha256"]
    assert str(score.OLD_SCORE.resolve()) in report["source_sha256"]
    assert str(score.EVAL_SOURCE.resolve()) in report["source_sha256"]
    assert str(controls / "independent_view" / "summary.json") in report["source_sha256"]


def test_rejects_existing_outputs_before_invocation(tmp_path, monkeypatch):
    controls, _, _ = make_controls(tmp_path)
    (controls / "local_joint_evaluation.json").write_text("{}")
    monkeypatch.setattr(score, "run_old_score", lambda *_: (_ for _ in ()).throw(AssertionError("called")))

    with pytest.raises(ValueError, match="overwrite"):
        score.main(["--controls", str(controls)])


def test_validate_requires_exact_29_pairs_and_true_tail(tmp_path):
    controls, _, _ = make_controls(tmp_path)
    data = json.loads((controls / "summary.json").read_text())
    data["full_seam_adapter"]["case_counts"][0]["pair_count"] = 28
    write_json(controls / "summary.json", data)

    with pytest.raises(ValueError, match="29"):
        score.validate_summary(controls / "summary.json", independent=False)

    controls, _, _ = make_controls(tmp_path / "tail")
    data = json.loads((controls / "summary.json").read_text())
    data["full_seam_adapter"]["case_counts"][0]["uncovered_tail_frames_after_last_endpoint"] = 0
    data["full_seam_adapter"]["case_counts"][0]["recording_raw_frame_count"] = 1161
    write_json(controls / "summary.json", data)

    with pytest.raises(ValueError, match="tail"):
        score.validate_summary(controls / "summary.json", independent=False)


def test_rejects_joint_independent_input_and_schedule_mismatch(tmp_path):
    controls, _, input_file = make_controls(tmp_path)
    joint, _ = score.validate_summary(controls / "summary.json", independent=False)
    indep, _ = score.validate_summary(controls / "independent_summary.json", independent=True)
    indep["cases"][0]["input_sha256"] = {str(input_file): "0" * 64}
    with pytest.raises(ValueError, match="input hash"):
        score.validate_joint_independent_identity(joint, indep)

    controls, _, _ = make_controls(tmp_path / "schedule")
    joint, _ = score.validate_summary(controls / "summary.json", independent=False)
    indep, _ = score.validate_summary(controls / "independent_summary.json", independent=True)
    indep["cases"][0]["windows"][0]["indices"] = [1, 5, 10, 15, 20]
    with pytest.raises(ValueError, match="endpoint schedule"):
        score.validate_joint_independent_identity(joint, indep)

    controls, _, _ = make_controls(tmp_path / "decoded")
    joint, _ = score.validate_summary(controls / "summary.json", independent=False)
    indep, _ = score.validate_summary(controls / "independent_summary.json", independent=True)
    indep["cases"][0]["decoded_grayscale_frame_sha256"] = {"left:1": "different"}
    with pytest.raises(ValueError, match="decoded hash"):
        score.validate_joint_independent_identity(joint, indep)


def test_rejects_old_scorer_outputs_without_explicit_reference_flags(tmp_path):
    controls, _, _ = make_controls(tmp_path)
    joint_summary, _ = score.validate_summary(controls / "summary.json", independent=False)
    indep_summary, _ = score.validate_summary(controls / "independent_summary.json", independent=True)
    joint_score = fake_eval_from_summary(controls / "summary.json", 10.0)
    indep_score = fake_eval_from_summary(controls / "independent_summary.json", 20.0)

    missing = dict(joint_score)
    missing.pop("external_reference_used_in_estimation")
    with pytest.raises(ValueError, match="estimation"):
        score.summarize_scores(joint_summary, indep_summary, missing, indep_score)

    wrong = dict(indep_score)
    wrong["external_reference_used_in_evaluation"] = False
    with pytest.raises(ValueError, match="evaluation"):
        score.summarize_scores(joint_summary, indep_summary, joint_score, wrong)
