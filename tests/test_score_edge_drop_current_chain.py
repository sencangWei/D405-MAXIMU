"""Frozen downstream command rewriting changes only allowed inputs/outputs."""
import importlib.util
from pathlib import Path

import pytest


MODULE = Path(__file__).resolve().parents[1] / ".planning/metric_window_bundle_20260928/score_edge_drop_current_chain.py"
spec = importlib.util.spec_from_file_location("score_edge_drop_current_chain", MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_exact_five_stage_order_and_input_replacement():
    old = Path("/repo/frozen")
    new = Path("/repo/candidate")
    stage = Path("/repo/front/mast3r")
    graph = ["python", "graph.py", "--trajectory", "/repo/original.csv",
             "--imu-scale-report", "/repo/original.json", "--keyframe-dir", "/repo/kf",
             "--output", "/repo/frozen/trajectory_graph.csv", "--fixed", "/repo/fixed"]
    manifest = {"commands": [("graph", graph)] + [(name, ["python", name,
                       "--output", f"/repo/frozen/{name}.json"])
                       for name in module.STAGES[1:]]}
    commands = module.commands_for(manifest, old, new, stage, Path("/repo/front/keyframes"))
    args = commands[0][1]
    assert args[args.index("--trajectory") + 1] == str(stage / "trajectory_imu_metric.csv")
    assert args[args.index("--keyframe-dir") + 1] == "/repo/front/keyframes"
    assert args[args.index("--output") + 1] == "/repo/candidate/trajectory_graph.csv"
    assert args[args.index("--fixed") + 1] == "/repo/fixed"
    assert tuple(name for name, _ in commands) == module.STAGES


def test_missing_or_repeated_input_flag_rejected():
    with pytest.raises(ValueError, match="exactly one"):
        module.replace_flag(["python", "--trajectory", "a", "--trajectory", "b"],
                            "--trajectory", "c")


def test_extra_stereo_report_only_changes_graph_command():
    old, new = Path("/repo/frozen"), Path("/repo/candidate")
    graph = ["python", "graph.py", "--trajectory", "raw.csv",
             "--imu-scale-report", "raw.json", "--keyframe-dir", "raw_kf"]
    manifest = {"commands": [("graph", graph)] + [(name, ["python", name])
                      for name in module.STAGES[1:]]}
    commands = module.commands_for(manifest, old, new, Path("/repo/front"),
                                   Path("/repo/kf"), Path("/repo/loop.json"))
    assert commands[0][1][-2:] == ["--additional-stereo-report", "/repo/loop.json"]
    assert all("--additional-stereo-report" not in command
               for _, command in commands[1:])


def test_candidate_stereo_reports_rewrite_exact_four_graph_sources():
    old, new = Path("/repo/frozen"), Path("/repo/candidate")
    graph = ["python", "graph.py", "--trajectory", "raw.csv",
             "--imu-scale-report", "raw.json", "--keyframe-dir", "raw_kf",
             "--stereo-report", "/old/stereo_scale_bidirectional_report.json"]
    for name in ("long_hops", "dense10hz", "multisecond"):
        graph += ["--additional-stereo-report", f"/old/stereo_scale_{name}_report.json"]
    manifest = {"commands": [("graph", graph)] + [(name, ["python", name])
                      for name in module.STAGES[1:]]}
    commands = module.commands_for(manifest, old, new, Path("/repo/front"),
                                   Path("/repo/kf"), candidate_stereo_dir=Path("/new/stereo"))
    rewritten = [value for i, value in enumerate(commands[0][1]) if i and
                 commands[0][1][i - 1] in ("--stereo-report", "--additional-stereo-report")]
    assert len(rewritten) == 4
    assert all(value.startswith("/new/stereo/") for value in rewritten)
    assert all("--stereo-report" not in command and "--additional-stereo-report" not in command
               for _, command in commands[1:])
