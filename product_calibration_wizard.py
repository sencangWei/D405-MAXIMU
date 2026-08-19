#!/usr/bin/env python3
"""Chinese operator CLI for fail-closed product calibration evidence."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from product_calibration.workflow import CalibrationSession, WorkflowError, load_workflow


ROOT = Path(__file__).resolve().parent
DEFAULT_WORKFLOW = ROOT / "product_calibration/workflow.yaml"
DEFAULT_BASELINE = ROOT / "product_calibration/GOLDEN_BASELINE_20260808.yaml"


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(description="D405+KT-EX9+AS5047P 产品标定向导")
    top.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    commands = top.add_subparsers(dest="command", required=True)

    create = commands.add_parser("init", help="创建一个产品标定session")
    create.add_argument("--product-id", required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--golden-baseline", type=Path, default=DEFAULT_BASELINE)

    guide = commands.add_parser("guide", help="显示某阶段的傻瓜式步骤")
    guide.add_argument("stage")

    status = commands.add_parser("status", help="显示所有阶段状态和下一步")
    status.add_argument("--session", type=Path, required=True)

    record = commands.add_parser("record", help="登记一个机器判定证据")
    record.add_argument("--session", type=Path, required=True)
    record.add_argument("--stage", required=True)
    record.add_argument("--result", required=True, choices=("PASS", "FAIL", "BLOCKED"))
    record.add_argument("--artifact", type=Path, required=True)
    record.add_argument("--note", default="")
    return top


def main() -> int:
    args = parser().parse_args()
    try:
        workflow = load_workflow(args.workflow)
        if args.command == "init":
            session = CalibrationSession.create(
                workflow, args.output, args.product_id, args.golden_baseline
            )
            print(f"已创建：{session.manifest_path}")
            print("下一步：python3 product_calibration_wizard.py guide identity")
        elif args.command == "guide":
            print(workflow.guide(args.stage))
        elif args.command == "status":
            session = CalibrationSession.open(workflow, args.session)
            print(yaml.safe_dump(session.status(), allow_unicode=True, sort_keys=False))
        elif args.command == "record":
            session = CalibrationSession.open(workflow, args.session)
            session.record_result(
                args.stage, args.result, args.artifact, note=args.note
            )
            print(yaml.safe_dump(session.status(), allow_unicode=True, sort_keys=False))
        return 0
    except WorkflowError as exc:
        print(f"标定向导拒绝继续：{exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
