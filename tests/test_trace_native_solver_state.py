import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".planning/metric_window_bundle_20260928"))
from trace_native_solver_state import with_output_paths


def test_only_output_targets_are_replaced():
    args = ["--session", "raw", "--output", "old.csv", "--report", "old.json"]
    assert with_output_paths(args, Path("new.csv"), Path("new.json")) == [
        "--session", "raw", "--output", "new.csv", "--report", "new.json"]
    assert args[3] == "old.csv"


def test_missing_or_duplicate_output_fails_closed():
    with pytest.raises(ValueError, match="single --output"):
        with_output_paths(["--report", "old.json"], Path("x"), Path("y"))
    with pytest.raises(ValueError, match="single --output"):
        with_output_paths(["--output", "a", "--output", "b", "--report", "c"],
                          Path("x"), Path("y"))
