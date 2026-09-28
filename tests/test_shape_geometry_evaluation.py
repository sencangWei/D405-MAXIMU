import importlib.util
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / '.planning/metric_window_bundle_20260928/score_shape_geometry.py'
SPEC = importlib.util.spec_from_file_location('score_shape_geometry', PATH)
score = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(score)


def test_interior_errors_are_reported_even_with_both_endpoints_correct():
    centers = np.column_stack((np.linspace(0, .08, 9), np.zeros((9, 2))))
    bowed = centers.copy()
    bowed[2, 2] = .012
    errors = score.relative_errors(bowed, Rotation.identity(9), centers, Rotation.identity(9))
    assert errors.shape == (8,)
    np.testing.assert_allclose(errors[[3, 7]], 0)
    assert errors.max() == pytest.approx(12)
    assert np.count_nonzero(errors) == 1


def test_same_stored_geometry_is_invariant_to_world_rigid_transform():
    centers = np.column_stack((np.linspace(0, .08, 9), np.zeros((9, 2))))
    predicted = centers.copy()
    predicted[3, 1] = .005
    basis = Rotation.from_rotvec([.2, -.3, .1])
    shift = np.array([1., -.2, .4])
    original = score.relative_errors(predicted, Rotation.identity(9), centers, Rotation.identity(9))
    world = score.relative_errors(basis.apply(predicted) + shift, basis * Rotation.identity(9),
                                  basis.apply(centers) + shift, basis * Rotation.identity(9))
    np.testing.assert_allclose(world, original, atol=1e-10)


def test_camera_reference_uses_rotating_body_lever_not_fixed_world_shift():
    body = np.zeros((9, 3))
    rotations = Rotation.from_euler('z', np.linspace(0, 90, 9), degrees=True)
    extrinsic = np.eye(4)
    extrinsic[:3, 3] = [.1, 0, 0]
    extrinsic[:3, :3] = Rotation.from_euler('x', 20, degrees=True).as_matrix()
    camera, attitude = score.camera_reference(body, rotations, extrinsic)
    np.testing.assert_allclose(camera[[0, -1]], [[.1, 0, 0], [0, .1, 0]], atol=1e-12)
    np.testing.assert_allclose(attitude.as_matrix(), (rotations * Rotation.from_matrix(extrinsic[:3, :3])).as_matrix())


def test_invalid_geometry_and_extrinsic_fail_closed():
    with pytest.raises(ValueError):
        score.relative_errors(np.zeros((8, 3)), Rotation.identity(8), np.zeros((9, 3)), Rotation.identity(9))
    with pytest.raises(ValueError):
        score.camera_reference(np.zeros((9, 3)), Rotation.identity(9), np.zeros((4, 4)))
    with pytest.raises(ValueError):
        score.relative_errors(np.full((9, 3), np.nan), Rotation.identity(9), np.zeros((9, 3)), Rotation.identity(9))


def test_provenance_rejects_changed_or_deleted_file(tmp_path):
    source = tmp_path / 'source'
    source.write_text('frozen')
    hashes = {str(source): score.digest(source)}
    score.verify_hashes(hashes)
    source.write_text('changed')
    with pytest.raises(ValueError):
        score.verify_hashes(hashes)
    source.unlink()
    with pytest.raises(ValueError):
        score.verify_hashes(hashes)


def test_conflicting_frozen_bindings_cannot_silently_replace_expected_hash():
    hashes = {'source': 'old'}
    score.add_hashes(hashes, {'source': 'old'})
    with pytest.raises(ValueError):
        score.add_hashes(hashes, {'source': 'new'})
    assert hashes == {'source': 'old'}


def test_snapshot_parses_hashed_bytes_and_later_mutation_is_detected(tmp_path):
    source = tmp_path / 'record.json'
    source.write_text('{"frozen": true}')
    hashes = {}
    record = score.json_snapshot(source, hashes)
    source.write_text('{"frozen": false}')
    assert record == {'frozen': True}
    with pytest.raises(ValueError):
        score.verify_hashes(hashes)


def factor_fixture():
    return dict(available=True, diagnostic_only=True, not_admissible_for_graph=True, solver_accepted=True,
                optimized_centers_m=np.zeros((9, 3)).tolist(), optimized_rotvecs_camera_to_window=np.zeros((9, 3)).tolist(),
                metadata=dict(available_for_graph=False, calibrated_covariance=False, statistical_independence_claimed=False))


def test_pair_acceptance_flags_and_unavailable_refusals_are_retained():
    factor = factor_fixture()
    windows = [dict(accepted=True, diagnostics=dict(stereo_window_shape_factor=factor)) for _ in range(2)]
    assert score.pair_factor({'accepted': True}, windows) == factor
    factor['metadata']['calibrated_covariance'] = True
    with pytest.raises(ValueError):
        score.pair_factor({'accepted': True}, windows)
    with pytest.raises(ValueError):
        score.pair_factor({'accepted': False}, windows)
    assert score.pair_factor({'accepted': False}, [dict(accepted=False)] * 2) is None
    assert score.pair_factor({'accepted': True}, [dict(accepted=True)] * 2) is None


def test_no_gt_is_opened_before_exact_ten_controls_freeze(tmp_path, monkeypatch):
    import json
    source = tmp_path / 'summary.json'
    source.write_text(json.dumps({'cases': []}))
    monkeypatch.setattr(score, 'pose_snapshot', lambda *args: pytest.fail('GT must not be opened'))
    with pytest.raises(ValueError, match='exact ten'):
        score.score(source, tmp_path)
