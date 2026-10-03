import ast
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / ".planning/frontend_observation_20260930/probe_pointmap_match_stages.py"


def _tree():
    return ast.parse(SCRIPT.read_text(encoding="utf-8"), filename=str(SCRIPT))


def _function(tree, name):
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"function not found: {name}")


def _call_names(node):
    names = []
    for item in ast.walk(node):
        if isinstance(item, ast.Call):
            func = item.func
            if isinstance(func, ast.Name):
                names.append(func.id)
            elif isinstance(func, ast.Attribute):
                names.append(func.attr)
    return names


def test_probe_defaults_to_stereo_enabled():
    probe = _function(_tree(), "probe")

    assert [arg.arg for arg in probe.args.kwonlyargs] == ["with_stereo"]
    assert len(probe.args.kw_defaults) == 1
    assert isinstance(probe.args.kw_defaults[0], ast.Constant)
    assert probe.args.kw_defaults[0].value is True


def test_probe_pointmap_only_branch_skips_stereo_and_reports_metric_unavailable():
    probe = _function(_tree(), "probe")
    branch = next(
        node
        for node in ast.walk(probe)
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "with_stereo"
    )

    assert "stereo_pointmap_checks" in _call_names(ast.Module(body=branch.body, type_ignores=[]))
    assert "stereo_pointmap_checks" not in _call_names(ast.Module(body=branch.orelse, type_ignores=[]))
    constants = {
        node.value
        for node in ast.walk(ast.Module(body=branch.orelse, type_ignores=[]))
        if isinstance(node, ast.Constant)
    }
    assert "pointmap_only" in constants
    assert "stereo_pointmap_checks_skipped_by_pointmap_only" in constants
    assert False in constants


def test_cli_defines_pointmap_only_and_propagates_to_probe():
    main = _function(_tree(), "main")

    add_argument_calls = [
        node
        for node in ast.walk(main)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
    ]
    pointmap_arg = next(
        call
        for call in add_argument_calls
        if call.args
        and isinstance(call.args[0], ast.Constant)
        and call.args[0].value == "--pointmap-only"
    )
    action_keyword = next(keyword for keyword in pointmap_arg.keywords if keyword.arg == "action")
    assert isinstance(action_keyword.value, ast.Constant)
    assert action_keyword.value.value == "store_true"

    probe_call = next(
        node
        for node in ast.walk(main)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "probe"
    )
    with_stereo = next(keyword.value for keyword in probe_call.keywords if keyword.arg == "with_stereo")
    assert isinstance(with_stereo, ast.UnaryOp)
    assert isinstance(with_stereo.op, ast.Not)
    assert isinstance(with_stereo.operand, ast.Attribute)
    assert with_stereo.operand.attr == "pointmap_only"


def test_cli_pointmap_only_output_exposes_metric_unavailable_fields():
    main = _function(_tree(), "main")
    pointmap_if = next(
        node
        for node in ast.walk(main)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Attribute)
        and node.test.attr == "pointmap_only"
    )
    constants = {
        node.value
        for node in ast.walk(ast.Module(body=pointmap_if.body, type_ignores=[]))
        if isinstance(node, ast.Constant)
    }

    assert "pointmap_only" in constants
    assert "metric_available" in constants
    assert "metric_unavailable_reason" in constants
