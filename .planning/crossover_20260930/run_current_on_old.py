"""Replay current guarded product on frozen Sep-27 captures; score only afterward."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "reports/stereo_spatial_repeatability_20260927/sift_lm_gyro_candidate_ten_v1"
DEST = ROOT / "reports/crossover_20260930/current_on_old_ten_v1"
CASES = ("fresh1", "dev1", "dev2", "heldout1", "heldout2", "heldout3", "heldout4", "fresh2", "fresh3", "fresh4")
WORKFLOW = ROOT / "scripts/mast3r_slam_precision_workflow.sh"
SCORE = ROOT / "scripts/score_steamvr_slam.py"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command: list[str], log: Path) -> int:
    with log.open("w") as stream:
        return subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT).returncode


def main() -> None:
    if DEST.exists() or DEST.is_symlink():
        raise ValueError(f"Refuse overwrite: {DEST}")
    entries = []
    for name in CASES:
        manifest = BASE / name / "manifest.json"
        record = json.loads(manifest.read_text())
        inputs = record["cached_inputs"]
        score_cmd = next(cmd for stage, cmd in record["commands"] if stage == "score")
        capture = Path(score_cmd[score_cmd.index("--capture") + 1])
        paths = [manifest, capture / "capture_manifest.json", Path(inputs["session"]) / "acceptance.json",
                 Path(inputs["relative_motion_trajectory"]), Path(inputs["relative_motion_report"])]
        for path in paths:
            if not path.is_file():
                raise FileNotFoundError(path)
        entries.append(dict(name=name, session=inputs["session"], capture=str(capture),
                            vins=inputs["relative_motion_trajectory"], vins_report=inputs["relative_motion_report"],
                            input_sha256={str(path): sha(path) for path in paths}))
    DEST.mkdir(parents=True)
    snapshot = dict(policy="current fusion-guarded, frozen Sep-27 inputs, Tracker scoring only after all runs",
                    workflow_sha256=sha(WORKFLOW), score_sha256=sha(SCORE), entries=entries)
    (DEST / "input_manifest.json").write_text(json.dumps(snapshot, indent=2) + "\n")
    for entry in entries:
        name = entry["name"]
        target = DEST / name
        target.mkdir()
        print(f"{name}: fusion START", flush=True)
        entry["fusion_rc"] = run(["bash", str(WORKFLOW), "fusion-guarded", entry["session"],
                                  entry["vins"], entry["vins_report"], str(target / "fusion_guarded")],
                                 target / "fusion.log")
        entry["trajectory_exists"] = (target / "fusion_guarded/trajectory_fused.csv").is_file()
        print(f"{name}: fusion rc={entry['fusion_rc']} trajectory={entry['trajectory_exists']}", flush=True)
        (DEST / "run_status.json").write_text(json.dumps(entries, indent=2) + "\n")
    for entry in entries:
        name = entry["name"]
        target = DEST / name
        if not entry["trajectory_exists"]:
            continue
        for raw, expected in entry["input_sha256"].items():
            if sha(Path(raw)) != expected:
                raise ValueError(f"Input changed: {raw}")
        if sha(WORKFLOW) != snapshot["workflow_sha256"] or sha(SCORE) != snapshot["score_sha256"]:
            raise ValueError("Workflow or scoring source changed")
        entry["score_rc"] = run(["python3", str(SCORE), "--capture", entry["capture"],
                                  "--estimate", str(target / "fusion_guarded/trajectory_fused.csv"),
                                  "--query-time-domain", "camera", "--output", str(target / "score")],
                                 target / "score.log")
        print(f"{name}: score rc={entry['score_rc']}", flush=True)
        (DEST / "run_status.json").write_text(json.dumps(entries, indent=2) + "\n")


if __name__ == "__main__":
    main()
