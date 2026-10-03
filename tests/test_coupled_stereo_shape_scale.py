from pathlib import Path
import importlib.util
import torch
import pytest

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
spec = importlib.util.spec_from_file_location("coupled_probe", BASE / "probe_coupled_stereo_shape_scale.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_coupled_supported_depths_share_one_metric_gauge_unsupported_unchanged():
    p = torch.zeros(2, 8)
    p[:, 6] = 1
    p[:, 7] = torch.tensor([1.2, .8])
    xyz = torch.tensor([[[.1,.2,1.], [.2,.1,2.], [.3,.1,3.]],
                        [[.4,.1,4.], [.1,.4,2.], [.2,.2,1.]]])
    args = (p, xyz, *([torch.ones(1)] * 6), torch.ones(1), *([1.] * 10))
    depths = torch.tensor([[.2, .6, float("nan")], [.4, .3, float("nan")]])
    conditioned, targets, report, _ = probe.prepare(args, depths)
    valid = torch.isfinite(depths)
    expected = p[0, 7] / report["frames"][0]["median_metric_over_native_ratio"] * depths
    world_z = targets[:, None] * conditioned[1][..., 2]
    assert torch.allclose(world_z[valid], expected[valid])
    assert torch.equal(conditioned[1][~valid], xyz[~valid])
    assert torch.equal(args[0], p) and torch.equal(args[1], xyz)
    assert targets[0] == p[0, 7]
    for i in range(19):
        if i != 1:
            assert torch.equal(args[i], conditioned[i]) if torch.is_tensor(args[i]) else args[i] == conditioned[i]


def summary(right=1., passing=1.):
    return {"jobs": [{"id": name, "status": "DIAGNOSTIC_COMPLETE", "rotation_disagreement_deg":
                     {"control": {"max": 1.}, "coupled": {"max": passing if name.startswith("passing") else right}}}
                    for name in ["right587", "right592", "right613", "left877", "left1044", "passing_held2_752", "passing_take01_738"]]}


@pytest.mark.parametrize("right,passing", [(2., 1.), (10., 1.), (1., 1.1), (float("nan"), 1.)])
def test_completed_execution_does_not_hide_rejected_geometry(right, passing):
    verdict = probe.evaluate_candidate(summary(right, passing))
    assert verdict["formal_result"] == "REJECT"
    assert verdict["criterion_failures"] and verdict["ate_claim"] is False


def test_geometry_candidate_is_never_precision_pass():
    verdict = probe.evaluate_candidate(summary())
    assert verdict["formal_result"] == "NOT_EVALUATED_ATE"
    assert verdict["ate_claim"] is False


def test_incomplete_execution_rejected():
    value = summary()
    value["jobs"].pop()
    assert "technical_execution_incomplete" in probe.evaluate_candidate(value)["criterion_failures"]
