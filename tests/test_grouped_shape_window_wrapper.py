import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "fuse_mast3r_grouped_shape_windows.py"
SPEC = importlib.util.spec_from_file_location("fuse_mast3r_grouped_shape_windows", SCRIPT)
wrapper = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = wrapper
SPEC.loader.exec_module(wrapper)


CASES = ["dev1", "dev2", "heldout1", "heldout2", "heldout3", "heldout4", "fresh1", "fresh2", "fresh3", "fresh4"]


def digest(path: Path) -> str:
    return wrapper.seam_wrapper.file_sha256(path)


def shape_factor():
    return {
        "available": True,
        "diagnostic_only": True,
        "solver_accepted": True,
        "not_admissible_for_graph": True,
        "frames": 9,
        "gauge": "all_relative_centers_mapped_through_first_camera_rotation",
        "compact_sqrt_sensitivity": [[1.0] + [0.0] * 23],
        "reference_center_vector_m": [0.0] * 24,
        "affine_offset": [0.0],
        "metadata": {
            "available_for_graph": False,
            "calibrated_covariance": False,
            "statistical_independence_claimed": False,
        },
    }


def schedules():
    return wrapper.full_shape.expected_pair_schedule()


def case_rows(name: str, input_map: dict[str, str], *, refused_pair=None, missing_factor_pair=None):
    pairs, windows = [], []
    factor = shape_factor()
    for pair_index, indices in enumerate(schedules(), 1):
        accepted = pair_index != refused_pair
        pairs.append(
            {
                "pair": pair_index,
                "indices": indices,
                "raw_frame_indices": list(range(indices[0], indices[-1] + 1)),
                "accepted": accepted,
                "reason": "ok" if accepted else "model_consistency_failed",
            }
        )
        for part, endpoint in enumerate((indices[:5], indices[4:])):
            row = {
                "window": 2 * (pair_index - 1) + part + 1,
                "joint_pair": pair_index,
                "indices": endpoint,
                "elapsed_s": [float(value) for value in endpoint],
                "accepted": accepted,
                "reason": "ok" if accepted else "model_consistency_failed",
                "endpoint_m": [0.01 * pair_index, 0.001 * part, 0.02],
                "diagnostics": {},
            }
            if accepted and pair_index != missing_factor_pair:
                row["diagnostics"]["stereo_window_shape_factor"] = factor
            windows.append(row)
    independent_windows = [dict(row) for row in windows]
    for row in independent_windows:
        row.pop("joint_pair", None)
    return {
        "case": name,
        "pairs": pairs,
        "windows": windows,
        "independent_windows": independent_windows,
        "input_sha256": dict(input_map),
        "decoded_grayscale_frame_sha256": {f"{name}:left:0": "d" * 64},
    }


def write_shape_summaries(root: Path, *, refused_pair=None, missing_factor_pair=None, mutate=None):
    root.mkdir(parents=True, exist_ok=True)
    source = root / "input.txt"
    source.write_text("input")
    input_map = {str(source.resolve()): digest(source)}
    cases = [
        case_rows(name, input_map, refused_pair=refused_pair if name == "dev1" else None,
                  missing_factor_pair=missing_factor_pair if name == "dev1" else None)
        for name in CASES
    ]
    if mutate:
        mutate(cases)
    case_counts = {}
    for name in CASES:
        expected = wrapper.full_shape.EXPECTED_RAW_COUNTS[name]
        case_counts[name] = {
            "recording_raw_frame_count": expected,
            "pair_count": 29,
            "joint_window_count": 58,
            "independent_window_count": 58,
            "last_endpoint_index": 1160,
            "uncovered_tail_frames_after_last_endpoint": expected - 1 - 1160,
        }
    adapter = {
        "diagnostic_only": True,
        "external_ground_truth_used": False,
        "used_for_graph_or_selection": False,
        "available_for_graph": False,
        "calibrated_covariance": False,
        "statistical_independence_claimed": False,
        "production_modified": False,
        "joint_pairs_per_case": 29,
        "endpoint_rows_per_case": 58,
        "case_counts": case_counts,
    }
    sources = wrapper.full_shape.hash_sources()
    joint = {
        "cases": cases,
        "source_sha256": sources,
        "external_reference_used": False,
        "production_modified": False,
        "full_shape_window_adapter": {**adapter, "independent_summary": False},
    }
    independent_cases = [{**case, "windows": case["independent_windows"]} for case in cases]
    independent = {
        **joint,
        "cases": independent_cases,
        "full_shape_window_adapter": {**adapter, "independent_summary": True},
    }
    joint_path = root / "shape_summary.json"
    independent_path = root / "shape_independent_summary.json"
    joint_path.write_text(json.dumps(joint) + "\n")
    independent_path.write_text(json.dumps(independent) + "\n")
    return joint_path, independent_path, input_map


