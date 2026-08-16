import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from build_pnp_gate_from_regression import build_manifest


def test_builds_hashed_gate_manifest_from_completed_regression_runs(tmp_path):
    session = tmp_path / "recordings" / "closed"
    session.mkdir(parents=True)
    run_dir = tmp_path / "reports" / "regression" / "closed" / "run_01"
    run_dir.mkdir(parents=True)
    run_report = run_dir / "run_acceptance.json"
    run_report.write_text(
        json.dumps({"session": str(session.resolve())}), encoding="utf-8"
    )
    loop_log = run_dir / "auto_loop.log"
    loop_log.write_text("loop evidence", encoding="utf-8")
    regression_manifest = tmp_path / "config" / "regression_manifest.json"
    regression_manifest.parent.mkdir()
    regression_manifest.write_text(
        json.dumps(
            {
                "datasets": [
                    {
                        "id": "closed",
                        "session": str(session),
                        "expected_loop": True,
                    }
                ],
                "safety_controls": [],
            }
        ),
        encoding="utf-8",
    )
    summary = tmp_path / "reports" / "regression" / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "mode": "REPEATED_REGRESSION",
                "datasets": [
                    {
                        "id": "closed",
                        "runs": [
                            {
                                "repetition": 1,
                                "report": str(run_report.relative_to(tmp_path)),
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    roles = tmp_path / "roles.json"
    roles.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "roles": {"closed": "validation"},
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "gate" / "manifest.json"

    result = build_manifest(regression_manifest, summary, roles, output)

    assert result["schema_version"] == 1
    assert result["requirements"]["min_negative_runs"] == 2
    assert result["datasets"] == [
        {
            "id": "closed-run-01",
            "role": "validation",
            "session": str(session.resolve()),
            "expected_loop": True,
            "loop_log": str(loop_log.resolve()),
            "loop_log_sha256": hashlib.sha256(loop_log.read_bytes()).hexdigest(),
            "run_report": str(run_report.resolve()),
            "run_report_sha256": hashlib.sha256(run_report.read_bytes()).hexdigest(),
        }
    ]
    assert json.loads(output.read_text(encoding="utf-8")) == result


def test_rejects_unassigned_dataset_role(tmp_path):
    regression_manifest = tmp_path / "regression_manifest.json"
    regression_manifest.write_text(
        json.dumps(
            {
                "datasets": [
                    {"id": "closed", "session": "unused", "expected_loop": True}
                ]
            }
        ),
        encoding="utf-8",
    )
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps({"mode": "REPEATED_REGRESSION", "datasets": []}),
        encoding="utf-8",
    )
    roles = tmp_path / "roles.json"
    roles.write_text(
        json.dumps({"schema_version": 1, "roles": {}}), encoding="utf-8"
    )

    try:
        build_manifest(regression_manifest, summary, roles, tmp_path / "out.json")
    except ValueError as exc:
        assert "missing predeclared role: closed" in str(exc)
    else:
        raise AssertionError("missing role was accepted")


def test_rejects_incomplete_regression_instead_of_freezing_partial_gate(tmp_path):
    regression_manifest = tmp_path / "regression_manifest.json"
    regression_manifest.write_text(
        json.dumps(
            {
                "thresholds": {"required_repetitions_per_dataset": 3},
                "datasets": [
                    {"id": "closed", "session": "unused", "expected_loop": True}
                ],
            }
        ),
        encoding="utf-8",
    )
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "mode": "REPEATED_REGRESSION",
                "datasets": [{"id": "closed", "runs": []}],
            }
        ),
        encoding="utf-8",
    )
    roles = tmp_path / "roles.json"
    roles.write_text(
        json.dumps(
            {"schema_version": 1, "roles": {"closed": "validation"}}
        ),
        encoding="utf-8",
    )

    try:
        build_manifest(regression_manifest, summary, roles, tmp_path / "out.json")
    except ValueError as exc:
        assert "regression run 1 is missing" in str(exc)
    else:
        raise AssertionError("partial regression was accepted")
