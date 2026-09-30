"""Current graph/fusion on ten frozen Sep-27 frontends and plain-PnP factors."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / "reports/stereo_spatial_repeatability_20260927/sift_lm_gyro_candidate_ten_v1"
DEST = ROOT / "reports/crossover_20260930/current_cached_old_ten_v1"
NAMES = ("fresh1", "dev1", "dev2", "heldout1", "heldout2", "heldout3", "heldout4", "fresh2", "fresh3", "fresh4")
PYTHON = "/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/.venv/bin/python"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def set_flag(command: list[str], flag: str, value: str) -> None:
    command[command.index(flag) + 1] = value


def run(command: list[str], log: Path) -> int:
    with log.open("w") as stream:
        return subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT).returncode


def main() -> None:
    if DEST.exists() or DEST.is_symlink():
        raise ValueError(f"Refuse overwrite: {DEST}")
    entries = []
    for name in NAMES:
        manifest = OLD / name / "manifest.json"
        record = json.loads(manifest.read_text())
        inputs = record["cached_inputs"]
        score_cmd = next(cmd for stage, cmd in record["commands"] if stage == "score")
        capture = score_cmd[score_cmd.index("--capture") + 1]
        paths = [manifest, Path(inputs["trajectory"]), Path(inputs["stereo_report"]),
                 *map(Path, inputs["additional_stereo_reports"]), Path(inputs["imu_scale_report"]),
                 Path(inputs["relative_motion_trajectory"]), Path(inputs["relative_motion_report"])]
        for path in paths:
            if not path.is_file():
                raise FileNotFoundError(path)
        entries.append(dict(name=name, record=record, capture=capture,
                            input_sha256={str(path): sha(path) for path in paths}))
    DEST.mkdir(parents=True)
    sources = [ROOT / "scripts" / name for name in
               ("fuse_mast3r_stereo_imu.py", "fuse_docker2_mast3r_complementary.py",
                "assess_mast3r_fusion_input_quality.py", "smooth_pose_trajectory.py", "score_steamvr_slam.py")]
    source_sha = {str(path): sha(path) for path in sources}
    (DEST / "input_manifest.json").write_text(json.dumps(dict(
        policy="frozen Sep-27 frontends and original plain-PnP reports; CURRENT stages 7-9; GT after all trajectories",
        source_sha256=source_sha,
        cases=[{key: value for key, value in entry.items() if key != "record"} for entry in entries]), indent=2) + "\n")
    for entry in entries:
        name = entry["name"]
        old_dir = OLD / name
        target = DEST / name
        target.mkdir()
        inputs = entry["record"]["cached_inputs"]
        commands = []
        for stage, template in entry["record"]["commands"]:
            if stage == "score":
                continue
            command = list(template)
            command[0] = PYTHON
            command = [str(target) + value[len(str(old_dir)):] if value.startswith(str(old_dir) + "/") else value
                       for value in command]
            if stage == "graph":
                set_flag(command, "--stereo-report", inputs["stereo_report"])
                for index, value in enumerate(command):
                    if value == "--additional-stereo-report":
                        rank = sum(v == "--additional-stereo-report" for v in command[:index])
                        command[index + 1] = inputs["additional_stereo_reports"][rank]
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
        (DEST / "run_status.json").write_text(json.dumps(
            [{key: value for key, value in item.items() if key != "record"} for item in entries], indent=2) + "\n")
    for entry in entries:
        if not entry.get("trajectory_exists"):
            continue
        for raw, expected in entry["input_sha256"].items():
            if sha(Path(raw)) != expected:
                raise ValueError(f"Input changed: {raw}")
        for raw, expected in source_sha.items():
            if sha(Path(raw)) != expected:
                raise ValueError(f"Source changed: {raw}")
        target = DEST / entry["name"]
        entry["score_rc"] = run([sys.executable, str(ROOT / "scripts/score_steamvr_slam.py"),
            "--capture", entry["capture"], "--estimate", str(target / "trajectory_fused.csv"),
            "--query-time-domain", "camera", "--output", str(target / "score")], target / "score.log")
        print(f"{entry['name']}: score rc={entry['score_rc']}", flush=True)
        (DEST / "run_status.json").write_text(json.dumps(
            [{key: value for key, value in item.items() if key != "record"} for item in entries], indent=2) + "\n")


if __name__ == "__main__":
    main()
