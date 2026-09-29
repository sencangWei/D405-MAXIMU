import importlib.util
from pathlib import Path


PATH = Path(__file__).resolve().parents[1]/".planning/frontend_pivot_20260929/run_seam_candidate.py"
SPEC = importlib.util.spec_from_file_location("run_seam_candidate", PATH)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_only_candidate_frontend_and_output_paths_are_remapped():
    old_frontend = Path("/repo/frozen/mast3r")
    new_frontend = Path("/repo/new/mast3r")
    old_stage = Path("/repo/frozen/stage")
    new_stage = Path("/repo/new/stage")
    command = ["tool", "--trajectory", str(old_frontend/"trajectory_imu_metric.csv"),
               "--output", str(old_stage/"trajectory_graph.csv"),
               "--reference", "/repo/external/tracker.csv"]
    actual = mod.remap_command(command, old_frontend, new_frontend, old_stage, new_stage)
    assert actual == ["tool", "--trajectory", str(new_frontend/"trajectory_imu_metric.csv"),
                      "--output", str(new_stage/"trajectory_graph.csv"),
                      "--reference", "/repo/external/tracker.csv"]
