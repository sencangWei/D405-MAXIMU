from pathlib import Path
import json
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_mast3r_tracking_input_capture as runner


def test_prefix_keeps_all_prior_inputs_and_one_successor():
    targets, prefix = runner.capture_bounds([577, 576, 577], 1199)
    assert targets == [576, 577]
    assert prefix == 579


@pytest.mark.parametrize("ids", [[], [-1], [1199], [True], [1.2]])
def test_invalid_capture_indices_are_rejected(ids):
    with pytest.raises(ValueError):
        runner.capture_bounds(ids, 1199)


def test_running_producer_blocks_capture_instead_of_parallel_model(monkeypatch):
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout="6178, /usr/libexec/gnome-remote-desktop-daemon\n3518306, /venv/bin/python\n"))
    with pytest.raises(RuntimeError, match="GPU producer"):
        runner.require_idle_gpu()


def test_display_only_is_not_a_model_producer(monkeypatch):
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout="6178, /usr/libexec/gnome-remote-desktop-daemon\n"))
    runner.require_idle_gpu()


def test_between_models_live_queue_still_blocks_capture(monkeypatch):
    def subprocess_result(command, **kwargs):
        return SimpleNamespace(stdout=("3491727 python scripts/run_metric_joint_fast10.py --run\n"
                                       if command[0] == "ps" else ""))
    monkeypatch.setattr(runner.subprocess, "run", subprocess_result)
    with pytest.raises(RuntimeError, match="queue is active"):
        runner.require_idle_gpu()


def test_capture_command_uses_original_entry_and_config_without_solver_switch(tmp_path):
    source = {"config": "/frozen/effective_config.yaml", "checkpoint": "/model.pth"}
    cmd = runner.capture_command(tmp_path, source, [576, 577], 579)
    assert cmd[cmd.index("--prefix-count") + 1] == "579"
    assert cmd[cmd.index("--") + 1] == str(runner.TOOL / "main.py")
    assert cmd[cmd.index("--config") + 1] == source["config"]
    assert cmd[cmd.index("--checkpoint") + 1] == source["checkpoint"]
    assert "--require-complete" not in cmd
    assert "metric_relative_joint" not in " ".join(cmd)


def _report(tmp_path, **updates):
    capture = tmp_path / "captures"
    capture.mkdir()
    payload = capture / "frame_577_attempt_0.pt"
    payload.write_bytes(b"snapshot")
    report = {"schema": "umi_mast3r_tracking_input_capture_v1", "status": "CAPTURED_NOT_SCORED",
              "external_ground_truth_used": False, "precision_pass": False, "production_promoted": False,
              "requested_frame_ids": [577], "missing_frame_ids": [],
              "captures": [{"frame_id": 577, "attempt": 0, "path": payload.name, "status": "CAPTURED"}]}
    report.update(updates)
    (capture / "summary.json").write_text(json.dumps(report))
    return capture


def test_only_actual_requested_capture_files_are_accepted(tmp_path):
    capture = _report(tmp_path)
    result = runner.validate_capture(capture, [577])
    assert list(result) == ["frame_577_attempt_0.pt"]
    assert len(result["frame_577_attempt_0.pt"]) == 64


@pytest.mark.parametrize("updates", [
    {"status": "INCOMPLETE"}, {"precision_pass": True}, {"external_ground_truth_used": True},
    {"requested_frame_ids": [578]}, {"missing_frame_ids": [577]}, {"captures": []},
    {"captures": [{"frame_id": 577, "path": "../outside.pt", "status": "CAPTURED"}]},
    {"captures": [{"frame_id": 577, "path": "absent.pt", "status": "CAPTURED"}]},
    {"captures": [{"frame_id": 577, "path": "frame_577_attempt_0.pt", "status": "ERROR"}]},
])
def test_incomplete_unbound_or_unsafe_capture_not_relabelled_as_success(tmp_path, updates):
    capture = _report(tmp_path, **updates)
    with pytest.raises((ValueError, FileNotFoundError)):
        runner.validate_capture(capture, [577])


