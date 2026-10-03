"""Run MASt3R main.py with one in-memory latched-reference diagnostic patch.

The source file is never written.  This narrow Sep29 take02 diagnostic keeps the
normal primary ``tracker.track(frame, ...)`` call unchanged.  Only after primary
tracking returns relocalization does it try an alternate existing keyframe:
first the previous keyframe with a fixed 12-frame age allowance, then the last
successful alternate reference while the active tail keyframe is unchanged.
"""
from __future__ import annotations

import argparse
import ast
import copy
import json
import sys
from pathlib import Path

import run_native_retry_age_diagnostic as retry_runner


TARGET_ENV = retry_runner.TARGET_ENV
REQUIRED_MAIN_SHA256 = retry_runner.REQUIRED_MAIN_SHA256
TARGET_ENV_DUMP = retry_runner.TARGET_ENV_DUMP
TARGET_LEFT_DUMP = retry_runner.TARGET_LEFT_DUMP
FIXED_PREVIOUS_KF_MAX_AGE = 12


ORIGINAL_RETRY_BODY = """
previous_idx = len(keyframes) - 2
retry_frame = create_frame(
    i, img, T_WC, img_size=dataset.img_size,
    device=device, metric_depth=metric_depth,
)
tracker.reset_idx_f2k()
_, _, retry_reloc = tracker.track(
    retry_frame,
    diagnostic_depth=diagnostic_depth,
    reference_keyframe_index=previous_idx,
    update_reference=False,
)
tracker.reset_idx_f2k()
if not retry_reloc:
    frame = retry_frame
    try_reloc = False
    tracking_anchor_idx = previous_idx
    print(f"Recovered frame {i} using previous keyframe {previous_idx}")
"""


PATCHED_RETRY_STMTS = """
if not try_reloc:
    __mast3r_latched_reference_idx = None
    __mast3r_latched_reference_tail_idx = None
if (
    try_reloc
    and os.environ.get("MAST3R_TRACK_PREVIOUS_KF_RETRY") == "1"
    and len(keyframes) > 1
):
    __mast3r_active_tail_idx = len(keyframes) - 1
    if __mast3r_latched_reference_tail_idx != __mast3r_active_tail_idx:
        __mast3r_latched_reference_idx = None
        __mast3r_latched_reference_tail_idx = None
    __mast3r_retry_reference_indices = []
    previous_idx = len(keyframes) - 2
    if i - keyframes[previous_idx].frame_id <= 12:
        __mast3r_retry_reference_indices.append(previous_idx)
    if (
        __mast3r_latched_reference_idx is not None
        and __mast3r_latched_reference_tail_idx == __mast3r_active_tail_idx
        and __mast3r_latched_reference_idx not in __mast3r_retry_reference_indices
    ):
        __mast3r_retry_reference_indices.append(__mast3r_latched_reference_idx)
    for __mast3r_retry_reference_idx in __mast3r_retry_reference_indices:
        retry_frame = create_frame(
            i, img, T_WC, img_size=dataset.img_size,
            device=device, metric_depth=metric_depth,
        )
        tracker.reset_idx_f2k()
        _, _, retry_reloc = tracker.track(
            retry_frame,
            diagnostic_depth=diagnostic_depth,
            reference_keyframe_index=__mast3r_retry_reference_idx,
            update_reference=False,
        )
        tracker.reset_idx_f2k()
        if not retry_reloc:
            frame = retry_frame
            try_reloc = False
            tracking_anchor_idx = __mast3r_retry_reference_idx
            __mast3r_latched_reference_idx = __mast3r_retry_reference_idx
            __mast3r_latched_reference_tail_idx = __mast3r_active_tail_idx
            print(
                f"Recovered frame {i} using latched reference keyframe "
                f"{__mast3r_retry_reference_idx}"
            )
            break
"""


LATCH_INIT_STMTS = """
__mast3r_latched_reference_idx = None
__mast3r_latched_reference_tail_idx = None
"""


def _dump_stmt_list(source: str) -> list[str]:
    return [ast.dump(stmt, include_attributes=False) for stmt in ast.parse(source).body]


ORIGINAL_RETRY_BODY_DUMPS = _dump_stmt_list(ORIGINAL_RETRY_BODY)
PATCHED_RETRY_NODES = ast.parse(PATCHED_RETRY_STMTS).body
LATCH_INIT_NODES = ast.parse(LATCH_INIT_STMTS).body


