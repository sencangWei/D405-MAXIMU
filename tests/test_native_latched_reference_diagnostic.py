import importlib.util
import os
import sys
from pathlib import Path

import pytest


BASE = (
    Path(__file__).resolve().parents[1]
    / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
)
sys.path.insert(0, str(BASE))
MODULE = BASE / "run_native_latched_reference_diagnostic.py"
spec = importlib.util.spec_from_file_location("run_native_latched_reference_diagnostic", MODULE)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


SOURCE = f"""
import os
import sys

class Keyframe:
    def __init__(self, frame_id):
        self.frame_id = frame_id

class Frame:
    def __init__(self, frame_id):
        self.frame_id = frame_id

class Dataset:
    img_size = (640, 480)

class Tracker:
    def __init__(self, primary_reloc, retry_reloc):
        self.primary_reloc = list(primary_reloc)
        self.retry_reloc = dict(retry_reloc)
        self.calls = []
        self.reset_count = 0

    def reset_idx_f2k(self):
        self.reset_count += 1

    def track(self, frame, diagnostic_depth=None, reference_keyframe_index=None, update_reference=True):
        self.calls.append((frame.frame_id, reference_keyframe_index, update_reference))
        if reference_keyframe_index is None:
            reloc = self.primary_reloc.pop(0)
        else:
            reloc = self.retry_reloc.get((frame.frame_id, reference_keyframe_index), True)
        return False, [], reloc

def create_frame(i, img, T_WC, img_size=None, device=None, metric_depth=None):
    return Frame(i)

def run_sequence(primary_reloc, retry_reloc, start=588, stop=590, append_tail_at=()):
    dataset = Dataset()
    keyframes = [Keyframe(0), Keyframe(576), Keyframe(587)]
    tracker = Tracker(primary_reloc, retry_reloc)
    device = "cpu"
    diagnostic_depth = None
    metric_depth = None
    T_WC = object()
    img = object()
    i = start
    records = []
    while True:
        if i > stop:
            break
        if i in append_tail_at:
            keyframes.append(Keyframe(i - 1))
        add_new_kf = False
        tracked = False
        tracking_anchor_idx = len(keyframes) - 1
        add_new_kf, match_info, try_reloc = tracker.track(
            create_frame(i, img, T_WC, img_size=dataset.img_size, device=device, metric_depth=metric_depth),
            diagnostic_depth=diagnostic_depth
        )
        if (
            try_reloc
            and os.environ.get("MAST3R_TRACK_PREVIOUS_KF_RETRY") == "1"
            and len(keyframes) > 1
            and i - keyframes[len(keyframes) - 2].frame_id <= 8
        ):
            {runner.ORIGINAL_RETRY_BODY.replace(chr(10), chr(10) + "            ")}
        if not try_reloc:
            tracked = True
        records.append((i, try_reloc, tracked, tracking_anchor_idx, tracker.reset_count))
        i += 1
    return tracker.calls, records
"""


def _patched_namespace():
    tree, _, _ = runner.patch_source(SOURCE)
    namespace = {"__name__": "patched_test"}
    exec(compile(tree, "<patched>", "exec"), namespace)
    return namespace


def test_actual_reviewed_main_is_patchable():
    main_path = Path("/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/main.py")
    text, digest = runner.retry_runner.read_source(main_path, runner.REQUIRED_MAIN_SHA256)
    assert digest == runner.REQUIRED_MAIN_SHA256
    _, patched, patched_sha = runner.patch_source(text)
    assert "previous_kf_retry_max_age" not in patched
    assert "i - keyframes[previous_idx].frame_id <= 12" in patched
    assert len(patched_sha) == 64


