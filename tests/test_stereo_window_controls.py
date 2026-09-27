import importlib.util
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('window_controls',
    ROOT/'.planning/metric_window_bundle_20260928/run_observation_controls.py')
controls = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(controls)


def test_fixed_windows_cover_time_without_reference_selection():
    selections = controls.windows(1200)
    assert len(selections)==5
    np.testing.assert_array_equal(selections[0], [110,115,120,125,130])
    assert all(len(row)==5 and np.all(np.diff(row)==5) for row in selections)
    assert selections[-1][-1]<1200
    with pytest.raises(ValueError):
        controls.windows(20)
    with pytest.raises(ValueError):
        controls.windows(31)
    with pytest.raises(ValueError):
        controls.windows(96)
    assert controls.windows(97)[0][0]>=0


def test_bias_derivative_sign_and_body_camera_conversion():
    times = np.linspace(0,1,401)
    body_from_camera = Rotation.from_euler('xyz',[.3,-.4,.7])
    camera_bias = np.array([.003,-.002,.001])
    gyro = np.tile(body_from_camera.apply(camera_bias),(len(times),1))
    delta,jacobians = controls.gyro_factors(times,gyro,[.1,.4,.7],0.,body_from_camera)
    np.testing.assert_allclose(delta.as_rotvec(),np.tile(camera_bias*.3,(2,1)),atol=1e-9)
    # Correcting this measured bias should produce identity in camera coordinates.
    np.testing.assert_allclose(jacobians@camera_bias,-delta.as_rotvec(),atol=1e-8)


def test_td_applied_once_and_moving_gyro_jacobian_matches_reintegration():
    times = np.linspace(0,1,401)
    gyro = np.column_stack((times*.4,np.sin(times)*.3,np.full(len(times),.1)))
    camera = Rotation.from_euler('xyz',[.2,.3,-.1])
    td = -.009109323
    delta,jacobians = controls.gyro_factors(times,gyro,[.1,.6],td,camera)
    direct = camera.inv()*controls.fusion.integrate_gyro(times,gyro,.1+td,.6+td)*camera
    assert (delta[0].inv()*direct).magnitude()<1e-12
    bias = np.array([.0003,-.0002,.0001])
    corrected = camera.inv()*controls.fusion.integrate_gyro(
        times,gyro-camera.apply(bias),.1+td,.6+td)*camera
    np.testing.assert_allclose((delta[0].inv()*corrected).as_rotvec(),jacobians[0]@bias,atol=1e-8)


def pixel_scene():
    rng = np.random.default_rng(71)
    points = rng.uniform([-.08,-.06,.25],[.08,.06,.45],(60,3))
    centers = np.array([[0.,0.,0.],[.01,.002,.001],[.022,-.001,.003]])
    rotations = Rotation.from_rotvec([[0.,0.,0.],[.01,-.02,.004],[.02,-.035,.006]])
    k = dict(fx=420.,fy=420.,cx=320.,cy=240.)
    obs = []
    for center,rotation in zip(centers,rotations):
        xyz = rotation.inv().apply(points-center)
        pixels = []
        for baseline in (0.,.018):
            pixels.append(np.column_stack((k['fx']*(xyz[:,0]-baseline)/xyz[:,2]+k['cx'],
                                           k['fy']*xyz[:,1]/xyz[:,2]+k['cy'])))
        obs.append(np.column_stack(pixels))
    return dict(observations=np.asarray(obs),valid=np.ones((3,60),bool),initial_points=points),dict(
        left_intrinsics=k,right_intrinsics=k.copy(),baseline_m=.018),centers,rotations


def test_pnp_admission_is_per_observation_and_never_uses_holdout(monkeypatch):
    data,calibration,_,_ = pixel_scene()
    train = np.ones(60,bool)
    train[-10:] = False
    data['observations'][:,~train] = 10000.  # holdout sentinel
    data['observations'][1,3] += [80.,0.,80.,0.]
    original = controls.cv2.solvePnPRansac
    def checked(points,pixels,*args,**kwargs):
        assert len(points)==50
        assert np.all(pixels<10000.)
        return original(points,pixels,*args,**kwargs)
    monkeypatch.setattr(controls.cv2,'solvePnPRansac',checked)
    _,_,admission = controls.visual_initialization(data,calibration,train)
    assert not admission[:,~train].any()
    assert not admission[1,3]
    assert admission[0,3] and admission[2,3]
    assert controls.training_support(train,admission)[3]


def test_support_collapse_is_not_filled_or_repaired():
    with pytest.raises(ValueError,match='support_collapsed'):
        controls.training_support(np.ones(30,bool),np.array([[True]*30,[False]*30,[True]*30]))


def test_gross_pixel_outliers_do_not_reenter_bundle_after_pnp():
    data,calibration,centers,rotations = pixel_scene()
    # Ten percent of tracks are grossly wrong in later views, source is sound.
    data['observations'][1:,::10] += [80.,30.,80.,30.]
    train = np.ones(60,bool)
    initial_centers,initial_rotations,admission = controls.visual_initialization(data,calibration,train)
    supported = controls.training_support(train,admission)
    gyro = rotations[:-1].inv()*rotations[1:]
    result = controls.solve_stereo_window(data['observations'][:,supported],admission[:,supported],
        np.array([0.,.25,.5]),calibration['left_intrinsics'],calibration['right_intrinsics'],.018,
        data['initial_points'][supported],initial_centers,initial_rotations,gyro,.00103,.01/3.)
    assert result['accepted'],result
    assert np.linalg.norm(result['centers'][-1]-centers[-1])<.001
    assert result['diagnostics']['pixel_p95_px']<.1


def test_small_source_depth_bias_can_remain_variable_in_bundle():
    data,calibration,centers,rotations = pixel_scene()
    true_points = data['initial_points'].copy()
    data['initial_points'] *= 1.01
    train = np.ones(60,bool)
    initial_centers,initial_rotations,admission = controls.visual_initialization(data,calibration,train)
    supported = controls.training_support(train,admission)
    assert supported.sum()==60  # modest depth bias must not delete whole tracks
    result = controls.solve_stereo_window(data['observations'],admission,np.array([0.,.25,.5]),
        calibration['left_intrinsics'],calibration['right_intrinsics'],.018,
        data['initial_points'],initial_centers,initial_rotations,rotations[:-1].inv()*rotations[1:],
        .00103,.01/3.)
    assert result['accepted'],result
    assert np.median(np.linalg.norm(result['landmarks']-true_points,axis=1))<.0001
    assert np.linalg.norm(result['centers'][-1]-centers[-1])<.0001
