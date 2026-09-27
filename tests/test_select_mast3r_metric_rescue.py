import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.select_mast3r_metric_rescue import DENSE, decide, select

STEREO_STAGES = (
    "mast3r/stereo_scale_bidirectional_report.json",
    "mast3r/stereo_scale_long_hops_report.json",
    DENSE,
    "mast3r/stereo_scale_multisecond_report.json",
)


def report(path: Path, result: str, **extra):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"result": result, **extra}), encoding="utf-8")


def baseline(tmp_path, *, dense_result="PASS", failures=None, complete=True):
    root = tmp_path / "baseline"
    for name in STEREO_STAGES:
        report(root / name, "PASS", external_ground_truth_used=False, slam_supervision=False)
    report(
        root / "mast3r/stereo_scale_dense10hz_report.json",
        dense_result,
        failures=failures or [],
        external_ground_truth_used=False, slam_supervision=False,
    )
    if complete:
        report(
            root / "input_quality_report.json", "PASS",
            external_ground_truth_used=False, slam_supervision=False,
        )
        (root / "trajectory_fused.csv").write_text("baseline\n", encoding="utf-8")
    return root


def rescue(tmp_path, *, graph_result="PASS"):
    root = tmp_path / "rescue"
    for name in (
        "stereo_scale_bidirectional_report.json",
        "stereo_scale_long_hops_report.json",
        "stereo_scale_dense10hz_report.json",
        "stereo_scale_multisecond_report.json",
        "imu_scale_report.json",
        "graph_fusion_report.json",
    ):
        report(
            root / "mast3r" / name,
            graph_result if name == "graph_fusion_report.json" else "PASS",
            external_ground_truth_used=False,
            slam_supervision=False,
            **({"trajectory_continuity": {"result": "PASS", "unverified_gap_count": 0}}
               if name == "stereo_scale_dense10hz_report.json" else {}),
        )
    report(root / "fusion_report.json", "PASS", external_ground_truth_used=False, slam_supervision=False)
    report(root / "input_quality_report.json", "PASS", external_ground_truth_used=False, slam_supervision=False)
    (root / "trajectory_fused.csv").write_text("rescue\n", encoding="utf-8")
    return root


def test_healthy_baseline_wins_even_when_rescue_exists(tmp_path):
    original = baseline(tmp_path)
    candidate = rescue(tmp_path)
    assert decide(original) == "baseline"
    assert select(original, candidate) == original / "trajectory_fused.csv"


def test_dense_dispersion_can_rescue_only_with_complete_internal_pass(tmp_path):
    original = baseline(
        tmp_path, dense_result="FAIL", complete=False,
        failures=["stereo scale dispersion too high: relative_p90_p10=0.849"],
    )
    candidate = rescue(tmp_path)
    assert decide(original) == "rescue"
    assert select(original, candidate) == candidate / "trajectory_fused.csv"
    report(candidate / "mast3r/graph_fusion_report.json", "FAIL")
    with pytest.raises(ValueError, match="graph_fusion_report"):
        select(original, candidate)


def test_other_baseline_failures_are_not_hidden(tmp_path):
    original = baseline(tmp_path, dense_result="FAIL", complete=False, failures=["tracker missing"])
    with pytest.raises(ValueError, match="not eligible"):
        decide(original)


@pytest.mark.parametrize("stage_index", range(4))
def test_first_failed_stereo_stage_can_rescue_without_downstream_reports(tmp_path, stage_index):
    root = tmp_path / "early_failure"
    for name in STEREO_STAGES[:stage_index]:
        report(root / name, "PASS", external_ground_truth_used=False, slam_supervision=False)
    report(
        root / STEREO_STAGES[stage_index], "FAIL",
        failures=["stereo scale dispersion too high: relative_p90_p10=0.523"],
        external_ground_truth_used=False, slam_supervision=False,
    )
    assert decide(root) == "rescue"
    assert select(root, rescue(tmp_path)) == tmp_path / "rescue/trajectory_fused.csv"


def test_missing_report_is_not_evidence_of_scale_failure(tmp_path):
    root = tmp_path / "missing"
    root.mkdir()
    with pytest.raises(ValueError, match="missing stereo stage"):
        decide(root)


def test_later_dispersion_does_not_hide_earlier_non_scale_failure(tmp_path):
    root = baseline(tmp_path, dense_result="FAIL", complete=False,
                    failures=["stereo scale dispersion too high: relative_p90_p10=0.523"])
    report(root / STEREO_STAGES[0], "FAIL", failures=["insufficient stereo observations"],
           external_ground_truth_used=False, slam_supervision=False)
    with pytest.raises(ValueError, match="not eligible"):
        decide(root)


def test_dispersion_failure_requires_internal_only_provenance(tmp_path):
    root = baseline(tmp_path, dense_result="FAIL", complete=False,
                    failures=["stereo scale dispersion too high: relative_p90_p10=0.523"])
    report(root / DENSE, "FAIL", failures=["stereo scale dispersion too high: relative_p90_p10=0.523"],
           external_ground_truth_used=True, slam_supervision=False)
    with pytest.raises(ValueError, match="provenance"):
        decide(root)


def test_scale_failure_with_visual_gap_is_not_rescued(tmp_path):
    root = tmp_path / "gapped"
    report(root / STEREO_STAGES[0], "FAIL",
           failures=["stereo scale dispersion too high: relative_p90_p10=0.523"],
           external_ground_truth_used=False, slam_supervision=False,
           trajectory_continuity={"result": "FAIL", "unverified_gap_count": 1})
    with pytest.raises(ValueError, match="unverified visual gap"):
        decide(root)


def test_multiple_failures_are_not_treated_as_sole_scale_failure(tmp_path):
    root = tmp_path / "multiple"
    report(root / STEREO_STAGES[0], "FAIL",
           failures=["stereo scale dispersion too high: relative_p90_p10=0.523", "invalid timestamps"],
           external_ground_truth_used=False, slam_supervision=False)
    with pytest.raises(ValueError, match="not eligible"):
        decide(root)


def test_rescue_with_unverified_visual_gap_is_not_published(tmp_path):
    original = baseline(
        tmp_path, dense_result="FAIL", complete=False,
        failures=["stereo scale dispersion too high: relative_p90_p10=0.642"],
    )
    candidate = rescue(tmp_path)
    report(
        candidate / DENSE, "PASS", external_ground_truth_used=False,
        slam_supervision=False,
        trajectory_continuity={"result": "PASS", "unverified_gap_count": 1, "max_gap_s": 0.733},
    )
    with pytest.raises(ValueError, match="unverified visual gap"):
        select(original, candidate)


def test_internal_pass_without_final_baseline_is_not_silently_rescued(tmp_path):
    original = baseline(tmp_path, complete=False)
    with pytest.raises(ValueError, match="incomplete"):
        decide(original)


def test_cli_publishes_baseline_without_running_or_reading_rescue(tmp_path):
    original = baseline(tmp_path)
    output = tmp_path / "selected.csv"
    selection = tmp_path / "selection.json"
    script = Path(__file__).resolve().parents[1] / "scripts/select_mast3r_metric_rescue.py"
    subprocess.run(
        [sys.executable, str(script), "--baseline-dir", str(original),
         "--rescue-dir", str(tmp_path / "missing"), "--output", str(output),
         "--report", str(selection)],
        check=True,
    )
    assert output.read_text(encoding="utf-8") == "baseline\n"
    assert json.loads(selection.read_text(encoding="utf-8"))["selected"] == "baseline"
