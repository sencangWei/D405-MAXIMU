"""Diagnostic native GN control/relative-depth-shape trial; no trajectory scoring."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import torch
from scipy.spatial.transform import Rotation

from condition_native_pointmap_depth_shape import condition_depth_shape
from probe_dense_native_graph import _clone_args, _validate_graph_args


def sha(path):
    digest=hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def stereo_depth(left, right, focal, baseline, eye, minimum, maximum):
    """Same native SGBM/LR check, expressed in the requested eye's pixel grid."""
    common = dict(numDisparities=128, blockSize=5, P1=200, P2=800,
                  disp12MaxDiff=1, uniquenessRatio=8, speckleWindowSize=80,
                  speckleRange=2, preFilterCap=31, mode=cv2.STEREO_SGBM_MODE_SGBM_3WAY)
    dl = cv2.StereoSGBM_create(minDisparity=0, **common).compute(left, right).astype(np.float32)/16
    dr = cv2.StereoSGBM_create(minDisparity=-128, **common).compute(right, left).astype(np.float32)/16
    y, x = np.indices(left.shape)
    disparity = dl if eye == "left" else -dr
    partner_x = np.rint(x-disparity if eye == "left" else x+disparity).astype(np.int32)
    inside = (partner_x >= 0) & (partner_x < left.shape[1]) & (disparity > 0.5)
    partner = np.full(left.shape, np.nan, dtype=np.float32)
    partner[inside] = (dr if eye == "left" else dl)[y[inside], partner_x[inside]]
    valid = inside & (np.abs(disparity+partner if eye == "left" else disparity-partner) <= 1)
    depth = np.full(left.shape, np.nan, dtype=np.float32)
    depth[valid] = focal*baseline/disparity[valid]
    valid &= (depth >= minimum) & (depth <= maximum)
    depth[~valid] = np.nan
    return depth


def load_depths(job, graph):
    native, paired = Path(job["dataset"]), Path(job["paired_left_dataset"])
    nm, pm = read_json(native/"dataset_manifest.json"), read_json(paired/"dataset_manifest.json")
    eye = job["eye"]
    if eye not in ("left", "right") or nm["stream"] != "infrared_"+eye or pm["stream"] != "infrared_left":
        raise ValueError("source eye mismatch")
    source = pm["stereo_depth_source"]
    if source["max_left_right_skew_ms"] != 0 or not 0 < source["baseline_m"] < 0.1:
        raise ValueError("unverified stereo timing/baseline")
    with (native/"frames.csv").open() as f:
        nr = list(csv.DictReader(f))
    with (paired/"frames.csv").open() as f:
        pr = list(csv.DictReader(f))
    if nr != pr:
        raise ValueError("paired/native frame timelines differ")
    nc, pc = read_json(native/"calibration.yaml"), read_json(paired/"calibration.yaml")
    if nc != pc or any(nc["calibration"][4:]):
        raise ValueError("this diagnostic requires identical zero-distortion stereo intrinsics")
    h, w = int(graph["args"][9]), int(graph["args"][10])
    scale = nc["width"]/w
    if nc["height"]/h != scale:
        raise ValueError("unsupported native image crop/aspect transform")
    fx, fy, cx, cy = nc["calibration"][:4]
    expected_k = torch.tensor([[fx/scale,0,cx/scale],[0,fy/scale,cy/scale],[0,0,1]], dtype=graph["args"][3].dtype)
    if not torch.allclose(expected_k, graph["args"][3].cpu(), atol=1e-4, rtol=0):
        raise ValueError("native graph K differs from exact source resize")
    info = source["right_camera_info"]
    if [info[k] for k in ("fx","fy","ppx","ppy")] != [fx,fy,cx,cy] or any(info["coeffs"]):
        raise ValueError("right factory calibration ambiguity")
    depths, bindings = [], []
    for fid in graph["frame_ids"]:
        row = nr[int(fid)]
        if int(row["input_index"]) != int(fid):
            raise ValueError("native input index mismatch")
        lp, rp = paired/row["image"], paired/source["right_directory"]/row["image"]
        npth = native/row["image"]
        actual = lp if eye == "left" else rp
        if sha(npth) != sha(actual):
            raise ValueError("native image differs from actual stereo eye")
        left, right = cv2.imread(str(lp),0), cv2.imread(str(rp),0)
        if left is None or right is None or left.shape != (nc["height"],nc["width"]) or right.shape != left.shape:
            raise ValueError("raw stereo image size/missing data")
        depth = stereo_depth(left,right,fx,float(source["baseline_m"]),eye,
                             float(source.get("minimum_depth_m",0.15)),float(source.get("maximum_depth_m",0.65)))
        depths.append(cv2.resize(depth,(w,h),interpolation=cv2.INTER_NEAREST).reshape(-1))
        bindings.append({"frame_id":int(fid),"t_sec":row["t_sec"],"source_frame_number":row["source_frame_number"],
                         "native_image_sha256":sha(npth),"left_image_sha256":sha(lp),"right_image_sha256":sha(rp)})
    return torch.from_numpy(np.stack(depths)), bindings, {
        "baseline_m":source["baseline_m"],"eye":eye,"resize_scale":scale,"pixel_mapping":"native INTER_NEAREST, no crop",
        "native_frames_sha256":sha(native/"frames.csv"),"paired_manifest_sha256":sha(paired/"dataset_manifest.json"),
        "native_calibration_sha256":sha(native/"calibration.yaml"),"minimum_depth_m":source.get("minimum_depth_m",0.15),
        "maximum_depth_m":source.get("maximum_depth_m",0.65)}


