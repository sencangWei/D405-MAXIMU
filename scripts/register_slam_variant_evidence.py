#!/usr/bin/env python3
"""Register one immutable three-variant evidence bundle in a new manifest."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path


REQUIRED_VARIANTS = {"raw_vins", "auto_loop", "depth_plane"}


def register_variant_evidence(
    *,
    manifest_path: Path,
    evidence_fragment_path: Path,
    output_path: Path,
) -> dict:
    if output_path.exists():
        raise FileExistsError(output_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    fragment = json.loads(evidence_fragment_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 3:
        raise ValueError("manifest schema_version must be 3")
    if fragment.get("truth_usage") != "post_run_scoring_only":
        raise ValueError("evidence truth usage must be post-run scoring only")
    variants = fragment.get("variant_reports", {})
    if set(variants) != REQUIRED_VARIANTS:
        raise ValueError("evidence must contain all three required variants")
    release_variant = manifest.get("release_variant")
    if release_variant not in REQUIRED_VARIANTS:
        raise ValueError("manifest release_variant is invalid")

    dataset_id = fragment.get("dataset_id")
    result = deepcopy(manifest)
    selected = None
    for dataset in result.get("datasets", []):
        if dataset.get("id") != dataset_id:
            continue
        if dataset.get("role") != "hidden_test":
            raise ValueError("variant evidence may only be registered to hidden_test")
        if selected is not None:
            raise ValueError(f"duplicate dataset id: {dataset_id}")
        selected = dataset
    if selected is None:
        raise ValueError(f"dataset not found: {dataset_id}")
    if "variant_reports" in selected:
        raise ValueError(f"dataset already has variant evidence: {dataset_id}")

    release = variants[release_variant]
    selected.update(
        {
            "external_ground_truth": fragment["ground_truth"],
            "external_ground_truth_sha256": fragment["ground_truth_sha256"],
            "variant_reports": variants,
            "run_report": release["run_report"],
            "run_report_sha256": release["run_report_sha256"],
            "ground_truth_report": release["ground_truth_report"],
            "ground_truth_report_sha256": release[
                "ground_truth_report_sha256"
            ],
        }
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="将三版本盲测证据登记到新的schema 3清单（不覆盖原文件）"
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--evidence-fragment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = register_variant_evidence(
        manifest_path=args.manifest,
        evidence_fragment_path=args.evidence_fragment,
        output_path=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
