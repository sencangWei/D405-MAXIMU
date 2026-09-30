"""Apply Sep-28 full-seam joint candidate to all four complete Sep-30 frontends."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import cv2
ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / "reports/crossover_20260930/old_sift_on_new_six_v1"
DEST = ROOT / "reports/crossover_20260930/old_full_seam_on_new_v1"
CASES = ("take2", "take4", "take5", "take6")
PYTHON = "/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/.venv/bin/python"
BRIDGE = Path(__file__).with_name("single_cohort_seam_bridge.py")
WINDOW_SCRIPT = ROOT / ".planning/metric_window_bundle_20260928/run_seam_window_controls.py"
ADAPTER = ROOT / ".planning/metric_window_bundle_20260928/run_full_seam_controls.py"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command: list[str], log: Path) -> int:
    with log.open("w") as stream:
        return subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT).returncode


def remap(command: list[str], old: Path, new: Path, *, graph: bool = False) -> list[str]:
    fixed_inputs = {"--trajectory", "--stereo-report", "--additional-stereo-report"}
    result = []
    preserve = False
    for item in command:
        if preserve:
            result.append(item)
            preserve = False
        else:
            result.append(str(new) + item[len(str(old)):] if item.startswith(str(old) + "/") else item)
            preserve = graph and item in fixed_inputs
    return result


def main() -> None:
    if DEST.exists() or DEST.is_symlink():
        raise ValueError(f"Refuse overwrite: {DEST}")
    old_status = {entry["name"]: entry for entry in json.loads((OLD / "run_status.json").read_text())}
    for name in CASES:
        if not old_status[name].get("trajectory_exists"):
            raise ValueError(f"SIFT baseline missing: {name}")
    import importlib.util
    spec = importlib.util.spec_from_file_location("seam_controls_new", WINDOW_SCRIPT)
    window = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(window)
    spec2 = importlib.util.spec_from_file_location("full_seam_adapter_new", ADAPTER)
    full = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(full)
    cv2.setNumThreads(2)
    DEST.mkdir(parents=True)
    sources = [Path(__file__), BRIDGE, WINDOW_SCRIPT, ADAPTER,
               ROOT / "scripts/fuse_mast3r_seam_pair_windows.py",
               ROOT / "scripts/fuse_mast3r_stereo_imu.py"]
    source_sha = {str(path): sha(path) for path in sources}
    (DEST / "manifest.json").write_text(json.dumps(dict(
        policy="Sep-28 fixed full-seam joint candidate on every complete Sep-30 frontend, no GT in solve",
        cases=CASES, source_sha256=source_sha), indent=2) + "\n")
    statuses = []
    old_pair_windows = window.previous.pair_windows
    try:
        window.previous.pair_windows = full.pair_windows
        for name in CASES:
            target = DEST / name
            target.mkdir()
            old_graph = OLD / name / "graph_fusion_report.json"
            if not old_graph.is_file():
                raise FileNotFoundError(old_graph)
            controls_dir = target / "controls"
            controls_dir.mkdir()
            print(f"{name}: full-seam controls START", flush=True)
            case = window.run_case(name, old_graph, controls_dir)
            graph_inputs = json.loads(old_graph.read_text())["inputs"]
            stereo_report = json.loads(Path(graph_inputs["stereo_report"]).read_text())
            frame_count = len(window.previous.base.stereo.load_trajectory(Path(stereo_report["trajectory"]))[0])
            count = full._case_metadata(case, frame_count)
            summary = dict(cases=[case], source_sha256=source_sha,
                external_reference_used=False, production_modified=False,
                correlated_factors_from_shared_window=True,
                candidate_policy="Sep-28 full-seam paired-40 windows; fixed joint mode on all complete cases",
                full_seam_adapter=dict(raw_interval_frames=40,
                    ba_node_offsets=[0, 5, 10, 15, 20, 25, 30, 35, 40],
                    case_counts=[count], pair_counts={name: count["pair_count"]},
                    leftover_tail_policy="explicitly_uncovered_no_duplicate_partial_edge_no_tail_fallback",
                    correlated_paired_endpoints=True, calibrated_covariance=False,
                    external_ground_truth_used=False, production_selection=False,
                    independent_summary=False))
            (controls_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
            statuses.append(dict(name=name, controls_sha256=sha(controls_dir / "summary.json"),
                                 accepted_windows=sum(bool(row.get("accepted")) for row in case["windows"])))
            print(f"{name}: controls accepted={statuses[-1]['accepted_windows']}/58", flush=True)
            (DEST / "status.json").write_text(json.dumps(statuses, indent=2) + "\n")
    finally:
        window.previous.pair_windows = old_pair_windows
    for status in statuses:
        name = status["name"]
        target = DEST / name
        old_dir = OLD / name
        commands = []
        for stage, original in old_status[name]["commands"]:
            command = remap(original, old_dir, target, graph=stage == "graph")
            command[0] = PYTHON
            if stage == "graph":
                command[1] = str(BRIDGE)
                command += ["--seam-window-controls", str(target / "controls/summary.json"),
                            "--seam-window-case", name, "--seam-window-mode", "joint"]
            commands.append((stage, command))
        status["commands"] = commands
        for stage, command in commands:
            print(f"{name}: {stage} START", flush=True)
            rc = run(command, target / f"{stage}.log")
            status[f"{stage}_rc"] = rc
            print(f"{name}: {stage} rc={rc}", flush=True)
            if rc != 0 and not (stage == "quality" and rc == 3):
                break
        status["trajectory_exists"] = (target / "trajectory_fused.csv").is_file()
        (DEST / "status.json").write_text(json.dumps(statuses, indent=2) + "\n")
    # Scoring begins only after all four trajectories are frozen.
    for status in statuses:
        if not status.get("trajectory_exists"):
            continue
        name = status["name"]
        target = DEST / name
        if sha(target / "controls/summary.json") != status["controls_sha256"]:
            raise ValueError(f"controls changed: {name}")
        if any(sha(Path(raw)) != expected for raw, expected in source_sha.items()):
            raise ValueError("source changed during experiment")
        capture = Path(next(x["capture"] for x in old_status.values() if x["name"] == name))
        status["score_rc"] = run([sys.executable, str(ROOT / "scripts/score_steamvr_slam.py"),
            "--capture", str(capture), "--estimate", str(target / "trajectory_fused.csv"),
            "--query-time-domain", "camera", "--output", str(target / "score")], target / "score.log")
        print(f"{name}: score rc={status['score_rc']}", flush=True)
        (DEST / "status.json").write_text(json.dumps(statuses, indent=2) + "\n")


if __name__ == "__main__":
    main()
