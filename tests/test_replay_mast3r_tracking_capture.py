import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest
import torch


MODULE = Path(__file__).resolve().parents[1] / "scripts/replay_mast3r_tracking_capture.py"


def import_module():
    spec = importlib.util.spec_from_file_location(f"replay_mast3r_tracking_capture_{id(object())}", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeSim3:
    def __init__(self, data):
        self.data = data

    def inv(self):
        out = self.data.clone()
        out[..., :3] = -out[..., :3]
        return type(self)(out)

    def __mul__(self, other):
        out = self.data.clone()
        out[..., :3] = out[..., :3] + other.data[..., :3]
        return type(self)(out)

    def retr(self, tau):
        out = self.data.clone()
        out[..., : tau.shape[-1]] = out[..., : tau.shape[-1]] + tau
        return type(self)(out)


class FakeTracker:
    def solve_pose_increment(self, sqrt_info, r, J, visual_row_count=0, visual_effective_count=1):
        tau = torch.zeros(1, 8, dtype=r.dtype)
        tau[..., 0] = r.sum() * 0.1
        return tau, float((r * r).sum().item())

    def opt_pose_calib_sim3(
        self, Xf, Xk, T_WCf, T_WCk, Qk, valid, conf_w, meas_k, valid_meas_k, K, img_size,
        metric_translation_target=None, metric_world_scale=1.0,
    ):
        local = T_WCk.inv() * T_WCf
        for _step in range(int(self.cfg["max_iters"])):
            tau, _cost = self.solve_pose_increment(conf_w, meas_k - Xf[:, :3], Xf)
            local = local.retr(tau)
        return T_WCk * local, local


def payload_for_frame(frame_id=5):
    dtype = torch.float64
    T_f = {"class": "Sim3", "data": torch.tensor([[0.1, 0, 0, 0, 0, 0, 1, 1]], dtype=dtype)}
    T_k = {"class": "Sim3", "data": torch.tensor([[1.0, 0, 0, 0, 0, 0, 1, 1]], dtype=dtype)}
    opt = {
        "Xf": torch.ones(4, 3, dtype=dtype),
        "Xk": torch.ones(4, 3, dtype=dtype),
        "T_WCf": T_f,
        "T_WCk": T_k,
        "Qk": torch.ones(4, 1, dtype=dtype),
        "valid": torch.ones(4, 1, dtype=torch.bool),
        "conf_w": torch.ones(4, 1, dtype=dtype),
        "meas": torch.full((4, 3), 2.0, dtype=dtype),
        "valid_meas": torch.ones(4, 1, dtype=torch.bool),
        "K": torch.eye(3, dtype=dtype),
        "img_size": (3, 4),
        "metric_translation_target": None,
        "metric_world_scale": 1.0,
    }
    expected = FakeTracker.__new__(FakeTracker)
    expected.cfg = {"max_iters": 2}
    world, local = FakeTracker.opt_pose_calib_sim3(
        expected,
        opt["Xf"], opt["Xk"], FakeSim3(T_f["data"].clone()), FakeSim3(T_k["data"].clone()),
        opt["Qk"], opt["valid"], opt["conf_w"], opt["meas"], opt["valid_meas"],
        opt["K"], opt["img_size"],
    )
    opt["return"] = {
        "T_WCf": {"class": "Sim3", "data": world.data.clone()},
        "T_CkCf": {"class": "Sim3", "data": local.data.clone()},
    }
    opt_solve = []
    replay = FakeTracker.__new__(FakeTracker)
    replay.cfg = {"max_iters": 2}
    local = FakeSim3(T_k["data"].clone()).inv() * FakeSim3(T_f["data"].clone())
    for _ in range(2):
        tau, cost = FakeTracker.solve_pose_increment(replay, opt["conf_w"], opt["meas"] - opt["Xf"][:, :3], opt["Xf"])
        opt_solve.append({"cost": cost, "tau": tau.clone()})
        local = local.retr(tau)
    return {
        "schema": "mast3r_tracking_input_capture_v1",
        "status": "ok",
        "track_entry": {"frame_id": frame_id, "cfg": {"max_iters": 2}},
        "opt_pose_calib_sim3": opt,
        "solve_pose_increment": opt_solve,
    }


def test_fake_cpu_replay_repeats_exact_and_compares_to_captured_return():
    module = import_module()
    report = module.replay_twice(payload_for_frame(), FakeTracker, FakeSim3)
    assert report["status"] == "DIAGNOSTIC_REPLAY_COMPLETE_NOT_SCORED"
    assert report["repeat_exact"] is True
    assert report["precision_pass"] is False
    assert report["external_ground_truth_used"] is False
    assert len(report["runs"][0]["iterations"]) == 2
    diffs = report["runs"][0]["iteration_diffs_vs_captured_observer"]
    assert diffs["actual_count"] == 2
    assert diffs["captured_count"] == 2
    assert diffs["count_match"] is True
    assert diffs["matched_diffs"][0]["tau_max_abs_diff"] == 0.0
    assert report["runs"][0]["world_pose_diff_vs_captured_opt_return"] == 0.0
    assert report["runs"][0]["local_pose_diff_vs_captured_opt_return"] == 0.0


def test_increment_comparison_reports_count_mismatch_without_truncating():
    module = import_module()
    actual = [{"cost": 1.0, "tau": torch.zeros(1, 8)}, {"cost": 2.0, "tau": torch.ones(1, 8)}]
    captured = [{"cost": 1.0, "tau": torch.zeros(1, 8)}]
    report = module.compare_increments(actual, captured)
    assert report["actual_count"] == 2
    assert report["captured_count"] == 1
    assert report["count_match"] is False
    assert report["missing_captured_iterations"] == 1
    assert report["extra_captured_iterations"] == 0
    assert len(report["matched_diffs"]) == 1


def test_repeat_exact_false_when_final_pose_same_but_increment_trace_differs(monkeypatch):
    module = import_module()
    pose = torch.zeros(1, 8)
    calls = iter([
        {"world_pose": pose, "local_pose": pose, "increments": [{"cost": 1.0, "tau": torch.zeros(1, 8)}],
         "increment_diffs_vs_capture": {}, "world_pose_diff_vs_capture": 0.0, "local_pose_diff_vs_capture": 0.0},
        {"world_pose": pose, "local_pose": pose, "increments": [{"cost": 2.0, "tau": torch.zeros(1, 8)}],
         "increment_diffs_vs_capture": {}, "world_pose_diff_vs_capture": 0.0, "local_pose_diff_vs_capture": 0.0},
    ])
    monkeypatch.setattr(module, "replay_once", lambda *_args: next(calls))
    report = module.replay_twice(payload_for_frame(), FakeTracker, FakeSim3)
    assert report["repeat_exact"] is False


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda p: p.update(status="error"), "incomplete"),
        (lambda p: p["track_entry"].update(frame_id=99), "frame_id"),
        (lambda p: p["opt_pose_calib_sim3"].update(error={"type": "x"}), "lacks"),
        (lambda p: p["opt_pose_calib_sim3"].update(metric_translation_target=torch.ones(3)), "unsupported"),
    ],
)
def test_capture_payload_validation_rejects_incomplete_or_wrong_frame(mutate, match):
    module = import_module()
    payload = payload_for_frame()
    mutate(payload)
    with pytest.raises(ValueError, match=match):
        module.validate_capture_payload(payload, 5)