def pose_check(poses):
    if not torch.isfinite(poses).all() or not (poses[:,7] > 0).all():
        raise ValueError("nonfinite/nonpositive native GN output")
    if float((torch.linalg.vector_norm(poses[:,3:7],dim=1)-1).abs().max()) > 1e-3:
        raise ValueError("nonunit native GN quaternion")


def relative_rotation_error(poses, source, target, transform):
    r = Rotation.from_quat(poses[target,3:7].numpy()).inv()*Rotation.from_quat(poses[source,3:7].numpy())
    return float(np.degrees((Rotation.from_matrix(transform[:3,:3]).inv()*r).magnitude()))


def check_chain_pnp(graph, depths, poses):
    """All original chronological-neighbor edges, not GT/error-selected edges."""
    from types import SimpleNamespace
    from mast3r_slam.global_opt import FactorGraph
    a, ids = graph["args"], graph["frame_ids"]
    h,w = int(a[9]),int(a[10])
    holder = SimpleNamespace(device="cpu",K=a[3],cfg={})
    holder._metric_pnp_direction=lambda *v: FactorGraph._metric_pnp_direction(holder,*v)
    frames = [SimpleNamespace(frame_id=fid,img=torch.empty(1,3,h,w),metric_depth=d.reshape(h,w),
                              metric_anchor_mask=torch.isfinite(d)&(d>0)) for fid,d in zip(ids,depths)]
    edges = {(int(i),int(j)):k for k,(i,j) in enumerate(zip(a[4],a[5]))}
    reports = []
    for i in range(len(ids)-1):
        j=i+1
        if (i,j) not in edges or (j,i) not in edges:
            continue
        k,rev = edges[(i,j)],edges[(j,i)]
        vi=a[7][k].reshape(-1)&(a[8][k].reshape(-1)>float(a[16]))
        vj=a[7][rev].reshape(-1)&(a[8][rev].reshape(-1)>float(a[16]))
        accepted,report=FactorGraph.metric_loop_gate(holder,frames[i],frames[j],a[6][k],vi,a[6][rev],vj)
        if accepted:
            transform,_=holder._metric_pnp_direction(frames[i],frames[j],a[6][k],vi)
            report["rotation_disagreement_deg"]={name:relative_rotation_error(p,i,j,transform) for name,p in poses.items()}
        reports.append(report)
    return reports


def validate_job_bindings(job):
    native,paired,source_run=Path(job["dataset"]),Path(job["paired_left_dataset"]),Path(job["source_run"])
    bindings={"graph":Path(job["graph"]),"source_manifest":source_run/"run_manifest.json",
              "native_manifest":native/"dataset_manifest.json","paired_manifest":paired/"dataset_manifest.json",
              "native_frames":native/"frames.csv","paired_frames":paired/"frames.csv",
              "native_calibration":native/"calibration.yaml","paired_calibration":paired/"calibration.yaml"}
    if set(job["input_sha256"])!=set(bindings):
        raise ValueError("missing frozen source bindings")
    for name,path in bindings.items():
        if sha(path)!=job["input_sha256"][name]:
            raise ValueError("frozen source SHA mismatch: "+name)
    source_manifest=read_json(source_run/"run_manifest.json")
    if source_manifest.get("schema")!="umi_mast3r_run_v1" or source_manifest.get("slam_supervision") is not False:
        raise ValueError("native source provenance/supervision mismatch")
    if (source_run/"dataset").resolve()!=Path(job["dataset"]).resolve():
        raise ValueError("source-run/native dataset mismatch")
    if sha(source_manifest["config"])!=source_manifest["config_sha256"]:
        raise ValueError("native source configuration changed")


