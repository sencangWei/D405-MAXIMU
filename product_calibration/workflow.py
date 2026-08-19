"""Small fail-closed workflow engine for product calibration evidence."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import yaml


RESULTS = {"PASS", "FAIL", "BLOCKED"}


class WorkflowError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class Stage:
    name: str
    title: str
    prerequisites: tuple[str, ...]
    evidence: str
    invalidated_by: tuple[str, ...]
    operator_steps: tuple[str, ...]


class Workflow:
    def __init__(self, path: Path, document: Dict[str, Any]) -> None:
        self.path = Path(path).resolve()
        raw_stages = document.get("stages") or {}
        if not raw_stages:
            raise WorkflowError("workflow没有stage")
        self.stages: Dict[str, Stage] = {}
        for name, raw in raw_stages.items():
            self.stages[name] = Stage(
                name=name,
                title=str(raw["title"]),
                prerequisites=tuple(raw.get("prerequisites", [])),
                evidence=str(raw["evidence"]),
                invalidated_by=tuple(raw.get("invalidated_by", [])),
                operator_steps=tuple(raw.get("operator_steps", [])),
            )
        self.topological_order()

    def topological_order(self) -> List[str]:
        visiting: set[str] = set()
        visited: set[str] = set()
        order: List[str] = []

        def visit(name: str) -> None:
            if name not in self.stages:
                raise WorkflowError(f"未知前置stage: {name}")
            if name in visiting:
                raise WorkflowError(f"workflow存在循环依赖: {name}")
            if name in visited:
                return
            visiting.add(name)
            for dependency in self.stages[name].prerequisites:
                visit(dependency)
            visiting.remove(name)
            visited.add(name)
            order.append(name)

        for stage_name in self.stages:
            visit(stage_name)
        return order

    def guide(self, stage_name: str) -> str:
        stage = self._stage(stage_name)
        lines = [f"{stage.title}（{stage.name}）", ""]
        if stage.prerequisites:
            lines.append("前置阶段：" + "、".join(stage.prerequisites))
        lines.extend(
            [f"{index}. {step}" for index, step in enumerate(stage.operator_steps, 1)]
        )
        lines.extend([
            "",
            f"证据文件：{stage.evidence}",
            "标定向导只保存候选和证据，不会自动写入生产配置。",
        ])
        return "\n".join(lines)

    def _stage(self, name: str) -> Stage:
        try:
            return self.stages[name]
        except KeyError as exc:
            raise WorkflowError(f"未知stage: {name}") from exc


def load_workflow(path: Path) -> Workflow:
    path = Path(path)
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Workflow(path, document)


class CalibrationSession:
    MANIFEST = "session.yaml"

    def __init__(self, workflow: Workflow, root: Path) -> None:
        self.workflow = workflow
        self.root = Path(root).resolve()
        self.manifest_path = self.root / self.MANIFEST
        if not self.manifest_path.is_file():
            raise WorkflowError(f"session不存在: {self.manifest_path}")

    @classmethod
    def create(
        cls,
        workflow: Workflow,
        root: Path,
        product_id: str,
        golden_baseline: Path,
    ) -> "CalibrationSession":
        root = Path(root).resolve()
        baseline = Path(golden_baseline).resolve()
        if not product_id.strip():
            raise WorkflowError("product_id不能为空")
        if not baseline.is_file():
            raise WorkflowError(f"黄金基线不存在: {baseline}")
        root.mkdir(parents=True, exist_ok=False)
        frozen_inputs = root / "_frozen_inputs"
        frozen_inputs.mkdir()
        frozen_workflow = frozen_inputs / "workflow.yaml"
        frozen_baseline = frozen_inputs / "golden_baseline.yaml"
        shutil.copy2(workflow.path, frozen_workflow)
        shutil.copy2(baseline, frozen_baseline)
        document = {
            "format_version": 1,
            "product_id": product_id.strip(),
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "workflow": {
                "path": "_frozen_inputs/workflow.yaml",
                "sha256": sha256_file(frozen_workflow),
                "source_path": str(workflow.path),
            },
            "golden_baseline": {
                "path": "_frozen_inputs/golden_baseline.yaml",
                "sha256": sha256_file(frozen_baseline),
                "source_path": str(baseline),
            },
            "results": {},
        }
        (root / cls.MANIFEST).write_text(
            yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return cls(workflow, root)

    @classmethod
    def open(cls, workflow: Workflow, root: Path) -> "CalibrationSession":
        return cls(workflow, root)

    def _read(self) -> Dict[str, Any]:
        return yaml.safe_load(self.manifest_path.read_text(encoding="utf-8")) or {}

    def _write(self, document: Dict[str, Any]) -> None:
        self.manifest_path.write_text(
            yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    def _bound_input_status(self, document: Dict[str, Any]) -> Dict[str, Any]:
        checks: Dict[str, Any] = {}
        for name in ("workflow", "golden_baseline"):
            item = document.get(name, {})
            path = self.root / item.get("path", "")
            expected = item.get("sha256", "")
            if not path.is_file():
                checks[name] = {"state": "FAIL", "reason": "MISSING"}
            elif sha256_file(path) != expected:
                checks[name] = {"state": "FAIL", "reason": "HASH_MISMATCH"}
            else:
                checks[name] = {"state": "PASS", "sha256": expected}
        current_workflow_hash = sha256_file(self.workflow.path)
        checks["active_workflow"] = {
            "state": (
                "PASS"
                if current_workflow_hash == document.get("workflow", {}).get("sha256")
                else "FAIL"
            ),
            "sha256": current_workflow_hash,
            "reason": (
                None
                if current_workflow_hash == document.get("workflow", {}).get("sha256")
                else "ACTIVE_WORKFLOW_DIFFERS_FROM_SESSION"
            ),
        }
        return checks

    @staticmethod
    def _artifact_result(path: Path) -> str:
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (UnicodeDecodeError, yaml.YAMLError) as exc:
            raise WorkflowError(f"证据不是可读YAML/JSON: {path}") from exc
        result = str(document.get("result", document.get("status", ""))).upper()
        if result not in RESULTS:
            raise WorkflowError(f"证据缺少合法result/status: {path}")
        return result

    def record_result(
        self,
        stage_name: str,
        result: str,
        artifact: Path,
        note: str = "",
    ) -> None:
        self.workflow._stage(stage_name)
        result = result.upper()
        if result not in RESULTS:
            raise WorkflowError(f"非法结果: {result}")
        document = self._read()
        integrity = self._bound_input_status(document)
        if any(item["state"] != "PASS" for item in integrity.values()):
            raise WorkflowError("session绑定的workflow或黄金基线校验失败")
        artifact = Path(artifact).resolve()
        if not artifact.is_file():
            raise WorkflowError(f"证据文件不存在: {artifact}")
        embedded = self._artifact_result(artifact)
        if embedded != result:
            raise WorkflowError(f"请求结果{result}与证据结果{embedded}不一致")

        current = self.status()["stages"]
        if result == "PASS":
            failed_dependencies = [
                dependency
                for dependency in self.workflow.stages[stage_name].prerequisites
                if current[dependency]["state"] != "PASS"
            ]
            if failed_dependencies:
                raise WorkflowError("前置阶段未PASS: " + "、".join(failed_dependencies))

        stage = self.workflow.stages[stage_name]
        stored_artifact = (self.root / stage.evidence).resolve()
        try:
            stored_artifact.relative_to(self.root)
        except ValueError as exc:
            raise WorkflowError(f"证据路径越出session: {stage.evidence}") from exc
        stored_artifact.parent.mkdir(parents=True, exist_ok=True)
        if artifact != stored_artifact:
            shutil.copy2(artifact, stored_artifact)

        document.setdefault("results", {})[stage_name] = {
            "result": result,
            "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "artifact": stage.evidence,
            "artifact_sha256": sha256_file(stored_artifact),
            "note": note,
        }
        self._write(document)

    def status(self) -> Dict[str, Any]:
        document = self._read()
        integrity = self._bound_input_status(document)
        recorded = document.get("results", {})
        stages: Dict[str, Dict[str, Any]] = {}
        for name in self.workflow.topological_order():
            stage = self.workflow.stages[name]
            item = recorded.get(name)
            if item:
                artifact = self.root / item.get("artifact", "")
                expected_hash = item.get("artifact_sha256", "")
                if not artifact.is_file():
                    stages[name] = {
                        "state": "FAIL", "reason": "ARTIFACT_MISSING", **item
                    }
                elif sha256_file(artifact) != expected_hash:
                    stages[name] = {
                        "state": "FAIL", "reason": "ARTIFACT_HASH_MISMATCH", **item
                    }
                else:
                    stages[name] = {"state": item["result"], **item}
                continue
            dependencies_passed = all(
                stages[dependency]["state"] == "PASS"
                for dependency in stage.prerequisites
            )
            stages[name] = {
                "state": "READY" if dependencies_passed else "BLOCKED",
                "reason": "NOT_RUN" if dependencies_passed else "PREREQUISITES_NOT_PASS",
            }

        final_state = stages.get("final_acceptance", {}).get("state")
        input_integrity_passed = all(
            item["state"] == "PASS" for item in integrity.values()
        )
        if not input_integrity_passed:
            overall = "FAIL"
        elif final_state == "PASS":
            overall = "PASS"
        elif any(item["state"] == "FAIL" for item in stages.values()):
            overall = "FAIL"
        else:
            overall = "BLOCKED"
        return {
            "product_id": document.get("product_id"),
            "overall": overall,
            "bound_input_integrity": integrity,
            "stages": stages,
        }
