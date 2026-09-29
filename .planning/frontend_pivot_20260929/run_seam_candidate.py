#!/usr/bin/env python3
"""Replay frozen seam-graph commands with one experimental frontend substituted."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def digest(path):
    sha = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            sha.update(chunk)
    return sha.hexdigest()


def remap_command(command, old_frontend, new_frontend, old_stage, new_stage):
    result = []
    for token in command:
        for old, new in ((old_frontend, new_frontend), (old_stage, new_stage)):
            if token == str(old) or token.startswith(str(old) + "/"):
                token = str(new) + token[len(str(old)):]
                break
        result.append(token)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen-stage", type=Path, required=True)
    parser.add_argument("--candidate-fusion", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    frozen_stage = args.frozen_stage.resolve(strict=True)
    candidate_fusion = args.candidate_fusion.resolve(strict=True)
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError("candidate output already exists")
    manifest_path = frozen_stage/"manifest.json"
    manifest = json.loads(manifest_path.read_text())
    commands = dict(manifest["commands"])
    old_graph = commands["graph"]
    old_frontend = Path(old_graph[old_graph.index("--trajectory")+1]).parent
    new_frontend = candidate_fusion/"mast3r"
    required = (new_frontend/"trajectory_imu_metric.csv",
                new_frontend/"imu_scale_report.json",
                new_frontend/"mast3r_logs/keyframes/dataset")
    for path in required:
        if not path.exists():
            raise FileNotFoundError(path)
    if any(token.startswith(str(frozen_stage) + "/") for token in (str(candidate_fusion), str(output))):
        raise ValueError("candidate paths cannot be nested in the frozen stage")
    output.mkdir(parents=True)
    records = []
    for name in ("graph", "complementary", "quality", "smooth", "score"):
        command = remap_command(commands[name], old_frontend, new_frontend,
                                frozen_stage, output)
        # The external tracker is only read by the score stage, after the
        # complete candidate trajectory has been generated and hashed.
        if name == "score":
            trajectory = output/"trajectory_fused.csv"
            frozen_sha = digest(trajectory)
        run = subprocess.run(command, cwd=Path(__file__).resolve().parents[2],
                             text=True, capture_output=True)
        (output/f"{name}.log").write_text(run.stdout + run.stderr)
        records.append(dict(stage=name, command=command, exit_code=run.returncode))
        if run.returncode != 0 and not (name in ("quality", "score") and run.returncode == 3):
            raise RuntimeError(f"{name} failed with exit {run.returncode}; see {output/name}.log")
    if digest(output/"trajectory_fused.csv") != frozen_sha:
        raise ValueError("candidate trajectory changed during external scoring")
    precision_path = output/"official_score/precision.json"
    precision = json.loads(precision_path.read_text())
    result = dict(diagnostic_candidate=True, promoted=False,
                  frozen_manifest=str(manifest_path), frozen_manifest_sha256=digest(manifest_path),
                  candidate_fusion=str(candidate_fusion),
                  candidate_trajectory_sha256=frozen_sha,
                  fixed_stereo_reports_from_incumbent=True,
                  fixed_stereo_reports_limitation="Stereo reports were computed with the old visual trajectory; regenerate before a final same-mouth promotion decision.",
                  external_reference_used_in_optimization=False,
                  official_score=str(precision_path),
                  ate_translation_max_m=precision["ate_translation_max_m"],
                  result=precision["result"], stages=records)
    (output/"candidate_manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in
                      ("official_score", "ate_translation_max_m", "result", "candidate_trajectory_sha256")}))


if __name__ == "__main__":
    main()
