import importlib.util, json, hashlib
from pathlib import Path
p=Path("/home/robot/ego_vio_humble/.planning/frontend_observation_20260930/probe_pointmap_match_stages.py")
spec=importlib.util.spec_from_file_location("frontier_probe",p)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
cfg=Path("/home/robot/ego_vio_humble/config/mast3r_slam_d405_offline.yaml")
before={str(x):hashlib.sha256(x.read_bytes()).hexdigest() for x in [p,cfg]}
m.load_config(str(cfg))
model=m.load_mast3r("/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/checkpoints/MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric.pth",device="cuda:0").eval()
cases=[
("20260929_take02","left","/home/robot/ego_vio_humble/reports/steamvr_umi_sessions/20260929_195055_slam_validation_take02/evaluation_20260929_v1/fusion_adaptive/sparse/mast3r/dataset",[(587,585)]),
("20260929_take02","right","/home/robot/ego_vio_humble/.planning/dual_ir_regression_25_20261002/batch_v1/20260929_take02/right_cache/dataset",[(587,576),(588,576)])]
for record,eye,data,pairs in cases:
    dataset=Path(data);manifest=json.loads((dataset/"dataset_manifest.json").read_text())
    assert manifest["stream"]=="infrared_"+eye
    assert record[:8] in manifest["source_session"]
    cache={}
    for source,target in pairs:
        result=m.probe(model,dataset,source,target,cache,with_stereo=False)
        print(json.dumps({"id":record,"eye":eye,"dataset":data,"with_stereo":False,"probe":result}),flush=True)
after={str(x):hashlib.sha256(x.read_bytes()).hexdigest() for x in [p,cfg]}
assert after==before
print(json.dumps({"source_guard_before":before,"source_guard_after":after,"source_guard_verified":True}),flush=True)