def run_job(job, out):
    validate_job_bindings(job)
    import mast3r_slam_backends
    graph_path,source_run=Path(job["graph"]),Path(job["source_run"])
    graph=torch.load(graph_path,map_location="cpu",weights_only=True)
    a=graph["args"]
    if len(a)!=19 or len(graph["frame_ids"])!=a[0].shape[0] or sorted(set(graph["frame_ids"]))!=graph["frame_ids"]:
        raise ValueError("malformed original graph")
    _validate_graph_args(torch,a)
    depth,images,metadata=load_depths(job,graph)
    conditioned,conditioning=condition_depth_shape(a,depth,torch.isfinite(depth)&(depth>0))
    def solve(v):
        args=_clone_args(torch,v,"cuda")
        mast3r_slam_backends.gauss_newton_calib(*args)
        torch.cuda.synchronize()
        p=args[0].detach().cpu()
        pose_check(p)
        del args
        torch.cuda.empty_cache()
        return p
    control=solve(a)
    repeat=solve(a)
    if not torch.equal(control,repeat):
        raise ValueError("same original native GN replay not exact")
    variant=solve(conditioned)
    poses={"pre":a[0].cpu(),"control":control,"depth_shape":variant}
    pnp=check_chain_pnp(graph,depth,poses)
    torch.save({"graph_sha256":sha(graph_path),"frame_ids":graph["frame_ids"],"poses":poses},out/"poses.pt")
    report={"status":"DIAGNOSTIC_COMPLETE","job":job,"graph_sha256":sha(graph_path),"source_run_manifest_sha256":sha(source_run/"run_manifest.json"),"keyframe_count":len(graph["frame_ids"]),
            "native_backend_sha256":sha(mast3r_slam_backends.__file__),
            "directed_edge_count":int(a[4].numel()),"baseline_replay_exact":True,"conditioning":conditioning,
            "stereo_source":metadata,"images":images,"chain_pnp":pnp,"poses_sha256":sha(out/"poses.pt"),
            "only_graph_Xs_changed":all(torch.equal(a[i],conditioned[i]) if torch.is_tensor(a[i]) else a[i]==conditioned[i] for i in range(19) if i!=1)}
    (out/"report.json").write_text(json.dumps(report,indent=2)+"\n")
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan",required=True,type=Path)
    parser.add_argument("--output",required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError(args.output)
    plan=read_json(args.plan)
    if plan.get("external_ground_truth_used") is not False or plan.get("diagnostic_only") is not True:
        raise ValueError("invalid diagnostic plan")
    if len(plan["jobs"])!=len({j["id"] for j in plan["jobs"]}) or any(not j["id"].replace("_","").isalnum() for j in plan["jobs"]):
        raise ValueError("unsafe/duplicate job ID")
    for path,expected_sha in plan["native_source_sha256"].items():
        if sha(path)!=expected_sha:
            raise ValueError("native implementation changed")
    args.output.mkdir()
    summary={"schema":"native_depth_shape_GN_falsifier_v1","diagnostic_only":True,"external_ground_truth_used":False,
             "precision_pass":False,"production_promoted":False,"plan_sha256":sha(args.plan),
             "runner_sha256":sha(__file__),"helper_sha256":sha(Path(__file__).with_name("condition_native_pointmap_depth_shape.py")),"jobs":[]}
    for job in plan["jobs"]:
        out=args.output/job["id"]
        out.mkdir()
        try:
            report=run_job(job,out)
            accepted=[r for r in report["chain_pnp"] if r["accepted"]]
            row={"id":job["id"],"status":report["status"],"accepted_chain_pairs":len(accepted),"report_sha256":sha(out/"report.json")}
            if accepted:
                row["rotation_disagreement_deg"]={n:{"median":float(np.median([r["rotation_disagreement_deg"][n] for r in accepted])),
                                                    "max":float(np.max([r["rotation_disagreement_deg"][n] for r in accepted]))} for n in ("pre","control","depth_shape")}
        except Exception as e:
            row={"id":job["id"],"status":"DIAGNOSTIC_FAILED","error":str(e)}
        summary["jobs"].append(row)
        (args.output/"summary.json").write_text(json.dumps(summary,indent=2)+"\n")
        print(json.dumps(row),flush=True)
    return 0 if all(r["status"]=="DIAGNOSTIC_COMPLETE" for r in summary["jobs"]) else 3


if __name__=="__main__":
    raise SystemExit(main())
