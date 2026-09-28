import importlib.util
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "shape_window_controls",
    ROOT / ".planning" / "metric_window_bundle_20260928" / "run_shape_window_controls.py",
)
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


CASE_NAMES = ["dev1", "dev2", "heldout1", "heldout2", "heldout3", "heldout4", "fresh1", "fresh2", "fresh3", "fresh4"]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def case_rows(name: str):
    count = adapter.EXPECTED_RAW_COUNTS[name]
    pairs = []
    windows = []
    for number, selection in enumerate(adapter.seam.previous.pair_windows(count), 1):
        pairs.append(
            {
                "pair": number,
                "indices": selection.tolist(),
                "raw_frame_indices": list(range(int(selection[0]), int(selection[-1]) + 1)),
                "accepted": True,
                "reason": "ok",
            }
        )
        for part, indices in enumerate((selection[:5], selection[4:])):
            windows.append(
                {
                    "window": 2 * (number - 1) + part + 1,
                    "joint_pair": number,
                    "indices": indices.tolist(),
                    "accepted": True,
                    "reason": "ok",
                    "diagnostics": {},
                }
            )
    return pairs, windows


def write_fake_outputs(output: Path, *, independent_joint_pair: str = "present"):
    output.mkdir(parents=True, exist_ok=True)
    cases = []
    for name in CASE_NAMES:
        input_path = output / f"{name}_input.txt"
        input_path.write_text(f"input:{name}")
        pairs, windows = case_rows(name)
        independent_windows = [dict(row) for row in windows]
        if independent_joint_pair == "missing":
            for row in independent_windows:
                row.pop("joint_pair", None)
        elif independent_joint_pair == "bad":
            independent_windows[0]["joint_pair"] = 99
        case = {
            "case": name,
            "windows": windows,
            "independent_windows": independent_windows,
            "pairs": pairs,
            "input_sha256": {str(input_path): sha(input_path)},
            "decoded_grayscale_frame_sha256": {f"left:{name}:0": "d" * 64},
        }
        cases.append(case)
        (output / f"{name}.json").write_text(json.dumps(case) + "\n")
    summary = {
        "cases": cases,
        "source_sha256": {},
        "external_reference_used": False,
        "production_modified": False,
        "correlated_factors_from_shared_window": True,
    }
    independent = [{**case, "windows": case["independent_windows"]} for case in cases]
    (output / "summary.json").write_text(json.dumps(summary) + "\n")
    (output / "independent_summary.json").write_text(
        json.dumps({**summary, "cases": independent, "correlated_factors_from_shared_window": False}) + "\n"
    )


def minimal_data():
    train = np.array([True, False, True, True])
    admitted = np.array(
        [
            [True, True, True, True],
            [True, False, True, True],
            [True, False, True, True],
        ]
    )
    return {
        "observations": np.arange(3 * 4 * 4, dtype=float).reshape(3, 4, 4),
        "admitted_train": admitted,
        "train": train,
        "initial_points": np.arange(12, dtype=float).reshape(4, 3),
        "initial_centers": np.zeros((3, 3)),
        "initial_rotations": Rotation.identity(3),
    }


def calibration():
    return {
        "left_intrinsics": {"fx": 1.0, "fy": 1.0, "cx": 0.0, "cy": 0.0},
        "right_intrinsics": {"fx": 1.0, "fy": 1.0, "cx": 0.0, "cy": 0.0},
        "baseline_m": 0.018,
    }


def test_shape_solve_preserves_joint_training_support_and_solver_arguments(monkeypatch):
    data = minimal_data()
    times = np.array([10.0, 10.5, 11.0])
    deltas = Rotation.identity(2)
    jacobians = -np.repeat(np.eye(3)[None], 2, axis=0)
    support = np.array([True, False, True, False])
    captured = {}

    def training_support(train, admitted, points):
        np.testing.assert_array_equal(train, data["train"])
        np.testing.assert_array_equal(admitted, data["admitted_train"])
        np.testing.assert_array_equal(points, data["initial_points"])
        return support

    def solve(bundle, *args, **kwargs):
        captured["bundle"] = bundle
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"accepted": True, "reason": "ok", "diagnostics": {"stereo_window_shape_factor": {}}}

    monkeypatch.setattr(adapter.seam.previous.base, "training_support", training_support)
    monkeypatch.setattr(adapter.shape, "solve_with_shape_factor", solve)

    result = adapter.solve_with_shape_factor(data, calibration(), times, deltas, jacobians)

    assert result["accepted"] is True
    assert captured["bundle"] is adapter.bundle
    args = captured["args"]
    np.testing.assert_array_equal(args[0], data["observations"][:, support])
    np.testing.assert_array_equal(args[1], data["admitted_train"][:, support])
    np.testing.assert_allclose(args[2], times - times[0])
    assert args[3] == calibration()["left_intrinsics"]
    assert args[4] == calibration()["right_intrinsics"]
    assert args[5] == calibration()["baseline_m"]
    np.testing.assert_array_equal(args[6], data["initial_points"][support])
    np.testing.assert_array_equal(args[7], data["initial_centers"])
    assert args[8] is data["initial_rotations"]
    assert args[9] is deltas
    assert captured["kwargs"]["gyro_noise_density"] == pytest.approx(0.00103)
    assert captured["kwargs"]["gyro_bias_sigma"] == pytest.approx(0.01 / 3.0)
    assert captured["kwargs"]["gyro_bias_jacobians"] is jacobians


