"""Run MASt3R main.py with one in-memory retry-age AST diagnostic patch.

The source file is never written.  This is a narrow Sep29 take02 diagnostic:
only the ``MAST3R_TRACK_PREVIOUS_KF_RETRY`` previous-keyframe age guard may
change, and only from the literal 8-frame guard to the requested value.
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import os
import sys
import types
from pathlib import Path


TARGET_ENV = "MAST3R_TRACK_PREVIOUS_KF_RETRY"
REQUIRED_MAIN_SHA256 = "52fa042caa2157dc86c73af0b7c3d4db180b9854c2da5c412015f8c11a44ae95"
TARGET_LEFT_EXPR = "i - keyframes[len(keyframes) - 2].frame_id"
TARGET_ENV_EXPR = 'os.environ.get("MAST3R_TRACK_PREVIOUS_KF_RETRY") == "1"'


def _dump_expr(expr: str) -> str:
    return ast.dump(ast.parse(expr, mode="eval").body, include_attributes=False)


TARGET_LEFT_DUMP = _dump_expr(TARGET_LEFT_EXPR)
TARGET_ENV_DUMP = _dump_expr(TARGET_ENV_EXPR)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_source(path: Path, expected_sha256: str) -> tuple[str, str]:
    data = path.read_bytes()
    actual = sha256_bytes(data)
    if actual != expected_sha256:
        raise ValueError(f"source SHA mismatch: expected {expected_sha256}, got {actual}")
    return data.decode("utf-8"), actual


def _has_exact_env_guard(node: ast.AST) -> bool:
    return any(ast.dump(child, include_attributes=False) == TARGET_ENV_DUMP for child in ast.walk(node))


def _candidate_compares(node: ast.AST, expected_constant: int) -> list[ast.Compare]:
    matches: list[ast.Compare] = []
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Compare)
            and len(child.ops) == 1
            and isinstance(child.ops[0], ast.LtE)
            and len(child.comparators) == 1
            and isinstance(child.comparators[0], ast.Constant)
            and child.comparators[0].value == expected_constant
            and ast.dump(child.left, include_attributes=False) == TARGET_LEFT_DUMP
        ):
            matches.append(child)
    return matches


class RetryAgePatcher(ast.NodeTransformer):
    def __init__(self, retry_max_age: int, *, old_value: int = 8):
        self.retry_max_age = retry_max_age
        self.old_value = old_value
        self.matches = 0

    def visit_If(self, node: ast.If) -> ast.AST:
        self.generic_visit(node)
        if not _has_exact_env_guard(node.test):
            return node
        compares = _candidate_compares(node.test, self.old_value)
        if not compares:
            return node
        if len(compares) > 1:
            raise ValueError("ambiguous retry-age comparators in one guard")
        self.matches += 1
        compare = compares[0]
        compare.comparators[0] = ast.copy_location(
            ast.Constant(value=self.retry_max_age), compare.comparators[0]
        )
        return node


def patch_source(source_text: str, retry_max_age: int) -> tuple[ast.AST, str, str]:
    original_tree = ast.parse(source_text)
    original_dump = ast.dump(original_tree, include_attributes=False)
    patcher = RetryAgePatcher(retry_max_age)
    patched_tree = patcher.visit(ast.parse(source_text))
    ast.fix_missing_locations(patched_tree)
    if patcher.matches != 1:
        raise ValueError(f"expected exactly one retry-age guard match, found {patcher.matches}")

    verifier = RetryAgePatcher(8, old_value=retry_max_age)
    restored = verifier.visit(copy.deepcopy(patched_tree))
    ast.fix_missing_locations(restored)
    if verifier.matches != 1 or ast.dump(restored, include_attributes=False) != original_dump:
        raise ValueError("patched AST differs by more than the retry-age constant")

    patched_text = ast.unparse(patched_tree) + "\n"
    return patched_tree, patched_text, sha256_bytes(patched_text.encode("utf-8"))


def exec_patched_main(source_main: Path, patched_tree: ast.AST, native_args: list[str], toolroot: Path) -> types.ModuleType:
    old_argv = sys.argv[:]
    old_path = sys.path[:]
    had_env = TARGET_ENV in os.environ
    old_env = os.environ.get(TARGET_ENV)
    if had_env and old_env != "1":
        raise ValueError(f"{TARGET_ENV} must be unset or '1', got {old_env!r}")
    sys.path.insert(0, str(toolroot))
    sys.argv = [str(source_main), *native_args]
    os.environ[TARGET_ENV] = "1"
    module = types.ModuleType("__main__")
    module.__file__ = str(source_main)
    module.__package__ = None
    module.__cached__ = None
    module.__spec__ = None
    previous_main = sys.modules.get("__main__")
    sys.modules["__main__"] = module
    try:
        code = compile(patched_tree, str(source_main), "exec")
        exec(code, module.__dict__)
        return module
    finally:
        if previous_main is None:
            sys.modules.pop("__main__", None)
        else:
            sys.modules["__main__"] = previous_main
        sys.argv = old_argv
        sys.path[:] = old_path
        if had_env:
            os.environ[TARGET_ENV] = old_env or ""
        else:
            os.environ.pop(TARGET_ENV, None)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-main", required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--retry-max-age", required=True, type=int, choices=(8, 12))
    parser.add_argument("native_args", nargs=argparse.REMAINDER)
    return parser


def split_native_args(native_args: list[str]) -> list[str]:
    if not native_args or native_args[0] != "--":
        raise ValueError("native MASt3R arguments must follow an explicit -- separator")
    return native_args[1:]


def run(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    if args.expected_source_sha256 != REQUIRED_MAIN_SHA256:
        raise ValueError(
            "expected-source-sha256 must bind the reviewed native main "
            f"{REQUIRED_MAIN_SHA256}"
        )
    native_args = split_native_args(args.native_args)
    source_main = Path(args.source_main).resolve()
    toolroot = source_main.parent
    source_text, original_sha = read_source(source_main, args.expected_source_sha256)
    patched_tree, _patched_text, patched_sha = patch_source(source_text, args.retry_max_age)

    print(json.dumps({
        "schema": "mast3r_retry_age_runtime_ast_diagnostic_v1",
        "diagnostic_only": True,
        "external_ground_truth_used": False,
        "production_promoted": False,
        "source_main": str(source_main),
        "toolroot": str(toolroot),
        "original_sha256": original_sha,
        "patched_ast_source_sha256": patched_sha,
        "old_retry_max_age": 8,
        "new_retry_max_age": int(args.retry_max_age),
        "native_argc": len(native_args),
    }, sort_keys=True), flush=True)

    exec_patched_main(source_main, patched_tree, native_args, toolroot)


if __name__ == "__main__":
    run()
