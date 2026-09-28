import importlib.util
import json
from pathlib import Path

import pytest


PATH = Path(__file__).resolve().parents[1]/".planning/metric_window_bundle_20260928/summarize_frontend_match_probe.py"
SPEC = importlib.util.spec_from_file_location("frontend_probe_summary", PATH)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_valid_counts_and_missing_rows_are_not_zero(tmp_path):
    path = tmp_path/"matches.csv"
    path.write_text("frame_id,n_match,n_match_Q,n_opt,n_total\n1,8,6,4,10\n3,9,7,5,10\n")
    rows = module.read_matches(path, 4)
    result = module.summarize_rows(rows, 0, 3)
    assert result["missing_frame_ids"] == [0, 2]
    assert result["optimization_fraction"]["median"] == pytest.approx(0.45)
    assert result["optimization_fraction"]["n"] == 2


@pytest.mark.parametrize("line", ["1,8,6,9,10", "1,8,9,4,10", "1,8,6,4,0", "4,8,6,4,10"])
def test_invalid_count_or_index_is_rejected(tmp_path, line):
    path = tmp_path/"matches.csv"
    path.write_text("frame_id,n_match,n_match_Q,n_opt,n_total\n"+line+"\n")
    with pytest.raises(ValueError):
        module.read_matches(path, 4)


def test_duplicate_indices_are_rejected(tmp_path):
    path = tmp_path/"matches.csv"
    path.write_text("frame_id,n_match,n_match_Q,n_opt,n_total\n1,8,6,4,10\n1,8,6,4,10\n")
    with pytest.raises(ValueError):
        module.read_matches(path, 4)


@pytest.fixture
def probe_fixture(tmp_path):
    frozen, probe = tmp_path/"frozen", tmp_path/"probe"
    frozen.mkdir()
    probe.mkdir()
    (frozen/"dataset").mkdir()
    (probe/"dataset").symlink_to(frozen/"dataset", target_is_directory=True)
    manifest = {key: "same" for key in ("config_sha256", "toolchain_commit",
        "toolchain_dirty_diff_sha256", "lietorch_commit", "checkpoint_sha256")}
    manifest["elapsed_s"] = 3
    poses = "t_sec,x,y,z,qw,qx,qy,qz\n0,0,0,0,1,0,0,0\n1,1,0,0,1,0,0,0\n"
    for directory in (frozen, probe):
        (directory/"run_manifest.json").write_text(json.dumps(manifest))
        (directory/"trajectory_frames.csv").write_text(poses)
    (probe/"match_log.csv").write_text("frame_id,n_match,n_match_Q,n_opt,n_total\n1,8,6,4,10\n")
    (probe/"frontend_log.csv").write_text("frame_id,keyframe_id,n_opt,n_total,pointmap_z_current,metric_pointmap_scale,metric_relative_mad,metric_points,relative_scale\n1,0,4,10,1,nan,nan,0,1\n")
    return probe, frozen


def test_analyze_binds_identity_source_and_missing_diagnostics(probe_fixture):
    result = module.analyze(*probe_fixture)
    assert result["replay_trajectory_identical"] is True
    assert result["raw_csv_byte_identical"] is True
    assert result["identity_limitation"] is None
    assert result["source_sha256"][str(PATH)] == module.digest(PATH)
    assert result["match_logging_whole_run"]["missing_frame_ids"] == [0]
    assert result["frontend_fixed_windows"][0]["statistics"]["metric_pointmap_scale"] is None


def test_changed_producer_is_rejected(probe_fixture):
    probe, frozen = probe_fixture
    path = probe/"run_manifest.json"
    manifest = json.loads(path.read_text())
    manifest["checkpoint_sha256"] = "changed"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="producer mismatch"):
        module.analyze(probe, frozen)


@pytest.mark.parametrize("change", ["timestamp", "coverage"])
def test_changed_pose_binding_is_rejected(probe_fixture, change):
    probe, frozen = probe_fixture
    path = probe/"trajectory_frames.csv"
    text = path.read_text()
    path.write_text(text.replace("1,1,0", "2,1,0") if change == "timestamp"
                    else "\n".join(text.splitlines()[:-1])+"\n")
    with pytest.raises(ValueError, match="coverage/timestamps"):
        module.analyze(probe, frozen)


def test_changed_pose_is_reported_not_original_causal_proof(probe_fixture):
    probe, frozen = probe_fixture
    path = probe/"trajectory_frames.csv"
    path.write_text(path.read_text().replace("1,1,0", "1,2,0"))
    result = module.analyze(probe, frozen)
    assert result["original_trajectory_reproduced"] is False
    assert "cannot" in result["identity_limitation"]
    assert result["replay_max_position_delta_mast3r_units"] == 1


def test_frontend_counts_must_match_match_log(probe_fixture):
    probe, frozen = probe_fixture
    path = probe/"frontend_log.csv"
    path.write_text(path.read_text().replace("1,0,4,10", "1,0,3,10"))
    with pytest.raises(ValueError, match="counts differ"):
        module.analyze(probe, frozen)
