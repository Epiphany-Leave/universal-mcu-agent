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
from .memory import ProjectMemoryError, ProjectMemoryStore
from .skill_router import SkillRouter
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
        capabilities: Iterable[Dict[str, Any]] = (),
    ) -> Dict[str, Any]:
        if task.get("state") not in {"READY", "IMPLEMENTING"}:
            raise TaskExecutionError(f"task {task.get('id')} is not executable: {task.get('state')}")
        project = Path(project_root).resolve()
        product = Path(product_root).resolve() if product_root else project
        self._ensure_confined(project, product, "project root")
        inputs = []
        input_errors = []
        declared_roles = task.get("input_roles", []) or []
        for role_spec in declared_roles:
            # input_roles entries may be a plain role string or
            # {"role": ..., "scope": ...} for role-level scope control.
            if isinstance(role_spec, dict):
                role = role_spec.get("role")
                scope = role_spec.get("scope", task.get("input_scope", "product"))
            else:
                role = role_spec
                scope = task.get("input_scope", "product")
            if not role:
                continue
            if manifest is None:
                input_errors.append(f"task {task['id']} declares input role {role} but no manifest was provided")
                continue
            try:
                artifact = manifest.resolve(role, scope=scope)
            except ArtifactManifestError as exc:
                # Wildcard identity fallback is valid only when the exact role
                # is absent. Integrity/path/hash errors must never degrade into
                # a different manifest entry.
                if not str(exc).startswith("no CURRENT artifact for "):
                    input_errors.append(str(exc))
                    continue
                try:
                    artifact = self._resolve_device_fallback(manifest, role, scope)
                except ArtifactManifestError as fallback_error:
                    input_errors.append(str(fallback_error))
                    continue
                if artifact is None:
                    input_errors.append(f"no CURRENT artifact for {scope}/{role}")
                    continue
            artifact["content_excerpt"] = self._content_excerpt(artifact.get("resolved_path", artifact.get("path", "")))
            inputs.append(artifact)

        # Tasks that declare no input roles still get the project's canonical
        # evidence (structured requirements + peripheral connections) so driver
        # code is written against real requirements and wiring, not guesses.
        if not inputs and manifest is not None:
            for role in ("requirements.structured", "hardware.peripheral_connections", "requirements.acceptance_tests"):
                artifact = self._resolve_any_scope(manifest, role)
                if artifact is None:
                    continue
                artifact["content_excerpt"] = self._content_excerpt(artifact.get("resolved_path", artifact.get("path", "")))
                artifact["auto_attached"] = True
                inputs.append(artifact)

        allowed_paths = task.get("allowed_paths", ["."])
        allowed_roots = [self._confined(project / relative, project) for relative in allowed_paths]
        try:
            skill_context = EspDevSkillRouter(skill_root, product_root=product).route(
                request, platform=platform
            )
        except EspDevSkillError as exc:
            raise TaskExecutionError(str(exc)) from exc
        try:
            general_skills = SkillRouter(skill_root, product_root=product).route(
                request, platform=platform, limit=5
            )
        except Exception as exc:  # skill browsing must never block a task
            general_skills = {"available": False, "references": [], "matched": [], "error": str(exc)}
        project_memories = ProjectMemoryStore(product).search(
            f"{request} {task.get('title', '')}", platform=platform, limit=5
        )
        return {
            "schema_version": "2.0",
            "task_id": task["id"],
            "title": task.get("title", ""),
            "request": request,
            "mode": mode,
            "verification_tier": task.get("verification_tier", "host"),
            "verification_profile": task.get("verification_profile", task.get("verification_tier", "host")),
            "execution_mode": task.get("execution_mode", "read_write"),
            "platform": platform or "",
            "project_root": str(project),
            "product_root": str(product),
            "allowed_roots": [str(path) for path in allowed_roots],
            "input_artifacts": inputs,
            "input_artifact_errors": input_errors,
            "hardware_facts": self._hardware_facts(capabilities),
            "requirements": task.get("requirements", []),
            "workflow": ({
                "id": task.get("workflow_id"),
                "version": task.get("workflow_version"),
                "digest": task.get("workflow_digest"),
                "handler_reference": task.get("handler_reference"),
            } if task.get("workflow_digest") else None),
            "esp_dev_skill": skill_context,
            "skill_references": general_skills,
            "project_memories": project_memories,
            "instructions": (
                "Implement only this Task. Use hardware_facts for confirmed chip/board "
                "facts (pins, sensor identity, toolchain) and input_artifacts content "
                "excerpts for wiring and peripheral details; do not invent pins or "
                "registers that conflict with them. Keep unrelated findings as debt. "
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
        memory: ProjectMemoryStore | None = None,
        build_verifier: Any | None = None,
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

        reported_changes = list(model_result.get("changed_files", []))
        if context.get("execution_mode") == "read_only" and reported_changes:
            raise TaskExecutionError("read-only Task reported file changes")
        self._validate_changed_files(
            reported_changes,
            context.get("allowed_roots", []),
            context.get("project_root"),
        )
        # Model self-reported verification is audit data only. A verifier callback
        # or configured BuildVerifier must produce the lifecycle verdict.
        verification = None
        if build_verifier is not None:
            verification = build_verifier.verify(context)
        if (verification is None or verification.get("skipped")) and verifier is not None:
            verification = verifier(working, model_result)
        if (
            isinstance(verification, dict)
            and verification.get("passed") is True
            and context.get("verification_tier") == "target"
            and not self._target_verification(verification)
        ):
            verification = dict(verification)
            verification["passed"] = False
            verification["reason"] = "build/host verification cannot satisfy a target-tier Task"
        working["execution"] = self._execution_record(model_result, verification)
        working["execution"]["model_verification"] = model_result.get("verification")
        if verification is None or verification.get("skipped"):
            self.transitions.transition("task", working, "VALIDATING")
        else:
            working = self.complete(working, verification=verification)

        # Outputs are registered only after the Task is truly DONE. Validate the
        # complete batch before writing one manifest revision.
        outputs = list(model_result.get("output_artifacts", []))
        if working.get("state") == "DONE" and outputs:
            if manifest is None:
                working["state"] = "FAILED"
                working["execution"]["artifact_error"] = "output artifacts require a canonical manifest"
            else:
                try:
                    registered = manifest.register_generated_many(
                        outputs,
                        producer=f"task:{working['id']}",
                        allowed_roles=self._output_roles(working),
                        allowed_roots=context.get("allowed_roots", []),
                        expected_revision=int(manifest.manifest.get("revision", 0)),
                    )
                    working["execution"]["registered_artifacts"] = [item["id"] for item in registered]
                except ArtifactManifestError as exc:
                    working["state"] = "FAILED"
                    working["execution"]["artifact_error"] = str(exc)

        memory_store = memory
        if memory_store is None and context.get("product_root"):
            memory_store = ProjectMemoryStore(context["product_root"])
        if memory_store is not None:
            try:
                memory_record = memory_store.capture(
                    summary=model_result.get("summary", "") or working.get("title", "Task result"),
                    problem=context.get("request", ""),
                    solution=model_result.get("summary", ""),
                    source_task=working.get("id", ""),
                    platform=context.get("platform", ""),
                    source_files=model_result.get("changed_files", []),
                    source_artifacts=[item.get("id", "") for item in context.get("input_artifacts", []) if item.get("id")],
                    verification=verification,
                    confidence="verified" if working.get("state") == "DONE" else "draft",
                    tags=[working.get("handler", "firmware-development"), working.get("verification_tier", "host")],
                    task_state=working.get("state"),
                )
                working["execution"]["memory_id"] = memory_record["memory_id"]
                if memory_record.get("deduplicated"):
                    working["execution"]["memory_deduplicated"] = True
            except ProjectMemoryError as exc:
                working["execution"]["memory_error"] = str(exc)
        return working

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
    def _target_verification(verification: Dict[str, Any]) -> bool:
        return verification.get("tier") == "target" and (
            verification.get("kind") == "manual-attestation"
            or verification.get("hil") is True
            or str(verification.get("kind", "")).lower() == "hil"
            or verification.get("audit", {}).get("profile") is True
        )

    @staticmethod
    def _output_roles(task: Dict[str, Any]) -> set[str]:
        roles = set()
        for item in task.get("outputs", []) or []:
            roles.add(item.get("role") if isinstance(item, dict) else item)
        return {role for role in roles if role}

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

    @staticmethod
    def _hardware_facts(capabilities: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Flatten capability facts into a model-facing list with values."""
        facts: List[Dict[str, Any]] = []
        for capability in capabilities or ():
            if not isinstance(capability, dict) or not capability.get("id"):
                continue
            for name, fact in (capability.get("facts") or {}).items():
                if not isinstance(fact, dict):
                    continue
                facts.append({
                    "capability": capability["id"],
                    "fact": name,
                    "state": fact.get("state", "UNKNOWN"),
                    "value": fact.get("value"),
                    "evidence": list(fact.get("evidence_ids", [])),
                })
        return facts

    @staticmethod
    def _content_excerpt(path: str | Path, limit: int = 60) -> str:
        """Return a bounded content excerpt from a resolved artifact for the model.

        Text files are truncated to `limit` lines; PDFs are text-extracted up to
        ~4 KB; binaries are summarized without being read.
        """
        target = Path(path)
        if not target.is_file():
            return "(artifact file missing)"
        try:
            suffix = target.suffix.lower()
            if suffix == ".pdf":
                try:
                    import fitz  # type: ignore
                    document = fitz.open(target)
                    text = []
                    for page in document:
                        text.append(page.get_text())
                        if sum(len(chunk) for chunk in text) > 4000:
                            break
                    document.close()
                    excerpt = "".join(text)
                    return excerpt[:4000] or "(PDF: no extractable text)"
                except Exception:
                    return "(PDF text extraction unavailable)"
            data = target.read_bytes()
            if b"\x00" in data[:4096]:
                return f"(binary artifact, {len(data)} bytes)"
            text = data.decode("utf-8", errors="replace")
            lines = text.splitlines()
            if len(lines) > limit:
                return "\n".join(lines[:limit]) + f"\n... (truncated: {len(lines)} lines total)"
            return text
        except Exception as exc:
            return f"(cannot read artifact: {exc})"

    @staticmethod
    def _resolve_any_scope(manifest: ArtifactResolver, role: str) -> Dict[str, Any] | None:
        """Resolve an auto-attached role deterministically, product before project."""
        for scope in ("product", "project"):
            matches = [
                artifact
                for artifact in getattr(manifest, "manifest", {}).get("artifacts", [])
                if artifact.get("role") == role
                and artifact.get("scope", "product") == scope
                and artifact.get("status") == "CURRENT"
            ]
            if len(matches) > 1:
                raise TaskExecutionError(f"ambiguous CURRENT artifacts for {scope}/{role}")
            if matches:
                return manifest.resolve(matches[0]["role"], scope=scope)
        return None

    @staticmethod
    def _resolve_device_fallback(manifest: ArtifactResolver, role: str, scope: str) -> Dict[str, Any] | None:
        """Resolve device.*.<suffix> roles by pattern when the identity is unknown."""
        if not (role.startswith("device.") and role.count(".") >= 2):
            return None
        suffix = "." + role.split(".", 2)[2]  # e.g. ".datasheet"
        matches = []
        for artifact in getattr(manifest, "manifest", {}).get("artifacts", []):
            candidate_role = artifact.get("role", "")
            if (
                candidate_role.startswith("device.")
                and candidate_role.endswith(suffix)
                and artifact.get("scope", "product") == scope
                and artifact.get("status") == "CURRENT"
            ):
                matches.append(artifact)
        if len(matches) > 1:
            roles = sorted(str(item.get("role")) for item in matches)
            raise TaskExecutionError(
                f"ambiguous device artifact fallback for {scope}/{role}: {roles}"
            )
        if not matches:
            return None
        return manifest.resolve(matches[0]["role"], scope=scope)

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