def test_main_restores_hooks_and_writes_both_summary_provenance(tmp_path, monkeypatch):
    output = tmp_path / "shape"
    original_solve = adapter.seam.previous.solve
    original_argv = sys.argv[:]
    called = {}

    def fake_main():
        called["solve_is_patched"] = adapter.seam.previous.solve is adapter.solve_with_shape_factor
        called["argv"] = sys.argv[:]
        write_fake_outputs(output)
        return 7

    monkeypatch.setattr(adapter.seam, "main", fake_main)

    assert adapter.main(["--output", str(output)]) == 7

    assert called["solve_is_patched"] is True
    assert called["argv"] == [str(Path(adapter.seam.__file__).resolve()), "--output", str(output)]
    assert adapter.seam.previous.solve is original_solve
    assert sys.argv == original_argv
    joint = json.loads((output / "summary.json").read_text())
    independent = json.loads((output / "independent_summary.json").read_text())
    for summary, independent_flag in ((joint, False), (independent, True)):
        assert summary["shape_window_adapter"]["diagnostic_only"] is True
        assert summary["shape_window_adapter"]["external_ground_truth_used"] is False
        assert summary["shape_window_adapter"]["used_for_graph_or_selection"] is False
        assert summary["shape_window_adapter"]["available_for_graph"] is False
        assert summary["shape_window_adapter"]["calibrated_covariance"] is False
        assert summary["shape_window_adapter"]["statistical_independence_claimed"] is False
        assert summary["shape_window_adapter"]["independent_summary"] is independent_flag
        assert summary["shape_window_adapter"]["endpoint_diagnostic_counts"]["missing"] == 100
        assert summary["shape_window_adapter"]["pair_group_counts"]["missing"] == 50
        assert str(Path(adapter.shape.__file__).resolve()) in summary["source_sha256"]
        assert str(Path(adapter.__file__).resolve()) in summary["source_sha256"]
        assert len(summary["cases"]) == 10
        assert sum(len(case["windows"]) for case in summary["cases"]) == 100
        assert sum(len(case["pairs"]) for case in summary["cases"]) == 50
    assert joint["cases"][0]["input_sha256"] == independent["cases"][0]["input_sha256"]
    assert joint["cases"][0]["decoded_grayscale_frame_sha256"] == independent["cases"][0]["decoded_grayscale_frame_sha256"]


def test_main_refuses_existing_output_before_patch_and_rejects_extra_args(tmp_path, monkeypatch):
    output = tmp_path / "exists"
    output.mkdir()
    original_solve = adapter.seam.previous.solve
    monkeypatch.setattr(adapter.seam, "main", lambda: pytest.fail("base main should not run"))
    with pytest.raises(ValueError, match="overwrite"):
        adapter.main(["--output", str(output)])
    assert adapter.seam.previous.solve is original_solve

    with pytest.raises(SystemExit):
        adapter.main(["--output", str(tmp_path / "new"), "--unexpected"])
    assert not (tmp_path / "new").exists()
    assert adapter.seam.previous.solve is original_solve


def test_source_change_after_base_run_fails_and_restores(tmp_path, monkeypatch):
    output = tmp_path / "shape"
    source = tmp_path / "adapter_source.py"
    source.write_text("before")
    original_solve = adapter.seam.previous.solve
    original_paths = adapter.source_paths

    monkeypatch.setattr(adapter, "source_paths", lambda: [source])

    def fake_main():
        write_fake_outputs(output)
        source.write_text("after")

    monkeypatch.setattr(adapter.seam, "main", fake_main)

    with pytest.raises(ValueError, match="source changed"):
        adapter.main(["--output", str(output)])
    assert adapter.seam.previous.solve is original_solve
    monkeypatch.setattr(adapter, "source_paths", original_paths)


