import importlib.util
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('window_observations', ROOT / 'scripts/prepare_stereo_window_observations.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def synthetic_images():
    image = np.zeros((180, 320), dtype=np.uint8)
    for k, (x, y) in enumerate(( (x,y) for y in range(30, 160, 25) for x in range(60, 290, 25) )):
        cv2.rectangle(image, (x-4,y-4), (x+4,y+4), int(120+k%100), -1)
        image[y-2:y+2,x-2:x+2] = 255
    left = [cv2.warpAffine(image, np.float32([[1,0,-3*j],[0,1,0]]), (320,180)) for j in range(3)]
    right = [cv2.warpAffine(im, np.float32([[1,0,-18],[0,1,0]]), (320,180)) for im in left]
    camera = dict(fx=300., fy=300., cx=160., cy=90., coeffs=[0.]*5)
    return left, right, dict(left_intrinsics=camera, right_intrinsics=camera.copy(), baseline_m=.018)


def test_multi_frame_tracks_are_metric_and_do_not_read_learned_poses(monkeypatch):
    left, right, calibration = synthetic_images()
    monkeypatch.setattr(module, 'stereo_disparity', lambda a,b,n: (np.full(a.shape,18.,np.float32),np.full(a.shape,-18.,np.float32)))
    result = module.track_stereo_window(left, right, calibration, max_points=80)
    assert result['accepted']
    assert result['observations'].shape[0] == 3
    assert result['observations'].shape[-1] == 4
    assert np.sum(np.all(result['valid'],axis=0)) >= 20
    np.testing.assert_allclose(np.median(result['initial_points'][:,2]), .3, atol=.003)
    np.testing.assert_allclose(result['initial_centers'][-1], [.006,0.,0.], atol=.001)
    assert result['observation_frame'] == 'infrared_left_camera0'


def test_empty_images_fail_closed():
    _, _, calibration = synthetic_images()
    blank = [np.zeros((180,320),np.uint8)]*3
    result = module.track_stereo_window(blank, blank, calibration)
    assert not result['accepted']
    assert result['reason'] == 'insufficient_source_stereo_features'


def test_holdout_extraction_does_not_run_pose_initialization(monkeypatch):
    left,right,calibration = synthetic_images()
    monkeypatch.setattr(module,'stereo_disparity',lambda a,b,n:
        (np.full(a.shape,18.,np.float32),np.full(a.shape,-18.,np.float32)))
    def forbidden(*args,**kwargs):
        raise AssertionError('all-track PnP entered holdout extraction')
    monkeypatch.setattr(module.cv2,'solvePnPRansac',forbidden)
    result = module.track_stereo_window(left,right,calibration,initialize_poses=False)
    assert result['accepted']
    assert 'initial_centers' not in result
    assert 'initial_rotations' not in result


@pytest.mark.parametrize('failure', ['count', 'shape', 'baseline', 'distortion'])
def test_bad_stereo_contract_is_rejected(failure):
    left, right, calibration = synthetic_images()
    if failure == 'count':
        right = right[:-1]
    if failure == 'shape':
        right[1] = right[1][:-1]
    if failure == 'baseline':
        calibration['baseline_m'] = 0.
    if failure == 'distortion':
        calibration['left_intrinsics']['coeffs'][0] = .1
    with pytest.raises(ValueError):
        module.track_stereo_window(left, right, calibration)
