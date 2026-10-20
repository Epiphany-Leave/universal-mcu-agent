"""High-level runtime entry point."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

import yaml

from .artifact_resolver import ArtifactManifestError, ArtifactResolver
from .capability_gate import BLOCK_PRIORITY, CapabilityGate
from .config_loader import (
    load_capabilities,
    load_capability_store,
    merge_capabilities,
    validate_capabilities,
)
from .intent_manager import IntentManager
from .project_contract import ProjectContractError, ProjectContractManager
from .work_package_factory import WorkPackageFactory


class RuntimeEngine:
    def __init__(self) -> None:
        self.intents = IntentManager()
        self.packages = WorkPackageFactory()
        self.gates = CapabilityGate()

    def plan(
        self,
        user_input: str,
        *,
        mode: str = "production",
        platform: str | None = None,
        capabilities: Iterable[Dict[str, Any]] = (),
        assumptions: Iterable[Dict[str, Any]] = (),
        config_path: str | None = None,
        capability_store: str | None = None,
        project_root: str | None = None,
        product_root: str | None = None,
    ) -> Dict[str, Any]:
        intent = self.intents.classify(user_input, mode=mode)
        manager = ProjectContractManager(product_root) if product_root else None
        contract = None
        status = None
        effective_project_root = project_root
        effective_platform = platform
        implementation_roles: list[str] = []
        contract_capabilities: list[Dict[str, Any]] = []

        if manager is not None:
            status = manager.evaluate()
            manager.persist_status(status)
            if status["state"] == "INVALID":
                raise ProjectContractError("invalid project contract/layout: " + "; ".join(status["errors"]))
            if status["state"] == "UNINITIALIZED":
                requested = intent["type"]
                intent["requested_type"] = requested
                intent["type"] = "project_setup"
                intent["scope"] = ["project"]
                intent["expansion"] = "dependency_only"
                intent["project_state"] = "UNINITIALIZED"
                effective_project_root = str(Path(product_root).resolve())
                effective_platform = None
            else:
                contract = manager.load(required=True)
                assert contract is not None
                intent["project_state"] = status["state"]
                contract_platform = contract["platform"].get("family")
                if platform and contract_platform and platform != contract_platform:
                    raise ProjectContractError(
                        f"CLI platform {platform!r} conflicts with project contract platform {contract_platform!r}"
                    )
                effective_platform = contract_platform or platform
                effective_project_root = str(manager.project_root(contract))
                implementation_roles = list(contract["gates"]["implementation_required_roles"])
                contract_capabilities.append(manager.platform_capability(contract))

        work_package = self.packages.create(
            intent,
            platform=effective_platform,
            implementation_input_roles=implementation_roles,
            enforce_platform=manager is not None and intent["type"] not in {"project_setup", "evidence", "review"},
        )

        # Facts are deterministic and ordered by authority:
        # project config < project contract < platform detection < durable store < explicit call.
        resolved = load_capabilities(config_path)
        resolved = merge_capabilities(resolved, contract_capabilities)
        platform_facts, platform_detection = self._detect_platform(effective_platform, effective_project_root)
        resolved = merge_capabilities(resolved, platform_facts)
        if capability_store:
            resolved = merge_capabilities(resolved, load_capability_store(capability_store))
        resolved = merge_capabilities(resolved, list(capabilities))
        validate_capabilities(resolved)

        if contract is not None and platform_detection and platform_detection.get("status") == "BLOCKED_CONFIG":
            platform_detection = {
                "platform": effective_platform,
                "root": effective_project_root,
                "status": "CONTRACT_DECLARED",
                "warnings": list(platform_detection.get("errors", [])),
            }

        gated = self.gates.evaluate(work_package, resolved, assumptions)
        if status is not None:
            gated["project_state"] = status["state"]
            gated["project_contract_digest"] = status["contract_digest"]
            self._apply_governance_gates(gated, status, manager)
        result = {
            "schema_version": "2.0",
            "platform": effective_platform or "",
            "project_root": effective_project_root or "",
            "project_state": status["state"] if status else None,
            "project_contract_digest": status["contract_digest"] if status else None,
            "intent": intent,
            "work_packages": [gated],
            "next_tasks": [task["id"] for task in gated["tasks"] if task["state"] == "READY"],
            "blocked_tasks": [task["id"] for task in gated["tasks"] if task["state"].startswith("BLOCKED")],
            "capabilities": resolved,
        }
        if effective_platform:
            result["platform_detection"] = platform_detection
        return result

    @classmethod
    def _apply_governance_gates(
        cls,
        package: Dict[str, Any],
        status: Dict[str, Any],
        manager: ProjectContractManager,
    ) -> None:
        resolver = None
        try:
            resolver = ArtifactResolver(manager.product_root)
        except ArtifactManifestError:
            pass
        for task in package.get("tasks", []):
            if status["state"] not in task.get("allowed_project_states", []):
                cls._block_task(
                    task,
                    "BLOCKED_CONFIG",
                    {"capability": "project.state", "facts": [status["state"]]},
                )
            for item in task.get("input_roles", []):
                role = item.get("role") if isinstance(item, dict) else item
                scope = item.get("scope") if isinstance(item, dict) else task.get("input_scope", "product")
                try:
                    if resolver is None:
                        raise ArtifactManifestError("canonical artifact manifest is unavailable")
                    artifact = resolver.resolve(str(role), scope=str(scope))
                    if not artifact.get("sha256"):
                        raise ArtifactManifestError(
                            f"required artifact has no SHA-256: {artifact.get('id')}"
                        )
                except ArtifactManifestError as exc:
                    cls._block_task(
                        task,
                        "BLOCKED_REQUIREMENT",
                        {"artifact_role": role, "scope": scope, "error": str(exc)},
                    )
        cls._aggregate(package)

    @staticmethod
    def _block_task(task: Dict[str, Any], state: str, blocker: Dict[str, Any]) -> None:
        current = str(task.get("state", "READY"))
        if not current.startswith("BLOCKED_") or BLOCK_PRIORITY.get(state, 0) > BLOCK_PRIORITY.get(current, 0):
            task["state"] = state
        blockers = task.setdefault("blockers", [])
        if blocker not in blockers:
            blockers.append(blocker)
        if task["state"] in {"BLOCKED_HARDWARE", "BLOCKED_REQUIREMENT"}:
            task["waiting"] = "WAITING_EXTERNAL"

    @staticmethod
    def _aggregate(package: Dict[str, Any]) -> None:
        tasks = package.get("tasks", [])
        counts = {"ready": 0, "blocked": 0, "implementing": 0, "validating": 0, "done": 0, "failed": 0, "skipped": 0}
        for task in tasks:
            state = str(task.get("state", ""))
            if state == "READY": counts["ready"] += 1
            elif state.startswith("BLOCKED_"): counts["blocked"] += 1
            elif state in {"IMPLEMENTING", "VALIDATING", "DONE", "FAILED", "SKIPPED"}: counts[state.lower()] += 1
        counts["active"] = counts["implementing"]
        counts["total"] = len(tasks)
        package["summary"] = counts
        states = {str(task.get("state", "")) for task in tasks}
        if "IMPLEMENTING" in states: package["state"] = "IN_PROGRESS"
        elif "VALIDATING" in states: package["state"] = "VALIDATING"
        elif "READY" in states: package["state"] = "READY"
        elif any(state.startswith("BLOCKED_") for state in states) or "FAILED" in states: package["state"] = "BLOCKED"
        elif states and states <= {"DONE", "SKIPPED"}: package["state"] = "DONE"
        else: package["state"] = "BLOCKED"

    @staticmethod
    def _detect_platform(
        platform: str | None,
        project_root: str | None,
    ) -> Tuple[list[Dict[str, Any]], Dict[str, Any] | None]:
        if not platform:
            return [], None
        if not project_root:
            return [], {"platform": None, "status": "BLOCKED_CONFIG", "errors": ["project_root is required"]}
        try:
            registry_path = Path(__file__).resolve().parents[1] / "registry" / "platforms.yaml"
            payload = yaml.safe_load(registry_path.read_text(encoding="utf-8-sig")) or {}
            record = payload.get("platforms", {}).get(platform)
            adapter_path = record.get("adapter") if isinstance(record, dict) else None
            if not isinstance(adapter_path, str) or adapter_path == "pending" or "." not in adapter_path:
                raise ValueError(f"platform adapter is not registered: {platform}")
            module_name, class_name = adapter_path.rsplit(".", 1)
            try:
                module = importlib.import_module(module_name)
            except ImportError:
                # Also support imports through the repository's ``core`` package.
                module = importlib.import_module(f"core.{module_name}")
            adapter_type = getattr(module, class_name)
            detected = adapter_type().detect(Path(project_root))
            if detected.get("platform") != platform:
                failed = dict(detected)
                failed["status"] = "BLOCKED_CONFIG"
                failed.setdefault("errors", []).append(
                    f"project_root does not satisfy the {platform} project contract"
                )
                return [], failed
            capability = {
                "schema_version": "2.0",
                "id": "platform.project",
                "state": "AVAILABLE",
                "facts": {
                    "root": {
                        "state": "CONFIRMED",
                        "value": detected["root"],
                        "evidence_ids": [f"platform:{platform}:detect"],
                    }
                },
            }
            success = dict(detected)
            success["status"] = "DETECTED"
            return [capability], success
        except Exception as exc:  # adapter/registry failures are configuration blockers
            return [], {"platform": None, "status": "BLOCKED_CONFIG", "errors": [str(exc)]}

    def regate(
        self,
        work_package: Dict[str, Any],
        capabilities: Iterable[Dict[str, Any]],
        assumptions: Iterable[Dict[str, Any]] = (),
    ) -> Dict[str, Any]:
        """Recompute an existing package after capability evidence changes."""
        return self.gates.evaluate(work_package, capabilities, assumptions)
