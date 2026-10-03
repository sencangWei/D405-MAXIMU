"""Compare the actual frontend adapter with retained frozen source solves.

This is integration evidence, not a complete frontend replay or ATE score.
"""
import argparse
import json
from pathlib import Path
import sys
import time

import torch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
from experimental_mast3r_metric_joint_adapter import Runtime, context_for_source, sha  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-trial", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError(args.output)
    report_path = args.source_trial / "report.json"
    report = json.loads(report_path.read_text())
    job = report["job"]
    graph_path, poses_path = Path(job["graph"]), args.source_trial / "poses.pt"
    if sha(graph_path) != job["input_sha256"]["graph"] or sha(poses_path) != report["poses_sha256"]:
        raise ValueError("frozen graph/retained pose source changed")
    graph = torch.load(graph_path, map_location="cpu", weights_only=True)
    poses = torch.load(poses_path, map_location="cpu", weights_only=True)
    expected = poses["poses"]["filtered"]
    args.output.mkdir(parents=True)
    context_path = args.output / "context.json"
    context = context_for_source(Path(job["dataset"]), Path(job["paired_left_dataset"]), job["eye"])
    context_path.write_text(json.dumps(context, indent=2) + "\n")
    runtime = Runtime(context_path, args.output / "solves.jsonl")
    started = time.monotonic()
    actual = runtime.solve(graph["frame_ids"], graph["args"])
    runtime.log.close()
    exact = torch.equal(actual, expected)
    result = {
        "schema": "metric_frontend_adapter_equivalence_v1",
        "id": job["id"], "eye": job["eye"], "exact": exact,
        "max_abs": float((actual - expected).abs().max()),
        "pin_exact": torch.equal(actual[0], graph["args"][0][0]),
        "elapsed_s": time.monotonic() - started,
        "source_report_sha256": sha(report_path), "source_poses_sha256": sha(poses_path),
        "code_sha256": context["code_sha256"], "runner_sha256": sha(__file__),
        "external_ground_truth_used": False, "precision_pass": False,
    }
    (args.output / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ("id", "exact", "max_abs", "pin_exact", "elapsed_s", "precision_pass")}), flush=True)
    if not exact or not result["pin_exact"]:
        raise ValueError("frontend adapter differs from retained source objective")


if __name__ == "__main__":
    main()