def test_schedule_validation_rejects_extra_case_or_shifted_window(tmp_path):
    output = tmp_path / "shape"
    write_fake_outputs(output)
    joint = json.loads((output / "summary.json").read_text())
    independent = json.loads((output / "independent_summary.json").read_text())
    joint["cases"].append({**joint["cases"][0], "case": "extra"})
    with pytest.raises(ValueError, match="exact ten unique"):
        adapter._validate_pair_summaries(joint, independent)

    write_fake_outputs(output)
    joint = json.loads((output / "summary.json").read_text())
    independent = json.loads((output / "independent_summary.json").read_text())
    joint["cases"].append({**joint["cases"][0]})
    with pytest.raises(ValueError, match="exact ten unique"):
        adapter._validate_pair_summaries(joint, independent)

    write_fake_outputs(output)
    joint = json.loads((output / "summary.json").read_text())
    independent = json.loads((output / "independent_summary.json").read_text())
    joint["cases"][0]["windows"][0]["indices"] = [1, 2, 3, 4, 5]
    with pytest.raises(ValueError, match="indices"):
        adapter._validate_pair_summaries(joint, independent)

    write_fake_outputs(output)
    joint = json.loads((output / "summary.json").read_text())
    independent = json.loads((output / "independent_summary.json").read_text())
    independent["cases"][0]["windows"][0]["indices"] = [1, 2, 3, 4, 5]
    with pytest.raises(ValueError, match="independent summary windows"):
        adapter._validate_pair_summaries(joint, independent)

    output_missing = tmp_path / "shape_missing_joint_pair"
    write_fake_outputs(output_missing, independent_joint_pair="missing")
    adapter._validate_pair_summaries(
        json.loads((output_missing / "summary.json").read_text()),
        json.loads((output_missing / "independent_summary.json").read_text()),
    )

    output_bad = tmp_path / "shape_bad_joint_pair"
    write_fake_outputs(output_bad, independent_joint_pair="bad")
    with pytest.raises(ValueError, match="independent joint_pair"):
        adapter._validate_pair_summaries(
            json.loads((output_bad / "summary.json").read_text()),
            json.loads((output_bad / "independent_summary.json").read_text()),
        )


def test_real_raw_shape_pairs_schema_validates_optional_independent_joint_pair():
    raw = ROOT / "reports" / "metric_window_bundle_20260928" / "shape_pairs_ten_v1"
    if not (raw / "summary.json").exists():
        pytest.skip("real raw shape-pairs summary not present")
    adapter._validate_pair_summaries(
        json.loads((raw / "summary.json").read_text()),
        json.loads((raw / "independent_summary.json").read_text()),
    )


def test_source_hash_conflict_rejected(tmp_path):
    output = tmp_path / "shape"
    write_fake_outputs(output)
    summary = json.loads((output / "summary.json").read_text())
    summary["source_sha256"][str(Path(adapter.__file__).resolve())] = "bad"
    (output / "summary.json").write_text(json.dumps(summary) + "\n")
    with pytest.raises(ValueError, match="source hash conflict"):
        adapter.annotate(output, adapter.hash_sources())


def test_input_hashes_fail_closed_and_shape_group_counts(tmp_path):
    output = tmp_path / "shape"
    write_fake_outputs(output)
    joint = json.loads((output / "summary.json").read_text())
    with pytest.raises(ValueError, match="input missing"):
        missing = {**joint, "cases": [{**joint["cases"][0], "input_sha256": {str(output / "missing"): "0" * 64}}]}
        adapter._verify_case_input_hashes(missing)
    with pytest.raises(ValueError, match="input_sha256"):
        empty = {**joint, "cases": [{**joint["cases"][0], "input_sha256": {}}]}
        adapter._verify_case_input_hashes(empty)
    with pytest.raises(ValueError, match="malformed"):
        bad = {**joint, "cases": [{**joint["cases"][0], "input_sha256": {str(output / "x"): "abc"}}]}
        adapter._verify_case_input_hashes(bad)
    with pytest.raises(ValueError, match="decoded"):
        empty_decoded = {**joint, "cases": [{**joint["cases"][0], "decoded_grayscale_frame_sha256": {}}]}
        adapter._verify_case_input_hashes(empty_decoded)

    factor_available = {
        "available": True,
        "solver_accepted": True,
        "not_admissible_for_graph": True,
        "conditional_rank": 6,
        "rank_deficient": False,
    }
    factor_unavailable = {
        "available": False,
        "solver_accepted": True,
        "not_admissible_for_graph": True,
        "rank_deficient": True,
    }
    joint["cases"][0]["windows"][0]["diagnostics"]["stereo_window_shape_factor"] = factor_available
    joint["cases"][0]["windows"][1]["diagnostics"]["stereo_window_shape_factor"] = factor_available
    joint["cases"][0]["windows"][2]["diagnostics"]["stereo_window_shape_factor"] = factor_unavailable
    joint["cases"][0]["windows"][3]["diagnostics"]["stereo_window_shape_factor"] = factor_unavailable
    joint["cases"][0]["windows"][4]["accepted"] = False
    joint["cases"][0]["windows"][5]["accepted"] = False
    counts = adapter._shape_factor_counts(joint)
    assert counts["endpoint_diagnostic_counts"]["available"] == 2
    assert counts["endpoint_diagnostic_counts"]["unavailable"] == 2
    assert counts["endpoint_diagnostic_counts"]["refused"] == 2
    assert counts["endpoint_diagnostic_counts"]["rank_histogram"]["6"] == 2
    assert counts["pair_group_counts"]["available"] == 1
    assert counts["pair_group_counts"]["unavailable"] == 1
    assert counts["pair_group_counts"]["refused"] == 1
    assert counts["pair_group_counts"]["missing"] == 47