def test_torch_load_uses_weights_only(monkeypatch, tmp_path):
    module = import_module()
    called = {}

    def fake_load(path, map_location=None, weights_only=None):
        called["args"] = (path, map_location, weights_only)
        return {}

    fake_torch = types.SimpleNamespace(load=fake_load)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    module.load_torch_snapshot(tmp_path / "x.pt")
    assert called["args"][2] is True


def test_output_json_refuses_overwrite(tmp_path):
    module = import_module()
    output = tmp_path / "out.json"
    output.write_text("old", encoding="utf-8")
    with pytest.raises(FileExistsError):
        module.write_json_new(output, {"x": 1})
    assert output.read_text(encoding="utf-8") == "old"


def test_validate_manifest_inputs_detects_changed_file(tmp_path):
    module = import_module()
    source = tmp_path / "source.txt"
    source.write_text("before", encoding="utf-8")
    manifest = {
        "input_sha256": {str(source.resolve()): module.sha256(source)}
    }
    source.write_text("after", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        module.validate_manifest_inputs(manifest)


def install_fake_adapter(monkeypatch, code_path, context=None):
    adapter = types.ModuleType("experimental_mast3r_metric_joint_adapter")
    adapter.ROOT = Path(__file__).resolve().parents[1]
    adapter.TOOL = Path("/tmp/fake-tool")
    adapter.SCHEMA = "umi_metric_relative_joint_frontend_context_v1"
    adapter.Runtime = object
    adapter.CODE_PATHS = {"adapter": code_path}
    adapter.sha = lambda path: import_module().sha256(Path(path))
    adapter.context_for_source = lambda dataset, paired, eye: context or {
        "dataset": str(dataset), "paired_left_dataset": str(paired), "eye": eye
    }
    monkeypatch.setitem(sys.modules, "experimental_mast3r_metric_joint_adapter", adapter)
    return adapter


def write_capture_run(tmp_path, module, frame_ids=(5, 6), status="TRACKING_INPUTS_CAPTURED_NOT_SCORED"):
    run = tmp_path / "run"
    captures = run / "captures"
    captures.mkdir(parents=True)
    code = tmp_path / "adapter.py"
    code.write_text("adapter", encoding="utf-8")
    native = tmp_path / "dataset"
    paired = tmp_path / "paired"
    native.mkdir()
    paired.mkdir()
    input_sha256 = {}
    for prefix, directory in (("native_", native), ("paired_", paired)):
        for key, filename in (("manifest", "dataset_manifest.json"), ("frames", "frames.csv"), ("calibration", "calibration.yaml")):
            path = directory / filename
            path.write_text(f"{prefix}{key}", encoding="utf-8")
            input_sha256[prefix + key] = module.sha256(path)
    context = {
        "schema": "umi_metric_relative_joint_frontend_context_v1",
        "external_ground_truth_used": False,
        "dataset": str(tmp_path / "dataset"),
        "paired_left_dataset": str(tmp_path / "paired"),
        "eye": "left",
        "input_sha256": input_sha256,
        "code_sha256": {"adapter": module.sha256(code)},
    }
    manifest = {
        "schema": "umi_bound_tracking_capture_run_v1",
        "status": status,
        "precision_pass": False,
        "external_ground_truth_used": False,
        "production_promoted": False,
        "requested_frame_ids": list(frame_ids),
        "source_context": context,
        "input_sha256": {str(code.resolve()): module.sha256(code)},
    }
    (run / "capture_run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    rows = []
    for frame_id in frame_ids:
        name = f"mast3r_tracking_frame{frame_id:06d}_attempt001.pt"
        torch.save(payload_for_frame(frame_id), captures / name)
        rows.append({"frame_id": frame_id, "attempt": 1, "path": name, "status": "CAPTURED"})
    summary = {
        "schema": "umi_mast3r_tracking_input_capture_v1",
        "status": "CAPTURED_NOT_SCORED",
        "external_ground_truth_used": False,
        "precision_pass": False,
        "production_promoted": False,
        "requested_frame_ids": list(frame_ids),
        "missing_frame_ids": [],
        "captures": rows,
    }
    (captures / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    manifest["snapshot_sha256"] = {row["path"]: module.sha256(captures / row["path"]) for row in rows}
    (run / "capture_run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return run, code, context


def test_load_capture_run_validates_whole_multiframe_summary_then_selects(monkeypatch, tmp_path):
    module = import_module()
    run, code, context = write_capture_run(tmp_path, module, frame_ids=(5, 6, 7))
    install_fake_adapter(monkeypatch, code, context)
    _manifest, snapshot = module.load_capture_run(run, 6)
    assert snapshot.name == "mast3r_tracking_frame000006_attempt001.pt"


def test_cli_requires_frame_id_for_multiframe_capture(monkeypatch, tmp_path):
    module = import_module()
    run, code, context = write_capture_run(tmp_path, module, frame_ids=(5, 6))
    install_fake_adapter(monkeypatch, code, context)
    with pytest.raises(ValueError, match="--frame-id"):
        module.main(["--capture-run", str(run), "--output", str(tmp_path / "out.json")])


def test_cli_rejects_bad_schema_and_capture_error(monkeypatch, tmp_path):
    module = import_module()
    run, code, context = write_capture_run(tmp_path, module, frame_ids=(5,))
    install_fake_adapter(monkeypatch, code, context)
    manifest_path = run / "capture_run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["schema"] = "wrong"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        module.main(["--capture-run", str(run), "--output", str(tmp_path / "out.json"), "--frame-id", "5"])
    manifest["schema"] = "umi_bound_tracking_capture_run_v1"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    summary_path = run / "captures/summary.json"
    summary = json.loads(summary_path.read_text())
    summary["captures"][0]["status"] = "ERROR"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(ValueError, match="failed|incomplete|unbound"):
        module.main(["--capture-run", str(run), "--output", str(tmp_path / "out2.json"), "--frame-id", "5"])


def test_load_capture_run_rejects_snapshot_hash_mismatch(monkeypatch, tmp_path):
    module = import_module()
    run, code, context = write_capture_run(tmp_path, module, frame_ids=(5,))
    install_fake_adapter(monkeypatch, code, context)
    manifest_path = run / "capture_run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["snapshot_sha256"]["mast3r_tracking_frame000005_attempt001.pt"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="output snapshot hashes"):
        module.load_capture_run(run, 5)


def test_strict_source_binding_rejects_missing_adapter_and_missing_code_path(monkeypatch, tmp_path):
    module = import_module()
    run, code, context = write_capture_run(tmp_path, module, frame_ids=(5,))
    monkeypatch.delitem(sys.modules, "experimental_mast3r_metric_joint_adapter", raising=False)
    monkeypatch.setattr(module, "ROOT", tmp_path / "no-such-root")
    monkeypatch.setattr(sys, "path", [])
    with pytest.raises(ModuleNotFoundError):
        module.load_capture_run(run, 5)
    install_fake_adapter(monkeypatch, tmp_path / "missing.py", context)
    monkeypatch.setattr(module, "ROOT", Path(__file__).resolve().parents[1])
    with pytest.raises(ValueError, match="binding missing"):
        module.load_capture_run(run, 5)


def test_source_binding_uses_context_code_sha256_not_top_level_input(monkeypatch, tmp_path):
    module = import_module()
    run, code, context = write_capture_run(tmp_path, module, frame_ids=(5,))
    install_fake_adapter(monkeypatch, code, context)
    manifest_path = run / "capture_run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["input_sha256"] = {}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _manifest, snapshot = module.load_capture_run(run, 5)
    assert snapshot.name == "mast3r_tracking_frame000005_attempt001.pt"


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda c: c.pop("dataset"), "fields/schema"),
        (lambda c: c.update(eye="center"), "eye"),
        (lambda c: c.update(schema="wrong"), "fields/schema"),
        (lambda c: c.update(external_ground_truth_used=True), "ground truth"),
    ],
)
def test_source_context_requires_exact_runtime_shape(monkeypatch, tmp_path, mutate, match):
    module = import_module()
    run, code, context = write_capture_run(tmp_path, module, frame_ids=(5,))
    install_fake_adapter(monkeypatch, code, context)
    manifest_path = run / "capture_run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    mutate(manifest["source_context"])
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        module.load_capture_run(run, 5)


def test_installed_original_cpu_smoke():
    module = import_module()
    tool = Path("/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM")
    if str(tool) not in sys.path:
        sys.path.insert(0, str(tool))
    try:
        import lietorch
        from mast3r_slam.tracker import FrameTracker
        from mast3r_slam.geometry import project_calib
    except (ModuleNotFoundError, ImportError) as error:
        pytest.skip(f"native CPU dependencies unavailable: {error}")
    dtype = torch.float64
    torch.manual_seed(0)
    X = torch.randn(128, 3, dtype=dtype)
    X[:, 2] = X[:, 2].abs() + 3.0
    K = torch.eye(3, dtype=dtype)
    meas, _J, valid_proj = project_calib(X, K, (64, 64), jacobian=True, border=0, z_eps=1e-6)
    initial = lietorch.Sim3.Identity(1, dtype=dtype).data.cpu()
    initial[..., 0] = 0.01
    expected_identity = lietorch.Sim3.Identity(1, dtype=dtype).data.cpu()
    payload = {
        "schema": "mast3r_tracking_input_capture_v1",
        "status": "ok",
        "track_entry": {
            "frame_id": 0,
            "cfg": {
                "sigma_pixel": 1.0,
                "sigma_depth": 1.0,
                "max_iters": 3,
                "pixel_border": 0,
                "depth_eps": 1e-6,
                "rel_error": 1e-7,
                "delta_norm": 1e-7,
                "huber": 1.345,
                "stereo_fix_pose_scale": False,
            },
        },
        "opt_pose_calib_sim3": {
            "Xf": X,
            "Xk": X,
            "T_WCf": {"class": "Sim3", "data": initial},
            "T_WCk": {"class": "Sim3", "data": expected_identity},
            "Qk": torch.ones(128, 1, dtype=dtype),
            "valid": valid_proj.cpu(),
            "conf_w": torch.ones(128, 1, dtype=dtype),
            "meas": meas.detach().cpu(),
            "valid_meas": torch.ones(128, 1, dtype=torch.bool),
            "K": K,
            "img_size": (64, 64),
            "metric_translation_target": None,
            "metric_world_scale": 1.0,
            "return": {
                "T_WCf": {"class": "Sim3", "data": expected_identity},
                "T_CkCf": {"class": "Sim3", "data": expected_identity},
            },
        },
    }
    report = module.replay_twice(payload, FrameTracker, lietorch.Sim3)
    assert report["status"] == "DIAGNOSTIC_REPLAY_COMPLETE_NOT_SCORED"
    assert report["repeat_exact"] is True
    assert len(report["runs"][0]["iterations"]) >= 1
    assert report["runs"][0]["world_pose_diff_vs_captured_opt_return"] < 1e-4