def _is_target_retry_guard(test: ast.AST) -> bool:
    has_env = any(ast.dump(child, include_attributes=False) == TARGET_ENV_DUMP for child in ast.walk(test))
    has_age8 = any(
        isinstance(child, ast.Compare)
        and len(child.ops) == 1
        and isinstance(child.ops[0], ast.LtE)
        and len(child.comparators) == 1
        and isinstance(child.comparators[0], ast.Constant)
        and child.comparators[0].value == 8
        and ast.dump(child.left, include_attributes=False) == TARGET_LEFT_DUMP
        for child in ast.walk(test)
    )
    return has_env and has_age8


def _insert_latch_init(body: list[ast.stmt]) -> tuple[list[ast.stmt], int]:
    new_body: list[ast.stmt] = []
    inserted = 0
    for stmt in body:
        if isinstance(stmt, ast.While) and isinstance(stmt.test, ast.Constant) and stmt.test.value is True:
            new_body.extend(copy.deepcopy(LATCH_INIT_NODES))
            inserted += 1
        new_body.append(stmt)
    return new_body, inserted


class LatchedReferencePatcher(ast.NodeTransformer):
    def __init__(self) -> None:
        self.retry_blocks = 0
        self.loop_inits = 0

    def visit_If(self, node: ast.If) -> ast.AST | list[ast.AST]:
        self.generic_visit(node)
        node.body, added = _insert_latch_init(node.body)
        self.loop_inits += added
        if not _is_target_retry_guard(node.test):
            return node
        body_dumps = [ast.dump(stmt, include_attributes=False) for stmt in node.body]
        if body_dumps != ORIGINAL_RETRY_BODY_DUMPS:
            raise ValueError("retry guard body does not match reviewed native source")
        self.retry_blocks += 1
        return [ast.copy_location(copy.deepcopy(stmt), node) for stmt in PATCHED_RETRY_NODES]

    def visit_Module(self, node: ast.Module) -> ast.Module:
        self.generic_visit(node)
        node.body, added = _insert_latch_init(node.body)
        self.loop_inits += added
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        self.generic_visit(node)
        node.body, added = _insert_latch_init(node.body)
        self.loop_inits += added
        return node


def patch_source(source_text: str) -> tuple[ast.AST, str, str]:
    patcher = LatchedReferencePatcher()
    patched_tree = patcher.visit(ast.parse(source_text))
    ast.fix_missing_locations(patched_tree)
    if patcher.retry_blocks != 1:
        raise ValueError(f"expected exactly one retry guard block, found {patcher.retry_blocks}")
    if patcher.loop_inits != 1:
        raise ValueError(f"expected exactly one tracking loop latch init, found {patcher.loop_inits}")
    patched_text = ast.unparse(patched_tree) + "\n"
    return patched_tree, patched_text, retry_runner.sha256_bytes(patched_text.encode("utf-8"))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-main", required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("native_args", nargs=argparse.REMAINDER)
    return parser


def run(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    if args.expected_source_sha256 != REQUIRED_MAIN_SHA256:
        raise ValueError(
            "expected-source-sha256 must bind the reviewed native main "
            f"{REQUIRED_MAIN_SHA256}"
        )
    native_args = retry_runner.split_native_args(args.native_args)
    source_main = Path(args.source_main).resolve()
    toolroot = source_main.parent
    source_text, original_sha = retry_runner.read_source(source_main, args.expected_source_sha256)
    patched_tree, _patched_text, patched_sha = patch_source(source_text)

    print(json.dumps({
        "schema": "mast3r_latched_reference_runtime_ast_diagnostic_v1",
        "diagnostic_only": True,
        "external_ground_truth_used": False,
        "production_promoted": False,
        "source_main": str(source_main),
        "toolroot": str(toolroot),
        "original_sha256": original_sha,
        "patched_ast_source_sha256": patched_sha,
        "previous_kf_retry_max_age": FIXED_PREVIOUS_KF_MAX_AGE,
        "native_argc": len(native_args),
    }, sort_keys=True), flush=True)

    retry_runner.exec_patched_main(source_main, patched_tree, native_args, toolroot)


if __name__ == "__main__":
    run()
