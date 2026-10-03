import importlib.util
import sys
from pathlib import Path
from types import MethodType

import numpy as np
import pytest
import torch


BASE = Path(__file__).resolve().parents[1] / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
MODULE = BASE / "local_tracking_metric_candidate.py"
TOOL = Path("/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM")


def import_candidate():
    spec = importlib.util.spec_from_file_location(f"local_tracking_metric_candidate_{id(object())}", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def factor(info=None, source=10, target=9, accepted=True):
    return {
        "source_index": 0,
        "target_index": 1,
        "source_frame_id": source,
        "target_frame_id": target,
        "measurement": {
            "translation_native": [0.0, 0.0, 0.0],
            "rotation_quat_xyzw": [0.0, 0.0, 0.0, 1.0],
            "scale_ratio": 1.0,
        },
        "information": (np.eye(7) if info is None else info).tolist(),
        "pnp_report": {"accepted": accepted},
    }


class FakePose:
    def __init__(self, data):
        self.data = data

    def inv(self):
        out = self.data.clone()
        out[:, :3] *= -1
        out[:, 7] = 1.0 / out[:, 7]
        return type(self)(out)

    def __mul__(self, other):
        out = self.data.clone()
        out[:, :3] += other.data[:, :3]
        out[:, 7] *= other.data[:, 7]
        return type(self)(out)

    def retr(self, tau):
        out = self.data.clone()
        out[:, :3] += tau[:, :3]
        out[:, 7] *= torch.exp(tau[:, 6])
        return type(self)(out)


class RaisingTracker:
    cfg = {"huber": 1.345, "stereo_fix_pose_scale": False}

    def solve_pose_increment(self, *args, **kwargs):
        raise AssertionError("instance method should be restored")

    def opt_pose_calib_sim3(self, *args, **kwargs):
        tau, _cost = self.solve_pose_increment(
            torch.ones(1, 7), torch.ones(1, 7), torch.eye(7).reshape(1, 7, 7)
        )
        raise RuntimeError("boom after wrapped solve")


class SuccessTracker(RaisingTracker):
    def opt_pose_calib_sim3(self, *args, **kwargs):
        tau, _cost = self.solve_pose_increment(
            torch.ones(1, 7), torch.ones(1, 7), torch.eye(7).reshape(1, 7, 7)
        )
        pose = FakePose(torch.zeros(1, 8, dtype=tau.dtype))
        return pose, pose


def patch_fake_native(monkeypatch, module):
    def embed(tau7, fixed_scale):
        tau = torch.zeros(1, 8, dtype=tau7.dtype)
        tau[:, :7] = tau7
        return tau

    monkeypatch.setattr(
        module,
        "_native_helpers",
        lambda: (lambda r, k=1.345: torch.ones_like(r), embed, lambda J, fixed_scale: J),
    )


def test_method_restored_when_original_opt_raises(monkeypatch):
    module = import_candidate()
    patch_fake_native(monkeypatch, module)
    tracker = RaisingTracker()
    original = tracker.solve_pose_increment
    pose = FakePose(torch.tensor([[0.1, 0, 0, 0, 0, 0, 1, 1.0]], dtype=torch.float64))
    with pytest.raises(RuntimeError, match="boom"):
        module.opt_pose_calib_sim3_with_metric_factor(
            tracker,
            Xf=torch.ones(1, 3),
            Xk=torch.ones(1, 3),
            T_WCf=pose,
            T_WCk=FakePose(torch.tensor([[0, 0, 0, 0, 0, 0, 1, 1.0]], dtype=torch.float64)),
            Qk=torch.ones(1, 1),
            valid=torch.ones(1, 1, dtype=torch.bool),
            conf_w=torch.ones(1, 1),
            meas_k=torch.ones(1, 7),
            valid_meas_k=torch.ones(1, 1, dtype=torch.bool),
            K=torch.eye(3),
            img_size=(4, 4),
            factor=factor(),
            current_frame_id=10,
            keyframe_id=9,
        )
    assert tracker.solve_pose_increment == original
    assert "solve_pose_increment" not in tracker.__dict__


@pytest.mark.parametrize("tracker_cls,expect_error", [(SuccessTracker, False), (RaisingTracker, True)])
def test_preexisting_instance_method_restored_on_success_and_exception(monkeypatch, tracker_cls, expect_error):
    module = import_candidate()
    patch_fake_native(monkeypatch, module)
    tracker = tracker_cls()
    calls = []

    def instance_method(*args, **kwargs):
        calls.append((args, kwargs))
        return torch.zeros(1, 8), 0.0

    tracker.solve_pose_increment = instance_method
    pose = FakePose(torch.tensor([[0.1, 0, 0, 0, 0, 0, 1, 1.0]], dtype=torch.float64))
    args = dict(
        Xf=torch.ones(1, 3),
        Xk=torch.ones(1, 3),
        T_WCf=pose,
        T_WCk=FakePose(torch.tensor([[0, 0, 0, 0, 0, 0, 1, 1.0]], dtype=torch.float64)),
        Qk=torch.ones(1, 1),
        valid=torch.ones(1, 1, dtype=torch.bool),
        conf_w=torch.ones(1, 1),
        meas_k=torch.ones(1, 7),
        valid_meas_k=torch.ones(1, 1, dtype=torch.bool),
        K=torch.eye(3),
        img_size=(4, 4),
        factor=factor(),
        current_frame_id=10,
        keyframe_id=9,
    )
    if expect_error:
        with pytest.raises(RuntimeError, match="boom"):
            module.opt_pose_calib_sim3_with_metric_factor(tracker, **args)
    else:
        module.opt_pose_calib_sim3_with_metric_factor(tracker, **args)
    assert tracker.solve_pose_increment is instance_method


@pytest.mark.parametrize(
    "bad,match",
    [
        (factor(source=9), "source_frame_id"),
        (factor(target=10), "target_frame_id"),
        (factor(accepted=False), "accepted PnP"),
        (factor(info=np.triu(np.ones((7, 7)))), "symmetric"),
        (factor(info=np.diag([1, 1, 1, 1, 1, 1, -1])), "SPD"),
        (factor(info=np.eye(7) * 1e6 + np.triu(np.ones((7, 7)) * 1e-4, 1)), "symmetric"),
    ],
)
def test_factor_validation_fails_closed(bad, match):
    module = import_candidate()
    with pytest.raises(ValueError, match=match):
        module._validate_factor(bad, 10, 9)


def test_gpu_like_tensor_rejected_before_metric_mode():
    module = import_candidate()
    with pytest.raises(ValueError, match="CPU"):
        module._check_cpu_tensor("x", torch.empty(1, device="meta"))


def test_relative_asymmetry_allowed_by_old_check_is_rejected():
    module = import_candidate()
    info = np.eye(7) * 2e6 + np.ones((7, 7)) * 1e6
    info[0, 1] += 1.0
    assert np.allclose(info, info.T, atol=1e-8)  # Former default-rtol check.
    with pytest.raises(ValueError, match="symmetric"):
        module._validate_factor(factor(info=info), 10, 9)


@pytest.mark.parametrize("dtype", [torch.float32, torch.float64])
def test_metric_terms_assemble_float64_then_cast(monkeypatch, dtype):
    module = import_candidate()
    residual = np.linspace(-0.3, 0.4, 7, dtype=np.float64)
    jac = np.reshape(np.linspace(-0.2, 0.3, 98, dtype=np.float64), (7, 14))
    base = np.reshape(np.linspace(0.1, 0.7, 49, dtype=np.float64), (7, 7))
    info = base.T @ base + np.eye(7) * 0.5
    monkeypatch.setattr(module, "factor_linearization", lambda poses, fac: (residual, jac, info))
    Hm, gm, cost = module._metric_terms(FakePose(torch.tensor([[0, 0, 0, 0, 0, 0, 1, 1.0]])), factor(), dtype)
    Jm = torch.as_tensor(jac[:, :7], dtype=torch.float64)
    rm = torch.as_tensor(residual, dtype=torch.float64).view(7, 1)
    Info = torch.as_tensor(info, dtype=torch.float64)
    assert torch.equal(Hm, (Jm.T @ Info @ Jm).to(dtype=dtype))
    assert torch.equal(gm, (-Jm.T @ Info @ rm).to(dtype=dtype))
    assert cost == pytest.approx(float(0.5 * (rm.T @ Info @ rm).item()))


def native_modules():
    if str(TOOL) not in sys.path:
        sys.path.insert(0, str(TOOL))
    try:
        import lietorch
        from mast3r_slam.tracker import FrameTracker
        from mast3r_slam.geometry import project_calib
    except (ModuleNotFoundError, ImportError) as error:
        pytest.skip(f"native CPU dependencies unavailable: {error}")
    return lietorch, FrameTracker, project_calib


def tracker():
    _lietorch, FrameTracker, _project = native_modules()
    obj = FrameTracker.__new__(FrameTracker)
    obj.cfg = {
        "sigma_pixel": 1.0,
        "sigma_depth": 1.0,
        "max_iters": 5,
        "pixel_border": 0,
        "depth_eps": 1e-6,
        "rel_error": 1e-10,
        "delta_norm": 1e-10,
        "huber": 1.345,
        "stereo_fix_pose_scale": False,
    }
    return obj


def native_inputs(dtype):
    lietorch, _FrameTracker, project_calib = native_modules()
    torch.manual_seed(4)
    X = torch.randn(160, 3, dtype=dtype)
    X[:, 2] = X[:, 2].abs() + 4.0
    K = torch.eye(3, dtype=dtype)
    identity = lietorch.Sim3.Identity(1, dtype=dtype).data.cpu()
    initial = identity.clone()
    initial[:, 0] = 0.03
    initial[:, 1] = -0.02
    initial[:, 5] = 0.04
    initial[:, 6] = float(np.sqrt(max(0.0, 1.0 - 0.04**2)))
    initial[:, 7] = float(np.exp(0.05))
    meas, _J, valid_proj = project_calib(X, K, (96, 96), jacobian=True, border=0, z_eps=1e-6)
    return {
        "Xf": X,
        "Xk": X.clone(),
        "T_WCf": lietorch.Sim3(initial),
        "T_WCk": lietorch.Sim3(identity.clone()),
        "Qk": torch.ones(160, 1, dtype=dtype),
        "valid": valid_proj.cpu(),
        "conf_w": torch.ones(160, 1, dtype=dtype),
        "meas_k": meas.detach().cpu(),
        "valid_meas_k": torch.ones(160, 1, dtype=torch.bool),
        "K": K,
        "img_size": (96, 96),
    }


@pytest.mark.parametrize("dtype", [torch.float32, torch.float64])
def test_factor_off_matches_original_pose_and_increment_trace(dtype):
    module = import_candidate()
    lietorch, _FrameTracker, _project = native_modules()
    inp = native_inputs(dtype)
    base = tracker()
    wrapped = tracker()
    base_trace = []
    wrapped_trace = []

    def record(inst, out):
        original = type(inst).solve_pose_increment

        def solve(self, *args, **kwargs):
            tau, cost = original(self, *args, **kwargs)
            out.append((tau.detach().cpu().clone(), cost))
            return tau, cost

        inst.solve_pose_increment = MethodType(solve, inst)

    record(base, base_trace)
    record(wrapped, wrapped_trace)
    expected = type(base).opt_pose_calib_sim3(base, **inp)
    got = module.opt_pose_calib_sim3_with_metric_factor(wrapped, **inp, factor=None)
    assert len(got) == 2
    assert torch.equal(expected[0].data, got[0].data)
    assert torch.equal(expected[1].data, got[1].data)
    assert len(base_trace) == len(wrapped_trace)
    assert len(base_trace) > 0
    assert torch.linalg.norm(base_trace[0][0]).item() > 0.0
    for (tau_a, cost_a), (tau_b, cost_b) in zip(base_trace, wrapped_trace):
        assert torch.equal(tau_a, tau_b)
        assert cost_a == cost_b
    assert isinstance(got[1], lietorch.Sim3)


@pytest.mark.parametrize("dtype", [torch.float32, torch.float64])
def test_metric_factor_signed_translation_and_scale_converges_with_cross_terms(dtype):
    module = import_candidate()
    lietorch, _FrameTracker, project_calib = native_modules()
    inp = native_inputs(dtype)
    initial = inp["T_WCf"].data.clone()
    initial[:, 0] = 0.2
    initial[:, 7] = 1.12
    inp["T_WCf"] = lietorch.Sim3(initial)
    X_initial, _d = project_calib(inp["Xf"] + torch.tensor([0.2, 0.0, 0.0], dtype=dtype), inp["K"], inp["img_size"], jacobian=True, border=0, z_eps=1e-6)[:2]
    inp["meas_k"] = X_initial.detach().cpu()
    info = np.eye(7) * 5000.0
    info[0, 6] = info[6, 0] = 300.0
    assert np.linalg.eigvalsh(info).min() > 0
    out_world, out_local, trace = module.opt_pose_calib_sim3_with_metric_factor(
        tracker(), **inp, factor=factor(info=info), current_frame_id=10, keyframe_id=9
    )
    assert trace
    assert trace[-1]["final_local_pose_max_abs_diff"] == pytest.approx(0.0, abs=0.0)
    assert abs(float(out_local.data[0, 0])) < 0.04
    assert abs(float(out_local.data[0, 7]) - 1.0) < 0.04
    assert trace[-1]["metric_cost"] < trace[0]["metric_cost"]
    assert torch.equal(out_world.data, out_local.data)


def test_unsupported_modes_reject_only_when_factor_enabled():
    module = import_candidate()
    bad = tracker()
    inp = native_inputs(torch.float64)
    bad.cfg["stereo_fix_pose_scale"] = True
    with pytest.raises(ValueError, match="stereo_fix_pose_scale"):
        module.opt_pose_calib_sim3_with_metric_factor(bad, **inp, factor=factor(), current_frame_id=10, keyframe_id=9)
    ok = tracker()
    with pytest.raises(ValueError, match="metric_translation_target"):
        module.opt_pose_calib_sim3_with_metric_factor(
            ok, **inp, factor=factor(), current_frame_id=10, keyframe_id=9,
            metric_translation_target=torch.zeros(3),
        )