def write_old_seam_summary(path: Path, input_map: dict[str, str], *, source_file: Path, refused_pair=None, mutate=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    cases = []
    for name in CASES:
        case = case_rows(name, input_map, refused_pair=refused_pair if name == "dev1" else None)
        cases.append({"case": name, "windows": case["windows"], "input_sha256": dict(input_map)})
    if mutate:
        mutate(cases)
    data = {
        "external_reference_used": False,
        "source_sha256": {str(source_file.resolve()): digest(source_file)},
        "full_seam_adapter": {
            "raw_interval_frames": 40,
            "independent_summary": False,
            "correlated_paired_endpoints": True,
            "calibrated_covariance": False,
            "case_counts": [
                {
                    "case": name,
                    "pair_count": 29,
                    "window_count": 58,
                    "recording_raw_frame_count": wrapper.seam_wrapper.EXPECTED_RAW_COUNTS[name],
                    "last_endpoint_index": 1160,
                    "uncovered_tail_frames_after_last_endpoint": wrapper.seam_wrapper.EXPECTED_RAW_COUNTS[name] - 1 - 1160,
                }
                for name in CASES
            ],
        },
        "cases": cases,
    }
    path.write_text(json.dumps(data) + "\n")


def native_args(tmp_path: Path, old_controls: Path):
    return [
        "--seam-window-controls", str(old_controls),
        "--seam-window-case", "dev1",
        "--seam-window-mode", "joint",
        "--trajectory", str(tmp_path / "trajectory.csv"),
        "--stereo-report", str(tmp_path / "stereo.json"),
        "--stream", "infrared_left",
        "--session", str(tmp_path / "session"),
        "--vins-config", str(tmp_path / "vins.yaml"),
        "--imu-calibration", str(tmp_path / "imu.json"),
        "--expected-td-s", "-0.009109323",
        "--position-mode", "joint-inertial",
        "--output", str(tmp_path / "out.csv"),
        "--report", str(tmp_path / "report.json"),
    ]


def setup_controls(tmp_path: Path, **kwargs):
    shape_controls, independent, input_map = write_shape_summaries(tmp_path, **{key: value for key, value in kwargs.items() if key in {"refused_pair", "missing_factor_pair", "mutate"}})
    old_source = tmp_path / "old_source.py"
    old_source.write_text("old")
    old_controls = tmp_path / "old_seam_summary.json"
    write_old_seam_summary(old_controls, input_map, source_file=old_source, refused_pair=kwargs.get("refused_pair"), mutate=kwargs.get("old_mutate"))
    return shape_controls, independent, old_controls


def run_args(shape_controls: Path, independent: Path, old_args: list[str], mode="grouped_shape"):
    return [
        "--shape-window-controls", str(shape_controls),
        "--shape-window-independent-controls", str(independent),
        "--shape-window-mode", mode,
        *old_args,
    ]


def test_build_groups_retains_refusals_and_rejects_missing_accepted_factor(tmp_path):
    joint_path, independent_path, _ = write_shape_summaries(tmp_path, refused_pair=3)
    joint = wrapper.load_json(joint_path)
    independent = wrapper.load_json(independent_path)
    wrapper._validate_shape_summaries(joint, independent)
    groups, refusals = wrapper._build_shape_groups(next(case for case in joint["cases"] if case["case"] == "dev1"))
    assert len(groups) == 28
    assert refusals == [{
        "pair": 3,
        "indices": schedules()[2],
        "endpoint_indices": [schedules()[2][:5], schedules()[2][4:]],
        "reasons": ["model_consistency_failed", "model_consistency_failed"],
    }]
    assert groups[0]["group_id"] == "dev1:pair:1"

    joint_path, _independent_path, _ = write_shape_summaries(tmp_path / "missing", missing_factor_pair=1)
    joint = wrapper.load_json(joint_path)
    with pytest.raises(ValueError, match="missing"):
        wrapper._build_shape_groups(next(case for case in joint["cases"] if case["case"] == "dev1"))


def test_main_forwards_old_args_intercepts_exactly_once_and_restores(tmp_path, monkeypatch):
    shape_controls, independent, old_controls = setup_controls(tmp_path)
    old_args = native_args(tmp_path, old_controls)
    expected_old_args = list(old_args)
    report = tmp_path / "report.json"
    original_refine = wrapper.seam_wrapper.native.refine_positions_visual_inertial
    captured = {}

    def fake_original(*args, **kwargs):
        return "native-refined", {"native": True}

    def fake_refiner(native, groups, mode, *args, expected_native_sha256=None, **kwargs):
        captured["native_restored_inside_adapter"] = native.refine_positions_visual_inertial is fake_original
        captured["groups"] = groups
        captured["mode"] = mode
        captured["expected_native_sha256"] = expected_native_sha256
        return "shape-refined", {"stereo_window_shape_refiner": {"actual_returned_camera_shape_evaluation": []}}

    def fake_old_main(argv=None):
        captured["argv"] = list(argv)
        refined, native_report = wrapper.seam_wrapper.native.refine_positions_visual_inertial(1, a=2)
        captured["refined"] = refined
        captured["native_report"] = native_report
        report.write_text(json.dumps({"old": "report"}) + "\n")
        return 0

    monkeypatch.setattr(wrapper.seam_wrapper.native, "refine_positions_visual_inertial", fake_original)
    monkeypatch.setattr(wrapper.shape_refiner, "refine_positions_visual_inertial_with_shape", fake_refiner)
    monkeypatch.setattr(wrapper.seam_wrapper, "main", fake_old_main)

    assert wrapper.main(run_args(shape_controls, independent, old_args)) == 0

    assert captured["argv"] == expected_old_args
    assert captured["mode"] == "grouped_shape"
    assert len(captured["groups"]) == 29
    assert captured["groups"][0]["group_id"] == "dev1:pair:1"
    assert captured["native_restored_inside_adapter"] is True
    assert captured["expected_native_sha256"] == wrapper.seam_wrapper.file_sha256(Path(wrapper.seam_wrapper.native.__file__))
    assert captured["refined"] == "shape-refined"
    assert wrapper.seam_wrapper.native.refine_positions_visual_inertial is fake_original
    saved = json.loads(report.read_text())
    integration = saved["grouped_shape_window_refinement"]
    assert integration["group_count"] == 29
    assert integration["shape_window_mode"] == "grouped_shape"
    assert integration["used_for_selection"] is False
    assert integration["used_for_production_graph"] is False
    assert integration["used_for_research_graph"] is True
    assert integration["available_for_production"] is False
    assert "used_for_selection_or_graph" not in integration
    assert "available_for_graph" not in integration
    manifest = json.loads(Path(integration["manifest"]).read_text())
    assert manifest["refiner_call_count"] == 1
    assert manifest["shape_refinement_scope"] == "actual_returned_camera_shape_evaluation_only"
    assert manifest["used_for_selection"] is False
    assert manifest["used_for_production_graph"] is False
    assert manifest["used_for_research_graph"] is True
    assert manifest["available_for_production"] is False
    monkeypatch.setattr(wrapper.seam_wrapper.native, "refine_positions_visual_inertial", original_refine)


def test_endpoint_control_mode_and_refused_count_are_reported(tmp_path, monkeypatch):
    shape_controls, independent, old_controls = setup_controls(tmp_path, refused_pair=2)
    old_args = native_args(tmp_path, old_controls)
    report = tmp_path / "report.json"

    def fake_refiner(native, groups, mode, *args, expected_native_sha256=None, **kwargs):
        assert mode == "endpoint_control"
        assert len(groups) == 28
        return None, {}

    def fake_old_main(argv=None):
        wrapper.seam_wrapper.native.refine_positions_visual_inertial()
        report.write_text(json.dumps({}) + "\n")
        return 0

    monkeypatch.setattr(wrapper.shape_refiner, "refine_positions_visual_inertial_with_shape", fake_refiner)
    monkeypatch.setattr(wrapper.seam_wrapper, "main", fake_old_main)
    wrapper.main(run_args(shape_controls, independent, old_args, mode="endpoint_control"))

    saved = json.loads(report.read_text())["grouped_shape_window_refinement"]
    assert saved["group_count"] == 28
    assert saved["refused_pair_count"] == 1


def test_rejects_bad_position_mode_before_old_wrapper_outputs(tmp_path, monkeypatch):
    shape_controls, independent, old_controls = setup_controls(tmp_path)
    old_args = native_args(tmp_path, old_controls)
    old_args[old_args.index("--position-mode") + 1] = "full"
    called = False

    def fake_old_main(argv=None):
        nonlocal called
        called = True

    monkeypatch.setattr(wrapper.seam_wrapper, "main", fake_old_main)
    with pytest.raises(ValueError, match="position-mode"):
        wrapper.main(run_args(shape_controls, independent, old_args))
    assert called is False
    assert not (tmp_path / "report.json").exists()
    assert not (tmp_path / "out.csv").exists()


def test_shape_groups_require_literal_acceptance_and_exact_indices(tmp_path):
    joint_path, _independent_path, _ = write_shape_summaries(
        tmp_path,
        mutate=lambda cases: cases[0]["pairs"][0].__setitem__("accepted", 1),
    )
    joint = wrapper.load_json(joint_path)
    with pytest.raises(ValueError, match="literal bool"):
        wrapper._build_shape_groups(next(case for case in joint["cases"] if case["case"] == "dev1"))

    joint_path, _independent_path, _ = write_shape_summaries(
        tmp_path / "window_bool",
        mutate=lambda cases: cases[0]["windows"][0].__setitem__("accepted", "yes"),
    )
    joint = wrapper.load_json(joint_path)
    with pytest.raises(ValueError, match="literal bool"):
        wrapper._build_shape_groups(next(case for case in joint["cases"] if case["case"] == "dev1"))

    joint_path, _independent_path, _ = write_shape_summaries(
        tmp_path / "mismatch",
        mutate=lambda cases: cases[0]["windows"][0].__setitem__("accepted", False),
    )
    joint = wrapper.load_json(joint_path)
    with pytest.raises(ValueError, match="pair/window accepted mismatch"):
        wrapper._build_shape_groups(next(case for case in joint["cases"] if case["case"] == "dev1"))

    joint_path, _independent_path, _ = write_shape_summaries(
        tmp_path / "indices",
        mutate=lambda cases: cases[0]["pairs"][0].__setitem__("indices", schedules()[0][:8] + [41]),
    )
    joint = wrapper.load_json(joint_path)
    with pytest.raises(ValueError, match="indices9 schedule"):
        wrapper._build_shape_groups(next(case for case in joint["cases"] if case["case"] == "dev1"))


def test_zero_multiple_and_exception_restore_native_hook(tmp_path, monkeypatch):
    shape_controls, independent, old_controls = setup_controls(tmp_path)
    old_args = native_args(tmp_path, old_controls)
    report = tmp_path / "report.json"
    original = wrapper.seam_wrapper.native.refine_positions_visual_inertial

    def no_call(argv=None):
        report.write_text(json.dumps({}) + "\n")
        return 0

    monkeypatch.setattr(wrapper.seam_wrapper, "main", no_call)
    with pytest.raises(ValueError, match="exactly one"):
        wrapper.main(run_args(shape_controls, independent, old_args))
    assert wrapper.seam_wrapper.native.refine_positions_visual_inertial is original

    def two_calls(argv=None):
        wrapper.seam_wrapper.native.refine_positions_visual_inertial()
        wrapper.seam_wrapper.native.refine_positions_visual_inertial()

    monkeypatch.setattr(wrapper.seam_wrapper, "main", two_calls)
    monkeypatch.setattr(wrapper.shape_refiner, "refine_positions_visual_inertial_with_shape", lambda *a, **k: (None, {}))
    with pytest.raises(ValueError, match="exactly one"):
        wrapper.main(run_args(shape_controls, independent, old_args))
    assert wrapper.seam_wrapper.native.refine_positions_visual_inertial is original

    def raises_after_call(argv=None):
        wrapper.seam_wrapper.native.refine_positions_visual_inertial()
        raise RuntimeError("old seam failed")

    monkeypatch.setattr(wrapper.seam_wrapper, "main", raises_after_call)
    with pytest.raises(RuntimeError, match="old seam failed"):
        wrapper.main(run_args(shape_controls, independent, old_args))
    assert wrapper.seam_wrapper.native.refine_positions_visual_inertial is original


def test_controls_identity_source_and_gt_flags_fail_closed(tmp_path, monkeypatch):
    shape_controls, independent, old_controls = setup_controls(
        tmp_path,
        old_mutate=lambda cases: cases[0]["windows"][0].__setitem__("endpoint_m", [9.0, 0.0, 0.0]),
    )
    old_args = native_args(tmp_path, old_controls)
    monkeypatch.setattr(wrapper.seam_wrapper, "main", lambda argv=None: pytest.fail("old seam should not run"))
    with pytest.raises(ValueError, match="endpoint_m"):
        wrapper.main(run_args(shape_controls, independent, old_args))

    shape_controls, independent, old_controls = setup_controls(tmp_path / "source")
    data = json.loads(shape_controls.read_text())
    data["source_sha256"][next(iter(data["source_sha256"]))] = "0" * 64
    shape_controls.write_text(json.dumps(data) + "\n")
    with pytest.raises(ValueError, match="source hash"):
        wrapper.main(run_args(shape_controls, independent, native_args(tmp_path / "source", old_controls)))

    shape_controls, independent, old_controls = setup_controls(tmp_path / "gt")
    data = json.loads(shape_controls.read_text())
    data["external_reference_used"] = True
    shape_controls.write_text(json.dumps(data) + "\n")
    with pytest.raises(ValueError, match="external reference"):
        wrapper.main(run_args(shape_controls, independent, native_args(tmp_path / "gt", old_controls)))
