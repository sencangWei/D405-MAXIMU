#!/usr/bin/env python3
"""融合尾段 [7][8][9] 的独立复现台 —— 纯 CPU, 读现成产物, 输出改道到 scratch。

用法:
    python3 rerun_tail.py <group_dir> <out_dir> [--set key=value ...]

用途: 先在当前参数下复现出与现网逐位一致的 trajectory_fused.csv(对照组),
      再改单个参数扫描。绝不写真实产物目录。
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path("/home/robot/ego_vio_humble")
PY = "/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/.venv/bin/python"
VINS_CONFIG = ("/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release"
               "/formal_runtime_calibration/vins_config.yaml")
IMU_CAL = str(ROOT / "config/imu_runtime_accel_calibrated_raw_gyro_20260816.yaml")
S = ROOT / "scripts"

# 现网默认值(取自 mast3r_slam_precision_workflow.sh 第 253-305 行)
DEFAULTS = dict(
    relative_motion_sigma_m="0.008", joint_max_correction_mm="25",
    joint_correction_cap_mode="per-node", full_rate_max_correction_mm="20",
    orientation_node_stride="10", position_node_stride="5",
    docker2_local_weight="0.25", docker2_scale_weight="0.475",
    scale_horizon_s="1", smoothing_s="8",
    roughness_threshold_mm="9", adaptive_weight_strength="0.45",
    gaussian_sigma_s="0.025",
)
FLAGS = dict(  # 布尔开关: 默认开
    auto_visual_position_sigma=True, full_rate_imu_position_refinement=True,
    adaptive_local_weight=True, use_docker2_orientation_for_lever_arm=True,
    auto_docker2_scale_weight=False,   # 条件化尺度投票, 现网未启用 ⇒ 默认关
)


def build(group: Path, out: Path, ov: dict, sub: str = "sparse"):
    """group 是 .../groupN (或 .../groupN/fusion) ; sub 是 sparse/tight。"""
    base = group / "fusion" if (group / "fusion").is_dir() else group
    mast = base / sub / "mast3r"
    d = {**DEFAULTS, **{k.replace("-", "_"): v for k, v in ov.items()}}
    fl = {**FLAGS}
    for k, v in ov.items():                      # 允许 --no-X 关掉布尔开关
        k2 = k.replace("-", "_")
        if k2.startswith("no_"):
            fl[k2[3:]] = False
        elif k2 in fl:                           # 允许 --set X=1 打开默认关的开关
            fl[k2] = str(v) not in {"", "0", "false", "False", "no"}
    mo = out / sub / "mast3r"
    o = out / sub
    mo.mkdir(parents=True, exist_ok=True)
    o.mkdir(parents=True, exist_ok=True)

    sess = json.loads((mast / "graph_fusion_report.json").read_text())["inputs"]["session"]
    grp = base.parent
    vins = grp / "docker2_slam" / "vio_corrected_stream.csv"
    vrep = grp / "docker2_slam" / "run_acceptance.json"

    c1 = [PY, str(S / "fuse_mast3r_stereo_imu.py"),
          "--session", sess, "--trajectory", str(mast / "trajectory_imu_metric.csv"),
          "--stream", "infrared_left",
          "--stereo-report", str(mast / "stereo_scale_bidirectional_report.json"),
          "--additional-stereo-report", str(mast / "stereo_scale_long_hops_report.json"),
          "--additional-stereo-report", str(mast / "stereo_scale_dense10hz_report.json"),
          "--additional-stereo-report", str(mast / "stereo_scale_multisecond_report.json"),
          "--imu-scale-report", str(mast / "imu_scale_report.json"),
          "--vins-config", VINS_CONFIG, "--imu-calibration", IMU_CAL,
          "--expected-td-s", "-0.009109323",
          "--orientation-node-stride", d["orientation_node_stride"],
          "--position-node-stride", d["position_node_stride"],
          "--minimum-stereo-sample-hop", "1",
          "--keyframe-dir", str(mast / "mast3r_logs/keyframes/dataset"),
          "--relative-motion-trajectory", str(vins),
          "--relative-motion-report", str(vrep),
          "--relative-motion-sigma-m", d["relative_motion_sigma_m"],
          "--joint-max-correction-mm", d["joint_max_correction_mm"],
          "--joint-correction-cap-mode", d["joint_correction_cap_mode"],
          "--full-rate-max-correction-mm", d["full_rate_max_correction_mm"],
          "--metric-scale-mode", "joint", "--position-mode", "keyframe-graph",
          "--output", str(mo / "trajectory_graph.csv"),
          "--report", str(mo / "graph_fusion_report.json")]
    for f, ok in (("--auto-visual-position-sigma", fl["auto_visual_position_sigma"]),
                  ("--full-rate-imu-position-refinement", fl["full_rate_imu_position_refinement"])):
        if ok:
            c1.append(f)

    c2 = [PY, str(S / "fuse_docker2_mast3r_complementary.py"),
          "--mast3r", str(mo / "trajectory_graph.csv"),
          "--docker2", str(vins), "--docker2-report", str(vrep),
          "--body-t-camera-yaml", VINS_CONFIG,
          "--scale-horizon-s", d["scale_horizon_s"], "--smoothing-s", d["smoothing_s"],
          "--docker2-local-weight", d["docker2_local_weight"],
          "--docker2-scale-weight", d["docker2_scale_weight"],
          "--roughness-threshold-mm", d["roughness_threshold_mm"],
          "--adaptive-weight-strength", d["adaptive_weight_strength"],
          "--graph-report", str(mo / "graph_fusion_report.json"),
          "--output", str(o / "trajectory_fused_unsmoothed.csv"),
          "--report", str(o / "fusion_report.json")]
    for f, ok in (("--adaptive-local-weight", fl["adaptive_local_weight"]),
                  ("--use-docker2-orientation-for-lever-arm",
                   fl["use_docker2_orientation_for_lever_arm"]),
                  ("--auto-docker2-scale-weight",
                   fl["auto_docker2_scale_weight"])):
        if ok:
            c2.append(f)

    c3 = [PY, str(S / "assess_mast3r_fusion_input_quality.py"),
          "--graph-report", str(mo / "graph_fusion_report.json"),
          "--fusion-report", str(o / "fusion_report.json"),
          "--output", str(o / "input_quality_report.json")]
    c4 = [PY, str(S / "smooth_pose_trajectory.py"),
          "--input", str(o / "trajectory_fused_unsmoothed.csv"),
          "--output", str(o / "trajectory_fused.csv"),
          "--method", "gaussian", "--gaussian-sigma-s", d["gaussian_sigma_s"],
          "--report", str(o / "smoothing_report.json")]
    return [c1, c2, c3, c4]


def run_tail(group: Path, out: Path, ov: dict, sub: str = "sparse"):
    """不退出, 返回 dict(steps=[rc...], error=None|str, out=Path|None)。

    rc==3 出现在第 3 步 = [9/9] 输入质量门主动拒绝, 不是崩溃, 产物仍在。
    """
    res = dict(steps=[], error=None, out=None)
    try:
        cmds = build(group, out, ov, sub)
    except Exception as exc:                     # noqa: BLE001
        res["error"] = f"构建失败: {exc}"
        return res
    for i, c in enumerate(cmds, 1):
        r = subprocess.run(c, capture_output=True, text=True)
        res["steps"].append(r.returncode)
        if r.returncode == 0:
            continue
        if i == 3 and r.returncode == 3:
            res["quality_gate"] = "REJECT"
            continue
        res["error"] = f"步骤 {i} rc={r.returncode}: {r.stderr[-600:]}"
        return res
    p = out / sub / "trajectory_fused.csv"
    res["out"] = p if p.is_file() else None
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("group")
    ap.add_argument("out")
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--candidate", default="sparse")
    a = ap.parse_args()
    ov = dict(x.split("=", 1) for x in a.set)
    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    cmds = build(Path(a.group), out, ov, a.candidate)
    for i, c in enumerate(cmds, 1):
        r = subprocess.run(c, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"步骤 {i} 失败 (rc={r.returncode})")
            print("STDERR:", r.stderr[-2500:])
            return 1
        print(f"  步骤 {i} OK")
    print(f"输出: {out/a.candidate/'trajectory_fused.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
