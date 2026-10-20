"""Machine-readable workflow descriptors and stable handler policy digests."""

from __future__ import annotations

import fnmatch
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable

import yaml

from .contract_validator import ContractValidationError, validate_contract


class WorkflowRegistryError(ValueError):
    pass


class WorkflowRegistry:
    def __init__(self, registry_path: str | Path | None = None):
        core_root = Path(__file__).resolve().parents[1]
        self.core_root = core_root
        self.registry_path = Path(registry_path).resolve() if registry_path else core_root / "registry" / "workflows.yaml"
        try:
            payload = yaml.safe_load(self.registry_path.read_text(encoding="utf-8-sig")) or {}
        except Exception as exc:
            raise WorkflowRegistryError(f"cannot load workflow registry: {exc}") from exc
        try:
            validate_contract(payload, "workflow-registry.schema.json", label="Workflow registry")
        except ContractValidationError as exc:
            raise WorkflowRegistryError(str(exc)) from exc
        self.payload = payload

    def descriptor(self, workflow_id: str) -> Dict[str, Any]:
        raw = self.payload["workflows"].get(workflow_id)
        if not isinstance(raw, dict):
            raise WorkflowRegistryError(f"unknown workflow handler: {workflow_id}")
        descriptor = deepcopy(raw)
        path = self._handler_path(descriptor["path"])
        if not path.is_file() or path.is_symlink():
            raise WorkflowRegistryError(f"workflow handler is missing or a symlink: {path}")
        frontmatter = self._frontmatter(path)
        if frontmatter.get("name") != workflow_id:
            raise WorkflowRegistryError(
                f"workflow handler name mismatch: registry={workflow_id}, handler={frontmatter.get('name')!r}"
            )
        if str(frontmatter.get("version")) != descriptor["version"]:
            raise WorkflowRegistryError(
                f"workflow handler version mismatch for {workflow_id}: registry={descriptor['version']}, handler={frontmatter.get('version')}"
            )
        if str(frontmatter.get("contract_version")) != descriptor["contract_version"]:
            raise WorkflowRegistryError(
                f"workflow contract version mismatch for {workflow_id}"
            )
        descriptor["workflow_id"] = workflow_id
        descriptor["handler_reference"] = descriptor["path"]
        descriptor["digest"] = self._digest(workflow_id, raw, path)
        return descriptor

    def policy_lock(self) -> Dict[str, Any]:
        from datetime import datetime, timezone
        digests = {
            workflow_id: self.descriptor(workflow_id)["digest"]
            for workflow_id in sorted(self.payload["workflows"])
        }
        policy_digest = hashlib.sha256(
            json.dumps(digests, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return {
            "schema_version": "1.0",
            "registry_schema_version": self.payload["schema_version"],
            "policy_digest": policy_digest,
            "workflow_digests": digests,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def bind_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        descriptor = self.descriptor(str(task.get("handler", "")))
        if descriptor["role"] != "task-handler":
            raise WorkflowRegistryError(f"workflow is not a task handler: {descriptor['workflow_id']}")
        profile = str(task.get("verification_profile") or task.get("verification_tier") or "")
        if profile not in descriptor["verification_profiles"]:
            raise WorkflowRegistryError(
                f"Task verification profile {profile!r} is not allowed by workflow {descriptor['workflow_id']}"
            )
        self._check_roles(task.get("input_roles", []), descriptor["input_roles"], "input", descriptor["workflow_id"])
        self._check_roles(task.get("outputs", []), descriptor["output_roles"], "output", descriptor["workflow_id"])
        for value in task.get("allowed_paths", []):
            if not any(self._path_within(str(value), root) for root in descriptor["allowed_paths"]):
                raise WorkflowRegistryError(
                    f"Task allowed path {value!r} exceeds workflow {descriptor['workflow_id']} policy"
                )
        task["workflow_id"] = descriptor["workflow_id"]
        task["workflow_version"] = descriptor["version"]
        task["workflow_digest"] = descriptor["digest"]
        task["handler_reference"] = descriptor["handler_reference"]
        task["allowed_project_states"] = list(descriptor["allowed_project_states"])
        return task

    def verify_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        workflow_id = task.get("workflow_id")
        if not workflow_id:
            raise WorkflowRegistryError("Task has no v0.8 workflow governance metadata")
        candidate = deepcopy(task)
        self.bind_task(candidate)
        for field in ("workflow_id", "workflow_version", "workflow_digest", "handler_reference", "allowed_project_states"):
            if candidate.get(field) != task.get(field):
                raise WorkflowRegistryError(f"Task {field} no longer matches the current workflow policy")
        return self.descriptor(str(workflow_id))

    @staticmethod
    def state_allowed(task: Dict[str, Any], project_state: str) -> bool:
        allowed = task.get("allowed_project_states")
        return isinstance(allowed, list) and project_state in allowed

    def _handler_path(self, relative: str) -> Path:
        lexical = self.core_root / relative
        if lexical.is_symlink():
            raise WorkflowRegistryError(f"workflow handler must not be a symlink: {relative}")
        candidate = lexical.resolve()
        try:
            candidate.relative_to(self.core_root)
        except ValueError as exc:
            raise WorkflowRegistryError(f"workflow handler escapes core root: {relative}") from exc
        return candidate

    @staticmethod
    def _frontmatter(path: Path) -> Dict[str, Any]:
        text = path.read_text(encoding="utf-8-sig")
        if not text.startswith("---\n") or "\n---\n" not in text[4:]:
            raise WorkflowRegistryError(f"workflow handler has no YAML front matter: {path}")
        raw = text[4:].split("\n---\n", 1)[0]
        try:
            value = yaml.safe_load(raw) or {}
        except Exception as exc:
            raise WorkflowRegistryError(f"invalid workflow front matter in {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise WorkflowRegistryError(f"workflow front matter must be an object: {path}")
        return value

    @staticmethod
    def _digest(workflow_id: str, descriptor: Dict[str, Any], path: Path) -> str:
        normalized = json.dumps(
            {"workflow_id": workflow_id, **descriptor},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256()
        digest.update(normalized)
        digest.update(b"\0")
        handler_text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        digest.update(handler_text.encode("utf-8"))
        return digest.hexdigest()

    @staticmethod
    def _role_value(value: Any) -> str:
        return value.get("role", "") if isinstance(value, dict) else str(value)

    @classmethod
    def _check_roles(cls, values: Iterable[Any], patterns: Iterable[str], label: str, workflow_id: str) -> None:
        allowed = list(patterns)
        for value in values:
            role = cls._role_value(value)
            if not any(pattern == "*" or fnmatch.fnmatchcase(role, pattern) for pattern in allowed):
                raise WorkflowRegistryError(f"Task {label} role {role!r} is not allowed by workflow {workflow_id}")

    @staticmethod
    def _path_within(value: str, root: str) -> bool:
        normalized = value.replace("\\", "/").rstrip("/") or "."
        policy = root.replace("\\", "/").rstrip("/") or "."
        return policy == "." or normalized == policy or normalized.startswith(policy + "/")
