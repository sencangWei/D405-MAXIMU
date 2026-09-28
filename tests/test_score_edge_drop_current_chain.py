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
