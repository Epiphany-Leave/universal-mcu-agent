"""Control-plane bridge between an AI model and an implementation Task.

The executor does not generate code. The model remains responsible for design and
edits; this module constrains the context, validates the edit boundary, and owns
the task state transition after verification.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List

from .artifact_resolver import ArtifactManifestError, ArtifactResolver
from .esp_dev_skill import EspDevSkillError, EspDevSkillRouter
from .transition_engine import TransitionEngine, TransitionError


class TaskExecutionError(ValueError):
    pass


ModelRunner = Callable[[Dict[str, Any]], Dict[str, Any]]
Verifier = Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]


class TaskExecutor:
    """Prepare model work and turn verified model output into task state."""

    def __init__(self, *, transitions: TransitionEngine | None = None):
        self.transitions = transitions or TransitionEngine()

    def prepare(
        self,
        task: Dict[str, Any],
        *,
        request: str,
        mode: str,
        project_root: str | Path,
        product_root: str | Path | None = None,
        manifest: ArtifactResolver | None = None,
        skill_root: str | Path | None = None,
        platform: str | None = None,
    ) -> Dict[str, Any]:
        if task.get("state") not in {"READY", "IMPLEMENTING"}:
            raise TaskExecutionError(f"task {task.get('id')} is not executable: {task.get('state')}")
        project = Path(project_root).resolve()
        product = Path(product_root).resolve() if product_root else project
        self._ensure_confined(project, product, "project root")
        inputs = []
        for role in task.get("input_roles", []):
            if manifest is None:
                raise TaskExecutionError(f"task {task['id']} requires manifest role {role}")
            try:
                inputs.append(manifest.resolve(role, scope=task.get("input_scope", "product")))
            except ArtifactManifestError as exc:
                raise TaskExecutionError(str(exc)) from exc

        allowed_paths = task.get("allowed_paths", ["."])
        allowed_roots = [self._confined(project / relative, project) for relative in allowed_paths]
        try:
            skill_context = EspDevSkillRouter(skill_root, product_root=product).route(
                request, platform=platform
            )
        except EspDevSkillError as exc:
            raise TaskExecutionError(str(exc)) from exc
        return {
            "schema_version": "2.0",
            "task_id": task["id"],
            "title": task.get("title", ""),
            "request": request,
            "mode": mode,
            "verification_tier": task.get("verification_tier", "host"),
            "project_root": str(project),
            "allowed_roots": [str(path) for path in allowed_roots],
            "input_artifacts": inputs,
            "requirements": task.get("requirements", []),
            "esp_dev_skill": skill_context,
            "instructions": (
                "Implement only this Task. Keep unrelated findings as debt. "
                "Use esp_dev_skill references when present, but keep Task scope, "
                "evidence gates, and platform validation authoritative. Return "
                "changed_files, output_artifacts, and a concise implementation summary."
            ),
        }

    def execute(
        self,
        task: Dict[str, Any],
        *,
        context: Dict[str, Any],
        model_runner: ModelRunner,
        verifier: Verifier | None = None,
        manifest: ArtifactResolver | None = None,
    ) -> Dict[str, Any]:
        working = deepcopy(task)
        if working.get("state") == "READY":
            self.transitions.transition("task", working, "IMPLEMENTING")
        model_result = model_runner(context)
        if not isinstance(model_result, dict):
            raise TaskExecutionError("model runner must return an object")

        blocked_state = model_result.get("blocked_state")
        if blocked_state in {"BLOCKED_HARDWARE", "BLOCKED_REQUIREMENT", "BLOCKED_CONFIG"}:
            self.transitions.transition("task", working, blocked_state)
            working["execution"] = self._execution_record(model_result, verification=None)
            return working

        self._validate_changed_files(
            model_result.get("changed_files", []),
            context.get("allowed_roots", []),
            context.get("project_root"),
        )
        if manifest is not None:
            for output in model_result.get("output_artifacts", []):
                if isinstance(output, dict) and output.get("role"):
                    manifest.register_generated(output, producer=f"task:{working['id']}")
        verification = verifier(working, model_result) if verifier else model_result.get("verification")
        working["execution"] = self._execution_record(model_result, verification)
        if verification is None:
            self.transitions.transition("task", working, "VALIDATING")
            return working
        return self.complete(working, verification=verification)

    def complete(self, task: Dict[str, Any], *, verification: Dict[str, Any]) -> Dict[str, Any]:
        working = deepcopy(task)
        current = working.get("state")
        if current == "IMPLEMENTING":
            self.transitions.transition("task", working, "VALIDATING")
        if not isinstance(verification, dict) or verification.get("passed") is not True:
            if working.get("state") == "VALIDATING":
                self.transitions.transition("task", working, "FAILED")
            working.setdefault("execution", {})["verification"] = verification
            return working
        if working.get("state") != "VALIDATING":
            raise TaskExecutionError(f"task {working.get('id')} is not awaiting validation")
        self.transitions.transition("task", working, "DONE")
        working.setdefault("execution", {})["verification"] = verification
        return working

    @staticmethod
    def _execution_record(model_result: Dict[str, Any], verification: Dict[str, Any] | None) -> Dict[str, Any]:
        return {
            "model_status": model_result.get("status", "IMPLEMENTED"),
            "summary": model_result.get("summary", ""),
            "changed_files": list(model_result.get("changed_files", [])),
            "output_artifacts": list(model_result.get("output_artifacts", [])),
            "verification": verification,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    @classmethod
    def _validate_changed_files(
        cls,
        changed_files: Iterable[str],
        allowed_roots: Iterable[str],
        project_root: str | None,
    ) -> None:
        roots = [Path(root).resolve() for root in allowed_roots]
        base = Path(project_root).resolve() if project_root else Path.cwd()
        for value in changed_files:
            candidate = Path(value)
            path = (candidate if candidate.is_absolute() else base / candidate).resolve()
            if not any(cls._is_within(path, root) for root in roots):
                raise TaskExecutionError(f"model changed file outside Task scope: {value}")
            if not path.exists():
                raise TaskExecutionError(f"model reported a missing changed file: {value}")

    @staticmethod
    def _ensure_confined(path: Path, root: Path, label: str) -> Path:
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise TaskExecutionError(f"{label} escapes product root: {path}") from exc
        return path

    @staticmethod
    def _confined(path: Path, root: Path) -> Path:
        resolved = path.resolve()
        try:
            resolved.relative_to(root.resolve())
        except ValueError as exc:
            raise TaskExecutionError(f"Task scope escapes project root: {path}") from exc
        return resolved

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False
