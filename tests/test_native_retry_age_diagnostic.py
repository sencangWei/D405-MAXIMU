import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


MODULE = (
    Path(__file__).resolve().parents[1]
    / ".planning/dual_ir_regression_25_20261002/sep29_frontier_local_pairs_20261003"
    / "run_native_retry_age_diagnostic.py"
)
spec = importlib.util.spec_from_file_location("run_native_retry_age_diagnostic", MODULE)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


SOURCE = """
import os
try_reloc = False
if (
    try_reloc
    and os.environ.get("MAST3R_TRACK_PREVIOUS_KF_RETRY") == "1"
    and len(keyframes) > 1
    and i - keyframes[len(keyframes) - 2].frame_id <= 8
):
    recovered = True
"""


def test_patch_source_changes_exact_retry_age_constant_only():
    _, patched, patched_sha = runner.patch_source(SOURCE, 12)
    assert "<= 12" in patched
    assert "<= 8" not in patched
    assert patched_sha == runner.sha256_bytes(patched.encode("utf-8"))


def test_patch_source_rejects_zero_or_ambiguous_matches():
    with pytest.raises(ValueError, match="exactly one"):
        runner.patch_source("if True:\n    pass\n", 12)
    with pytest.raises(ValueError, match="exactly one"):
        runner.patch_source(SOURCE + "\n" + SOURCE, 12)
    ambiguous_one_guard = SOURCE.replace(
        "i - keyframes[len(keyframes) - 2].frame_id <= 8",
        "(i - keyframes[len(keyframes) - 2].frame_id <= 8) and (i - keyframes[len(keyframes) - 2].frame_id <= 8)",
    )
    with pytest.raises(ValueError, match="ambiguous"):
        runner.patch_source(ambiguous_one_guard, 12)


def test_patch_source_requires_exact_env_and_left_expression():
    wrong_env = SOURCE.replace("os.environ.get", "config.environ.get")
    with pytest.raises(ValueError, match="exactly one"):
        runner.patch_source(wrong_env, 12)
    wrong_left = SOURCE.replace(
        "i - keyframes[len(keyframes) - 2].frame_id <= 8",
        "i - keyframes[-2].frame_id <= 8",
    )
    with pytest.raises(ValueError, match="exactly one"):
        runner.patch_source(wrong_left, 12)


def test_source_hash_mismatch_fails_closed(tmp_path):
    source = tmp_path / "main.py"
    source.write_text(SOURCE, encoding="utf-8")
    with pytest.raises(ValueError, match="source SHA mismatch"):
        runner.read_source(source, "0" * 64)


def test_read_source_accepts_exact_hash(tmp_path):
    source = tmp_path / "main.py"
    source.write_text(SOURCE, encoding="utf-8")
    text, digest = runner.read_source(source, runner.sha256_bytes(SOURCE.encode("utf-8")))
    assert text == SOURCE
    assert len(digest) == 64


def test_native_args_require_separator_and_preserve_native_help():
    with pytest.raises(ValueError, match="-- separator"):
        runner.split_native_args(["--help"])
    assert runner.split_native_args(["--", "--help", "--config", "cfg.yaml"]) == [
        "--help", "--config", "cfg.yaml"
    ]


def test_parser_accepts_retry_choices_and_remainder():
    args = runner.build_arg_parser().parse_args([
        "--source-main", "main.py",
        "--expected-source-sha256", "a" * 64,
        "--retry-max-age", "12",
        "--",
        "--dataset", "d",
    ])
    assert args.retry_max_age == 12
    assert args.native_args == ["--", "--dataset", "d"]
    with pytest.raises(SystemExit):
        runner.build_arg_parser().parse_args([
            "--source-main", "main.py",
            "--expected-source-sha256", "a" * 64,
            "--retry-max-age", "9",
            "--",
        ])


def test_run_requires_reviewed_main_sha256():
    with pytest.raises(ValueError, match="reviewed native main"):
        runner.run([
            "--source-main", "main.py",
            "--expected-source-sha256", "a" * 64,
            "--retry-max-age", "12",
            "--",
            "--help",
        ])


def test_exec_binds_fresh_main_module_for_dataclass_and_argv(tmp_path, monkeypatch):
    source = tmp_path / "main.py"
    source.write_text(
        SOURCE
        + """
from dataclasses import dataclass
import sys
@dataclass
class Payload:
    value: int
RESULT = {
    "module": Payload.__module__,
    "file": __file__,
    "argv": sys.argv[:],
    "registered": sys.modules["__main__"].__file__,
}
""",
        encoding="utf-8",
    )
    text, _ = runner.read_source(source, runner.sha256_bytes(source.read_bytes()))
    tree, _, _ = runner.patch_source(text, 12)
    old_main = sys.modules.get("__main__")
    old_argv = sys.argv[:]
    old_path = sys.path[:]
    monkeypatch.delenv(runner.TARGET_ENV, raising=False)
    module = runner.exec_patched_main(source, tree, ["--native-help"], tmp_path)
    assert module.RESULT["module"] == "__main__"
    assert module.RESULT["file"] == str(source)
    assert module.RESULT["registered"] == str(source)
    assert module.RESULT["argv"] == [str(source), "--native-help"]
    assert sys.modules.get("__main__") is old_main
    assert sys.argv == old_argv
    assert sys.path == old_path
    assert runner.TARGET_ENV not in os.environ


def test_exec_rejects_conflicting_retry_env(tmp_path, monkeypatch):
    source = tmp_path / "main.py"
    source.write_text(SOURCE, encoding="utf-8")
    text, _ = runner.read_source(source, runner.sha256_bytes(source.read_bytes()))
    tree, _, _ = runner.patch_source(text, 12)
    monkeypatch.setenv(runner.TARGET_ENV, "0")
    with pytest.raises(ValueError, match="must be unset or '1'"):
        runner.exec_patched_main(source, tree, [], tmp_path)
    assert os.environ[runner.TARGET_ENV] == "0"


def test_exec_main_identity_supports_spawn_pickle_smoke(tmp_path):
    source = tmp_path / "main.py"
    source.write_text(
        SOURCE
        + """
from dataclasses import dataclass
import multiprocessing as mp
import sys

@dataclass
class Payload:
    value: int

def child(payload, queue):
    queue.put((payload.__class__.__module__, payload.value))

if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    queue = mp.Queue()
    proc = mp.Process(target=child, args=(Payload(7), queue))
    proc.start()
    proc.join(10)
    if proc.exitcode != 0:
        raise RuntimeError(f"spawn child exit {proc.exitcode}")
    module_name, value = queue.get(timeout=5)
    if value != 7 or module_name not in {"__main__", "__mp_main__"}:
        raise RuntimeError((module_name, value))
""",
        encoding="utf-8",
    )
    code = (
        "import importlib.util, pathlib; "
        f"p=pathlib.Path({str(MODULE)!r}); "
        "s=importlib.util.spec_from_file_location('runner', p); "
        "r=importlib.util.module_from_spec(s); s.loader.exec_module(r); "
        f"src=pathlib.Path({str(source)!r}); "
        "text,_=r.read_source(src, r.sha256_bytes(src.read_bytes())); "
        "tree,_,_=r.patch_source(text, 12); "
        "r.exec_patched_main(src, tree, [], src.parent)"
    )
    result = subprocess.run([sys.executable, "-c", code], text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
