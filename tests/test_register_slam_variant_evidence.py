import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from register_slam_variant_evidence import register_variant_evidence


def test_register_variant_evidence_updates_only_selected_hidden_dataset(tmp_path):
    variants = {}
    for variant in ("raw_vins", "auto_loop", "depth_plane"):
        variants[variant] = {
            "run_report": f"/{variant}/run.json",
            "run_report_sha256": variant * 4,
            "trajectory": f"/{variant}/trajectory.csv",
            "trajectory_sha256": variant * 5,
            "ground_truth_report": f"/{variant}/metrics.json",
            "ground_truth_report_sha256": variant * 6,
        }
    truth = tmp_path / "truth.csv"
    truth.write_text("truth", encoding="utf-8")
    fragment = {
        "dataset_id": "hidden-01",
        "truth_usage": "post_run_scoring_only",
        "ground_truth": str(truth),
        "ground_truth_sha256": hashlib.sha256(truth.read_bytes()).hexdigest(),
        "variant_reports": variants,
    }
    fragment_path = tmp_path / "fragment.json"
    fragment_path.write_text(json.dumps(fragment), encoding="utf-8")
    manifest = {
        "schema_version": 3,
        "release_variant": "auto_loop",
        "datasets": [
            {
                "id": "hidden-01",
                "role": "hidden_test",
                "motion": "straight_open",
                "expected_loop": False,
                "session": "/recording",
                "acceptance_sha256": "acceptance",
            },
            {"id": "development", "role": "development"},
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output_path = tmp_path / "registered.json"

    registered = register_variant_evidence(
        manifest_path=manifest_path,
        evidence_fragment_path=fragment_path,
        output_path=output_path,
    )

    hidden = registered["datasets"][0]
    assert hidden["variant_reports"] == variants
    assert hidden["external_ground_truth"] == str(truth)
    assert hidden["external_ground_truth_sha256"] == fragment["ground_truth_sha256"]
    assert hidden["run_report"] == variants["auto_loop"]["run_report"]
    assert hidden["ground_truth_report"] == variants["auto_loop"][
        "ground_truth_report"
    ]
    assert registered["datasets"][1] == manifest["datasets"][1]
    assert output_path.is_file()
    assert not manifest["datasets"][0].get("variant_reports")


def test_register_variant_evidence_refuses_non_hidden_dataset(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "release_variant": "auto_loop",
                "datasets": [{"id": "dev", "role": "development"}],
            }
        ),
        encoding="utf-8",
    )
    fragment_path = tmp_path / "fragment.json"
    fragment_path.write_text(
        json.dumps(
            {
                "dataset_id": "dev",
                "truth_usage": "post_run_scoring_only",
                "variant_reports": {
                    variant: {} for variant in ("raw_vins", "auto_loop", "depth_plane")
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="hidden_test"):
        register_variant_evidence(
            manifest_path=manifest_path,
            evidence_fragment_path=fragment_path,
            output_path=tmp_path / "output.json",
        )