def test_latched_reference_seeds_age12_then_reuses_after_age13(monkeypatch):
    monkeypatch.setenv(runner.TARGET_ENV, "1")
    ns = _patched_namespace()
    calls, records = ns["run_sequence"](
        [True, True],
        {(588, 1): False, (589, 1): False},
        start=588,
        stop=589,
    )
    assert calls == [
        (588, None, True),
        (588, 1, False),
        (589, None, True),
        (589, 1, False),
    ]
    assert records == [
        (588, False, True, 1, 2),
        (589, False, True, 1, 4),
    ]


def test_primary_success_clears_latch(monkeypatch):
    monkeypatch.setenv(runner.TARGET_ENV, "1")
    ns = _patched_namespace()
    calls, records = ns["run_sequence"](
        [True, False, True],
        {(588, 1): False, (590, 1): False},
        start=588,
        stop=590,
    )
    assert (590, 1, False) not in calls
    assert records[-1] == (590, True, False, 2, 2)


def test_tail_change_clears_stale_latch(monkeypatch):
    monkeypatch.setenv(runner.TARGET_ENV, "1")
    ns = _patched_namespace()
    calls, _records = ns["run_sequence"](
        [True, True],
        {(588, 1): False, (589, 2): True, (589, 1): False},
        start=588,
        stop=589,
        append_tail_at={589},
    )
    assert (589, 2, False) in calls
    assert (589, 1, False) not in calls


def test_failed_retry_does_not_accept_or_latch(monkeypatch):
    monkeypatch.setenv(runner.TARGET_ENV, "1")
    ns = _patched_namespace()
    calls, records = ns["run_sequence"](
        [True, True],
        {(588, 1): True, (589, 1): False},
        start=588,
        stop=589,
    )
    assert calls == [
        (588, None, True),
        (588, 1, False),
        (589, None, True),
    ]
    assert records == [
        (588, True, False, 2, 2),
        (589, True, False, 2, 2),
    ]


def test_env_disabled_preserves_primary_only(monkeypatch):
    monkeypatch.delenv(runner.TARGET_ENV, raising=False)
    ns = _patched_namespace()
    calls, records = ns["run_sequence"]([True], {(588, 1): False}, start=588, stop=588)
    assert calls == [(588, None, True)]
    assert records == [(588, True, False, 2, 0)]


def test_patch_rejects_changed_source_blocks():
    with pytest.raises(ValueError, match="exactly one retry guard"):
        runner.patch_source("while True:\n    break\n")
    ambiguous = SOURCE + "\n" + SOURCE
    with pytest.raises(ValueError, match="exactly one retry guard"):
        runner.patch_source(ambiguous)
    changed_body = SOURCE.replace("tracker.reset_idx_f2k()", "tracker.reset_idx_f2k_changed()", 1)
    with pytest.raises(ValueError, match="body does not match"):
        runner.patch_source(changed_body)


def test_run_requires_reviewed_main_sha256():
    with pytest.raises(ValueError, match="reviewed native main"):
        runner.run([
            "--source-main", "main.py",
            "--expected-source-sha256", "0" * 64,
            "--",
            "--help",
        ])


def test_exec_identity_and_globals_restore(tmp_path, monkeypatch):
    source = tmp_path / "main.py"
    source.write_text(
        SOURCE
        + """
RESULT = {"file": __file__, "argv": sys.argv[:], "registered": sys.modules["__main__"].__file__}
""",
        encoding="utf-8",
    )
    text, _ = runner.retry_runner.read_source(source, runner.retry_runner.sha256_bytes(source.read_bytes()))
    tree, _, _ = runner.patch_source(text)
    old_main = sys.modules.get("__main__")
    old_argv = sys.argv[:]
    old_path = sys.path[:]
    monkeypatch.delenv(runner.TARGET_ENV, raising=False)
    module = runner.retry_runner.exec_patched_main(source, tree, ["--native"], tmp_path)
    assert module.RESULT == {
        "file": str(source),
        "argv": [str(source), "--native"],
        "registered": str(source),
    }
    assert sys.modules.get("__main__") is old_main
    assert sys.argv == old_argv
    assert sys.path == old_path
    assert runner.TARGET_ENV not in os.environ
