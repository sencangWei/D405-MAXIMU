"""Complete score entrypoint on synthetic, external-reference-only data."""
import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from score_steamvr_slam import score
from test_steamvr_scoring_contract import case


@pytest.mark.parametrize("error_m,expected_code", [(0.0, 0), (0.05, 3)])
def test_score_does_not_alter_estimate_and_keeps_failed_precision(tmp_path, error_m, expected_code):
    paths = case(tmp_path)
    estimate, _, _, _, config, reference, _ = paths
    rows = list(csv.DictReader(estimate.open()))
    with estimate.open("w", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(["t_sec", "x", "y", "z", "qw", "qx", "qy", "qz"])
        for i, row in enumerate(rows):
            # A single non-rigid spike cannot be removed by global SE3.
            writer.writerow([row["t_sec"], float(row["t_sec"]) - 1_788_000_000.0,
                error_m if i == 15 else 0, 0, 1, 0, 0, 0])
    # Default RPE delta30 requires more than30 points; use31 instead of fixture30.
    with estimate.open("a", newline="") as fp:
        csv.writer(fp).writerow([1_788_000_002.0, 2.0, 0, 0, 1, 0, 0, 0])
    before = estimate.read_bytes()
    output = tmp_path / "score"
    status = score(tmp_path, estimate, output, reference, config, "camera")
    assert status == expected_code
    assert estimate.read_bytes() == before
    manifest = json.loads((output / "workflow_manifest.json").read_text())
    assert manifest["estimate_unchanged"] is True
    assert manifest["slam_supervision"] is False
    assert (output / "precision.png").exists()
    assert (output / "precision.md").exists()
    precision = json.loads((output / "precision.json").read_text())
    assert precision["result"] == ("PASS" if expected_code == 0 else "FAIL")


def test_score_preserves_existing_output_directory(tmp_path):
    paths = case(tmp_path)
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(ValueError, match="new output directory"):
        score(tmp_path, paths[0], output, paths[5], paths[4], "camera")
