import importlib.util
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

SOURCE = Path(__file__).resolve().parents[1] / '.planning/stereo_spatial_repeatability_20260927/basis_contract.py'
spec = importlib.util.spec_from_file_location('basis_contract', SOURCE)
basis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(basis)
align_spec = importlib.util.spec_from_file_location('attitude_alignment', SOURCE.with_name('attitude_alignment.py'))
alignment = importlib.util.module_from_spec(align_spec)
align_spec.loader.exec_module(alignment)
score_spec = importlib.util.spec_from_file_location('local_edge_scoring', SOURCE.with_name('score_local_stereo_edges.py'))
scoring = importlib.util.module_from_spec(score_spec)
score_spec.loader.exec_module(scoring)


def test_different_world_gauge_is_not_local_attitude_error():
    vins = Rotation.from_euler('xyz', [[0, 0, 0], [.1, .2, .3], [.2, .3, .4]])
    world = Rotation.from_euler('xyz', [.2, -.4, 1.4])
    result = basis.compare_bases(world * vins, vins, world, np.ones((3, 3)) * .1, np.arange(3))
    assert result['constant_alignment_difference_deg'] < 1e-10
    for name in ('position_fit', 'attitude_fit'):
        assert max(result[name]['projected_displacement_difference_mm']) < 1e-10


def test_position_alignment_mismatch_is_separate_from_attitude_shape():
    vins = Rotation.from_euler('xyz', np.zeros((3, 3)))
    wrong_world = Rotation.from_euler('z', 5, degrees=True)
    result = basis.compare_bases(vins, vins, wrong_world, np.tile([.2, 0, 0], (3, 1)), np.arange(3))
    assert abs(result['constant_alignment_difference_deg'] - 5) < 1e-10
    assert min(result['position_fit']['projected_displacement_difference_mm']) > 17
    assert max(result['attitude_fit']['projected_displacement_difference_mm']) < 1e-10


def test_attitude_alignment_preserves_body_origin_and_camera_lever_convention():
    times = np.arange(5, dtype=float)
    body = Rotation.from_euler('xyz', [[.1*i, .2*i, -.1*i] for i in times])
    extrinsic = Rotation.from_euler('x', 1.4)
    world = Rotation.from_euler('xyz', [.3, -.2, .4])
    reference = np.column_stack((times*.1, times**2*.01, times*.02))
    target = world.apply(reference) + [.02, -.01, .03]
    result, valid, report = alignment.align_positions_with_attitudes(times, target, times, reference,
                                                                    world*body*extrinsic, body, extrinsic)
    assert valid.all()
    np.testing.assert_allclose(result, target, atol=1e-12)
    assert report['position_disagreement_p95_m'] < 1e-12
    assert report['attitude_disagreement_p95_deg'] < 1e-10
    assert not report['external_reference_used']


def test_reference_camera_origin_uses_rotating_metric_lever():
    positions = np.zeros((2, 3))
    body = Rotation.from_euler('z', [0, 90], degrees=True)
    ext = np.eye(4)
    ext[:3, 3] = [.02, 0, 0]
    cameras, rotations = scoring.camera_reference(positions, body, ext)
    np.testing.assert_allclose(cameras, [[.02, 0, 0], [0, .02, 0]], atol=1e-12)
    np.testing.assert_allclose(rotations.as_matrix(), body.as_matrix(), atol=1e-12)