def test_preflight_failure_is_preserved_without_starting_model(monkeypatch, tmp_path):
    source_run = tmp_path / "source"
    source_run.mkdir()
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    output = tmp_path / "capture"
    source = {"config": str(tmp_path / "config.yaml"), "checkpoint": str(tmp_path / "model.pth")}
    monkeypatch.setattr(runner, "bound_source", lambda _p: (source, {"dataset": str(dataset)}, 1199))
    monkeypatch.setattr(runner, "snapshot", lambda _p: {})
    monkeypatch.setattr(runner, "require_idle_gpu", lambda: None)
    def failed_runtime(*args):
        raise ValueError("changed raw source")
    monkeypatch.setattr(runner, "Runtime", failed_runtime)
    assert runner.main(["--source-run", str(source_run), "--output", str(output),
                        "--frame-id", "577", "--run"]) == 2
    manifest = json.loads((output / "capture_run_manifest.json").read_text())
    assert manifest["status"] == "CAPTURE_FAILED"
    assert "changed raw source" in manifest["error"]
    assert manifest["precision_pass"] is False


def test_dry_run_validates_source_but_never_creates_output_or_model(monkeypatch, tmp_path):
    source_run = tmp_path / "source"
    source_run.mkdir()
    output = tmp_path / "capture"
    source = {"config": "/config.yaml", "checkpoint": "/model.pth"}
    monkeypatch.setattr(runner, "bound_source", lambda _p: (source, {}, 1199))
    monkeypatch.setattr(runner, "snapshot", lambda _p: {})
    monkeypatch.setattr(runner, "require_idle_gpu", lambda: pytest.fail("must not reserve GPU"))
    assert runner.main(["--source-run", str(source_run), "--output", str(output), "--frame-id", "577"]) == 0
    assert not output.exists()


def image_hash_fixture(tmp_path):
    import torch
    native, paired, captures = (tmp_path / name for name in ("native", "paired", "captures"))
    for directory in (native, paired, paired / "right", captures):
        directory.mkdir(parents=True, exist_ok=True)
    rows = [{"image": f"{fid}.png"} for fid in range(3)]
    for fid in range(3):
        for directory in (native, paired, paired / "right"):
            (directory / f"{fid}.png").write_bytes(bytes([fid + 1]))
    def identity(fid):
        return tuple((p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ctime_ns)
                     for p in (native / rows[fid]["image"], paired / rows[fid]["image"], paired / "right" / rows[fid]["image"]))
    runtime = SimpleNamespace(native=native, paired=paired, right_directory="right", rows=rows, image_identity=identity)
    torch.save({"track_entry": {"frame_id": 2, "reference_frame_id": 0}, "get_points_poses": {"frame_id": 2, "keyframe_id": 0}}, captures / "capture.pt")
    return runtime, captures, {"capture.pt": runner.sha(captures / "capture.pt")}, {fid: identity(fid) for fid in range(3)}


def test_capture_binds_only_actual_current_reference_stereo_images(tmp_path):
    runtime, captures, hashes, identities = image_hash_fixture(tmp_path)
    frame_ids, image_hashes = runner.capture_image_hashes(runtime, captures, hashes, identities)
    assert frame_ids == [0, 2]
    assert len(image_hashes) == 6
    assert all(Path(path).name in ("0.png", "2.png") and digest == runner.sha(Path(path))
               for path, digest in image_hashes.items())


def test_capture_rejects_raw_image_changed_after_recording(tmp_path):
    runtime, captures, hashes, identities = image_hash_fixture(tmp_path)
    (runtime.paired / "right/2.png").write_bytes(b"changed")
    with pytest.raises(ValueError, match="raw image changed"):
        runner.capture_image_hashes(runtime, captures, hashes, identities)


def test_capture_rejects_image_changed_during_hash(monkeypatch, tmp_path):
    runtime, captures, hashes, identities = image_hash_fixture(tmp_path)
    original_sha = runner.sha
    def changing_sha(path):
        digest = original_sha(path)
        if Path(path).name == "2.png":
            Path(path).write_bytes(b"changed while hashing")
        return digest
    monkeypatch.setattr(runner, "sha", changing_sha)
    with pytest.raises(ValueError, match="raw image changed"):
        runner.capture_image_hashes(runtime, captures, hashes, identities)


def test_capture_rejects_reference_outside_original_prefix(tmp_path):
    import torch
    runtime, captures, hashes, identities = image_hash_fixture(tmp_path)
    torch.save({"track_entry": {"frame_id": 2, "reference_frame_id": 99}, "get_points_poses": {"frame_id": 2, "keyframe_id": 99}}, captures / "capture.pt")
    hashes["capture.pt"] = runner.sha(captures / "capture.pt")
    with pytest.raises(ValueError, match="prefix"):
        runner.capture_image_hashes(runtime, captures, hashes, identities)
