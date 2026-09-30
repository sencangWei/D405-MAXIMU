"""Transport the frozen Sep-27 SIFT-LM/gyro candidate to six Sep-30 inputs.

This covers the SIFT observation candidate, NOT the later full-seam window graph.
The separate graph/score phases prevent reference feedback into trajectory generation.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / "reports/stereo_spatial_repeatability_20260927/sift_lm_gyro_candidate_ten_v1/fresh1"
DEST = ROOT / "reports/crossover_20260930/old_sift_on_new_six_v1"
CAPTURE_ROOT = ROOT / "reports/steamvr_umi_sessions"
NAMES = (
    "20260930_164057_slam_validation_six_take01",
    "20260930_165020_slam_validation_six_take02_retry",
    "20260930_165307_slam_validation_six_take03",
    "20260930_165515_slam_validation_six_take04",
    "20260930_165741_slam_validation_six_take05",
    "20260930_170015_slam_validation_six_take06",
)
PYTHON = Path("/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/.venv/bin/python")
SIFT = ROOT / ".planning/stereo_spatial_repeatability_20260927/run_sift_lm_gyro_candidate.py"
STEREO = ROOT / "scripts/align_mast3r_scale_with_stereo.py"
SCORE = ROOT / "scripts/score_steamvr_slam.py"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def run(command: list[str], log: Path) -> int:
    with log.open("w") as stream:
        return subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT).returncode


def set_flag(command: list[str], flag: str, value: str) -> None:
    command[command.index(flag) + 1] = value


def main() -> None:
    if DEST.exists() or DEST.is_symlink():
        raise ValueError(f"Refuse overwrite: {DEST}")
    old_manifest = json.loads((OLD / "manifest.json").read_text())
    old_inputs = old_manifest["cached_inputs"]
    old_graph = json.loads((OLD / "graph_fusion_report.json").read_text())
    entries = []
    for number, name in enumerate(NAMES, 1):
        capture = CAPTURE_ROOT / name
        version = "v1" if number <= 2 else "v2"
        evaluation = capture / f"evaluation_20260930_{version}"
        base = evaluation / "fusion_guarded/baseline/mast3r"
        capture_manifest = json.loads((capture / "capture_manifest.json").read_text())
        inputs = dict(old_inputs)
        inputs.update(session=capture_manifest["d405_session"],
                      trajectory=str(base / "trajectory_imu_metric.csv"),
                      stereo_report=str(base / "stereo_scale_bidirectional_report.json"),
                      additional_stereo_reports=[str(base / f"stereo_scale_{part}_report.json")
                                                 for part in ("long_hops", "dense10hz", "multisecond")],
                      imu_scale_report=str(base / "imu_scale_report.json"),
                      keyframe_dir=str(base / "mast3r_logs/keyframes/dataset"),
                      relative_motion_trajectory=str(evaluation / "vins/vio_corrected_stream.csv"),
                      relative_motion_report=str(evaluation / "vins/run_acceptance.json"))
        paths = [capture / "capture_manifest.json", capture / "tracker.csv",
                 Path(inputs["trajectory"]), Path(inputs["stereo_report"]),
                 *map(Path, inputs["additional_stereo_reports"]), Path(inputs["imu_scale_report"]),
                 Path(inputs["relative_motion_trajectory"]), Path(inputs["relative_motion_report"]),
                 Path(inputs["session"]) / "d405_frames.csv", Path(inputs["session"]) / "external_imu/imu.bin"]
        for path in paths:
            if not path.is_file():
                raise FileNotFoundError(path)
        entries.append(dict(name=f"take{number}", capture=str(capture), inputs=inputs,
                            input_sha256={str(path): sha(path) for path in paths}))
    DEST.mkdir(parents=True)
    source_sha = {str(path): sha(path) for path in (SIFT, STEREO, SCORE, ROOT / "scripts/fuse_mast3r_stereo_imu.py")}
    (DEST / "input_manifest.json").write_text(json.dumps(dict(entries=entries, source_sha256=source_sha,
        policy="Sep-27 SIFT-LM/gyro observation candidate, no GT in solve, no full-seam windows"), indent=2) + "\n")
    sift = module("crossover_old_sift", SIFT)
    stereo = module("crossover_stereo", STEREO)
    for entry in entries:
        name = entry["name"]
        target = DEST / name
        (target / "mast3r").mkdir(parents=True)
        graph = dict(inputs=entry["inputs"], camera_extrinsics=old_graph["camera_extrinsics"])
        (target / "mast3r/graph_fusion_report.json").write_text(json.dumps(graph, indent=2) + "\n")
        # The historical helper resolves graph_path from ROOT/reports/<cached>/mast3r.
        cached = str(target.relative_to(ROOT / "reports"))
        print(f"{name}: SIFT refinement START", flush=True)
        try:
            rewritten = sift.refine_reports(stereo, cached, target / "stereo")
        except Exception as error:
            entry["refine_error"] = repr(error)
            print(f"{name}: refine ERROR {error}", flush=True)
            (DEST / "run_status.json").write_text(json.dumps(entries, indent=2) + "\n")
            continue
        commands = []
        for stage, template in old_manifest["commands"]:
            if stage == "score":
                continue
            command = list(template)
            command = [str(PYTHON) if i == 0 else value for i, value in enumerate(command)]
            if stage == "graph":
                set_flag(command, "--session", entry["inputs"]["session"])
                set_flag(command, "--trajectory", entry["inputs"]["trajectory"])
                set_flag(command, "--stereo-report", rewritten[0])
                for index, value in enumerate(command):
                    if value == "--additional-stereo-report":
                        rank = sum(v == "--additional-stereo-report" for v in command[:index])
                        command[index + 1] = rewritten[rank + 1]
                for flag, key in (("--imu-scale-report", "imu_scale_report"),
                                  ("--keyframe-dir", "keyframe_dir"),
                                  ("--relative-motion-trajectory", "relative_motion_trajectory"),
                                  ("--relative-motion-report", "relative_motion_report")):
                    set_flag(command, flag, entry["inputs"][key])
            else:
                for key, value in old_inputs.items():
                    if isinstance(value, str):
                        command = [entry["inputs"].get(key, value) if item == value else item for item in command]
                command = [str(target) + item[len(str(OLD)):] if item.startswith(str(OLD) + "/") else item
                           for item in command]
            if stage == "graph":
                set_flag(command, "--output", str(target / "trajectory_graph.csv"))
                set_flag(command, "--report", str(target / "graph_fusion_report.json"))
            commands.append((stage, command))
        entry["commands"] = commands
        for stage, command in commands:
            print(f"{name}: {stage} START", flush=True)
            rc = run(command, target / f"{stage}.log")
            entry[f"{stage}_rc"] = rc
            print(f"{name}: {stage} rc={rc}", flush=True)
            if rc != 0 and not (stage == "quality" and rc == 3):
                break
        entry["trajectory_exists"] = (target / "trajectory_fused.csv").is_file()
        (DEST / "run_status.json").write_text(json.dumps(entries, indent=2) + "\n")
    for entry in entries:
        if not entry.get("trajectory_exists"):
            continue
        for raw, expected in entry["input_sha256"].items():
            if sha(Path(raw)) != expected:
                raise ValueError(f"Input changed: {raw}")
        for raw, expected in source_sha.items():
            if sha(Path(raw)) != expected:
                raise ValueError(f"Source changed: {raw}")
        name = entry["name"]
        target = DEST / name
        entry["score_rc"] = run([sys.executable, str(SCORE), "--capture", entry["capture"],
                                  "--estimate", str(target / "trajectory_fused.csv"),
                                  "--query-time-domain", "camera", "--output", str(target / "score")],
                                 target / "score.log")
        print(f"{name}: score rc={entry['score_rc']}", flush=True)
        (DEST / "run_status.json").write_text(json.dumps(entries, indent=2) + "\n")


if __name__ == "__main__":
    main()
