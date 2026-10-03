import importlib.util
import sys
from pathlib import Path

import numpy as np
import torch
import pytest


BASE=Path(__file__).resolve().parents[1]/".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
sys.path.insert(0,str(BASE))
spec=importlib.util.spec_from_file_location("depth_shape_probe",BASE/"probe_stereo_depth_shape_native_graph.py")
probe=importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.mark.parametrize("eye",["left","right"])
def test_depth_lr_consistency_uses_correct_eye_grid(monkeypatch,eye):
    dl=np.ones((3,8),dtype=np.int16)*16
    dr=np.ones((3,8),dtype=np.int16)*-16
    class Matcher:
        def __init__(self,disparity):self.disparity=disparity
        def compute(self,*args):return self.disparity
    monkeypatch.setattr(probe.cv2,"StereoSGBM_create",lambda minDisparity,**kwargs:Matcher(dl if minDisparity==0 else dr))
    result=probe.stereo_depth(np.zeros((3,8),np.uint8),np.zeros((3,8),np.uint8),10,.02,eye,.15,.65)
    border=0 if eye=="left" else 7
    assert np.isnan(result[:,border]).all()
    assert np.allclose(np.delete(result,border,axis=1),.2)
    dr[:]=-48
    assert np.isnan(probe.stereo_depth(np.zeros((3,8),np.uint8),np.zeros((3,8),np.uint8),10,.02,eye,.15,.65)).all()


def test_pose_check_rejects_bad_native_output():
    p=torch.tensor([[0.,0,0,0,0,0,1,1]])
    probe.pose_check(p)
    p[0,7]=0
    with pytest.raises(ValueError,match="nonpositive"):probe.pose_check(p)
    p[0,7]=1
    p[0,3]=.5
    with pytest.raises(ValueError,match="nonunit"):probe.pose_check(p)


def test_rotation_direction_is_camera_source_to_target():
    from scipy.spatial.transform import Rotation
    p=torch.tensor([[0.,0,0,0,0,0,1,1],[0,0,0,0,0,0,1,1]],dtype=torch.float64)
    p[1,3:7]=torch.from_numpy(Rotation.from_euler("z",20,degrees=True).as_quat())
    transform=np.eye(4)
    transform[:3,:3]=Rotation.from_euler("z",-20,degrees=True).as_matrix()
    assert probe.relative_rotation_error(p,0,1,transform)<1e-10
    assert np.isclose(probe.relative_rotation_error(p,1,0,transform),40)


def test_stale_graph_binding_rejected_before_native_import(tmp_path):
    graph=tmp_path/"graph.pt"
    graph.write_bytes(b"actual")
    job={"graph":str(graph),"dataset":str(tmp_path/"dataset"),"paired_left_dataset":str(tmp_path/"paired"),
         "source_run":str(tmp_path/"source"),"input_sha256":{n:"not_the_actual_hash" for n in
         ("graph","source_manifest","native_manifest","paired_manifest","native_frames","paired_frames","native_calibration","paired_calibration")}}
    with pytest.raises(ValueError,match="SHA mismatch: graph"):
        probe.run_job(job,tmp_path)
