import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "evaluate_dual_ir_regression_acceptance.py"


def load_module():
    spec = importlib.util.spec_from_file_location("dual_ir_acceptance", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_case(root: Path, record_id: str, policy_args: dict | None = None, max_m: float = 0.009) -> dict:
    session = root / "sessions" / record_id
    capture = root / "captures" / record_id
    vins = root / "vins" / record_id
    artifact = root / "experiment" / record_id / "both"
    score = artifact / "score"
    for directory in (session, capture, vins, artifact, score):
        directory.mkdir(parents=True, exist_ok=True)
    frames = session / "d405_frames.csv"
    frames.write_text(f"{record_id},frames\n", encoding="utf-8")
    tracker = capture / "tracker.csv"
    tracker.write_text(f"{record_id},tracker\n", encoding="utf-8")
    reference = root / "frozen_reference.csv"
    if not reference.exists():
        reference.write_text("frozen,reference\n", encoding="utf-8")
    config = root / "config" / "vins_config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("body_T_cam0: []\n", encoding="utf-8")
    calibration = root / "frozen_calibration.json"
    if not calibration.exists():
        calibration.write_text('{"calibration": true}\n', encoding="utf-8")
    capture_manifest = capture / "capture_manifest.json"
    capture_manifest.write_text('{"status": "PASS_CAPTURE_ONLY_NOT_CALIBRATED"}\n', encoding="utf-8")
    estimate = artifact / "body_trajectory_fused.csv"
    estimate.write_text("t,x,y,z,qw,qx,qy,qz\n0,0,0,0,1,0,0,0\n", encoding="utf-8")
    selected_policy = policy_args or {
        "max_correction_mm": None,
        "correction_cap_mode": "global",
        "eyes": "both",
        "stereo_weight_policy": "uniform",
        "disable_learned_motion": False,
        "learned_motion_consistency_limit_m": None,
    }
    input_sha256 = {
        str(frames): sha(frames),
        str(tracker): sha(tracker),
        str(reference): sha(reference),
        str(config): sha(config),
    }
    write_json(
        artifact / "candidate_manifest.json",
        {
            "schema": "umi_dual_ir_symmetric_experiment_v1",
            "session": str(session),
            "external_ground_truth_used": False,
            "slam_supervision": False,
            "input_sha256": input_sha256,
            "policy_arguments": selected_policy,
        },
    )
    write_json(
        artifact / "graph_report.json",
        {
            "schema": "umi_dual_ir_symmetric_graph_diagnostic_v1",
            "external_ground_truth_used": False,
            "slam_supervision": False,
            "output_frame": "body_imu_origin",
            "output_samples": 5,
            "policy_arguments": selected_policy,
        },
    )
    write_json(
        score / "workflow_manifest.json",
        {
            "schema": "official_steamvr_slam_score_v1",
            "result": "SCORING_COMPLETED",
            "estimate": str(estimate),
            "estimate_frame": "body_imu_origin",
            "estimate_sha256": sha(estimate),
            "estimate_unchanged": True,
            "reference_manifest": str(reference),
            "reference_manifest_sha256": sha(reference),
            "slam_supervision": False,
        },
    )
    reference_csv = score / "steamvr_body_reference.csv"
    reference_csv.write_text("t,x,y,z,qw,qx,qy,qz\n0,0,0,0,1,0,0,0\n", encoding="utf-8")
    write_json(
        score / "reference_provenance.json",
        {
            "schema": "lighthouse_aprilgrid_ground_truth_provenance_v1",
            "result": "PASS",
            "slam_supervision": False,
            "reference_manifest_sha256": sha(reference),
            "capture_manifest_sha256": sha(capture_manifest),
            "tracker_csv_sha256": sha(tracker),
            "inputs": {
                "query_timestamps": str(estimate),
                "tracker": str(tracker),
                "calibration": str(calibration),
                "body_camera_config": str(config),
                "d405_frames": str(frames),
            },
            "sha256": {
                "calibration": sha(calibration),
                "body_camera_config": sha(config),
            },
            "output": str(reference_csv),
        },
    )
    precision = {
        "result": "PASS",
        "samples": 5,
        "estimate_samples_total": 5,
        "estimate_frame": "as_recorded",
        "estimate": str(estimate),
        "ground_truth": str(reference_csv),
        "ate_translation_max_m": max_m,
        "ate_translation_rmse_m": 0.003,
        "ate_translation_p95_m": 0.005,
        "ate_translation_mean_m": 0.002,
        "timestamp_overlap_ratio": 0.99,
    }
    write_json(score / "precision.json", precision)
    return {
        "id": record_id,
        "session": str(session),
        "capture_dir": str(capture),
        "vins_dir": str(vins),
        "artifact_dir": str(artifact),
        "selected_score": {
            "policy": "both",
            "ate_translation_max_m": max_m,
            "samples": 5,
        },
    }


def make_dataset(tmp_path: Path, *, count: int = 25) -> tuple[Path, Path, Path]:
    records = [make_case(tmp_path, f"case_{index:02d}") for index in range(count)]
    manifest = tmp_path / "manifest.json"
    summary = tmp_path / "experiment" / "summary.json"
    write_json(manifest, {"schema": "dual_ir_test_manifest_v1", "records": records})
    write_json(
        summary,
        {
            "schema": "umi_dual_ir_development_regression_v1",
            "status": "COMPLETED",
            "dataset_count": count,
            "results": [
                {
                    "id": record["id"],
                    "status": "COMPLETED",
                    "variants": {"both": {"score": record["selected_score"]}},
                }
                for record in records
            ],
        },
    )
    return manifest, summary, tmp_path / "experiment"


def evaluate(tmp_path: Path):
    module = load_module()
    manifest, summary, experiment = make_dataset(tmp_path)
    return module.evaluate(manifest, summary, "both", experiment)


def test_valid25_passes(tmp_path):
    result = evaluate(tmp_path)

    assert result["result"] == "PASS"
    assert result["dataset_count"] == 25
    assert len(result["rows"]) == 25
    assert "DEVELOPMENT_ONLY" in result["header"]


@pytest.mark.parametrize(
    "mutate,pattern",
    [
        (lambda m, s: m["records"].pop(), "dataset_count"),
        (lambda m, s: m["records"].append(dict(m["records"][0])), "duplicate"),
        (lambda m, s: s["results"].pop(), "summary ID"),
    ],
)
def test_manifest_id_cardinality_failures(tmp_path, mutate, pattern):
    manifest, summary, experiment = make_dataset(tmp_path)
    m, s = json.loads(manifest.read_text()), json.loads(summary.read_text())
    mutate(m, s)
    write_json(manifest, m)
    write_json(summary, s)
    result = load_module().evaluate(manifest, summary, "both", experiment)

    assert result["result"] == "FAIL"
    assert any(pattern in failure["message"] for failure in result["failures"])
    assert len(result["rows"]) == len(m["records"])


def test_one_223mm_outlier_fails_without_dropping_rows(tmp_path):
    manifest, summary, experiment = make_dataset(tmp_path)
    precision = experiment / "case_07" / "both" / "score" / "precision.json"
    data = json.loads(precision.read_text())
    data["ate_translation_max_m"] = 0.223
    write_json(precision, data)
    result = load_module().evaluate(manifest, summary, "both", experiment)

    assert result["result"] == "FAIL"
    assert len(result["rows"]) == 25
    assert any("max" in failure["message"] for failure in result["failures"])


def test_per_case_mixed_policy_fails(tmp_path):
    manifest, summary, experiment = make_dataset(tmp_path)
    candidate = experiment / "case_03" / "both" / "candidate_manifest.json"
    data = json.loads(candidate.read_text())
    data["policy_arguments"]["eyes"] = "right"
    write_json(candidate, data)
    result = load_module().evaluate(manifest, summary, "both", experiment)

    assert result["result"] == "FAIL"
    assert any("policy_arguments" in failure["message"] for failure in result["failures"])


def test_gt_supervision_fails(tmp_path):
    manifest, summary, experiment = make_dataset(tmp_path)
    candidate = experiment / "case_04" / "both" / "candidate_manifest.json"
    data = json.loads(candidate.read_text())
    data["external_ground_truth_used"] = True
    write_json(candidate, data)
    result = load_module().evaluate(manifest, summary, "both", experiment)

    assert result["result"] == "FAIL"
    assert any("GT" in failure["message"] or "supervision" in failure["message"] for failure in result["failures"])


def test_modified_estimate_hash_fails(tmp_path):
    manifest, summary, experiment = make_dataset(tmp_path)
    estimate = experiment / "case_05" / "both" / "body_trajectory_fused.csv"
    estimate.write_text(estimate.read_text() + "1,1,1,1,1,0,0,0\n", encoding="utf-8")
    result = load_module().evaluate(manifest, summary, "both", experiment)

    assert result["result"] == "FAIL"
    assert any("estimate_sha256" in failure["message"] for failure in result["failures"])


def test_reference_mismatch_fails(tmp_path):
    manifest, summary, experiment = make_dataset(tmp_path)
    workflow = experiment / "case_06" / "both" / "score" / "workflow_manifest.json"
    data = json.loads(workflow.read_text())
    data["reference_manifest_sha256"] = "bad"
    write_json(workflow, data)
    result = load_module().evaluate(manifest, summary, "both", experiment)

    assert result["result"] == "FAIL"
    assert any("reference" in failure["message"] for failure in result["failures"])


def test_modified_tracker_hash_fails(tmp_path):
    manifest, summary, experiment = make_dataset(tmp_path)
    data = json.loads(manifest.read_text())
    capture = Path(data["records"][9]["capture_dir"])
    (capture / "tracker.csv").write_text("changed\n", encoding="utf-8")
    result = load_module().evaluate(manifest, summary, "both", experiment)

    assert result["result"] == "FAIL"
    assert any("tracker" in failure["message"] for failure in result["failures"])


@pytest.mark.parametrize(
    "key,source_case,pattern",
    [
        ("query_timestamps", "case_10", "query_timestamps"),
        ("tracker", "case_10", "tracker"),
        ("d405_frames", "case_10", "d405_frames"),
    ],
)
def test_reference_provenance_rejects_cross_record_input_paths(tmp_path, key, source_case, pattern):
    manifest, summary, experiment = make_dataset(tmp_path)
    provenance = experiment / "case_09" / "both" / "score" / "reference_provenance.json"
    source_provenance = experiment / source_case / "both" / "score" / "reference_provenance.json"
    data = json.loads(provenance.read_text())
    source = json.loads(source_provenance.read_text())
    data["inputs"][key] = source["inputs"][key]
    write_json(provenance, data)

    result = load_module().evaluate(manifest, summary, "both", experiment)

    assert result["result"] == "FAIL"
    assert any(pattern in failure["message"] for failure in result["failures"])


def test_reference_provenance_output_must_exist_and_match_precision_ground_truth(tmp_path):
    manifest, summary, experiment = make_dataset(tmp_path)
    provenance = experiment / "case_11" / "both" / "score" / "reference_provenance.json"
    data = json.loads(provenance.read_text())
    data["output"] = str(experiment / "case_11" / "both" / "score" / "missing_reference.csv")
    write_json(provenance, data)

    result = load_module().evaluate(manifest, summary, "both", experiment)

    assert result["result"] == "FAIL"
    messages = [failure["message"] for failure in result["failures"]]
    assert any("output reference file is missing" in message for message in messages)
    assert any("output does not match precision" in message for message in messages)


def test_nonfinite_precision_fails(tmp_path):
    manifest, summary, experiment = make_dataset(tmp_path)
    precision = experiment / "case_08" / "both" / "score" / "precision.json"
    text = precision.read_text().replace("0.009", "NaN")
    precision.write_text(text, encoding="utf-8")
    result = load_module().evaluate(manifest, summary, "both", experiment)

    assert result["result"] == "FAIL"
    assert any("finite" in failure["message"] for failure in result["failures"])


def test_missing_file_cli_returns_json_fail_exit3(tmp_path):
    manifest, summary, _ = make_dataset(tmp_path)
    missing_summary = tmp_path / "missing.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--manifest",
            str(manifest),
            "--summary",
            str(missing_summary),
            "--policy",
            "both",
        ],
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 3
    payload = json.loads(proc.stdout)
    assert payload["result"] == "FAIL"
    assert "Traceback" not in proc.stderr
