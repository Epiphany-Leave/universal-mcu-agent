"""Durable ID-based Task claim/finalize lifecycle for runtime v0.7.0."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable

from .artifact_resolver import ArtifactManifestError, ArtifactResolver
from .config_loader import (
    load_capabilities,
    load_capability_store,
    merge_capabilities,
    validate_capabilities,
)
from .contract_validator import validate_contract
from .memory import ProjectMemoryStore
from .project_contract import ProjectContractManager
from .snapshot import configured_cache_paths, changed_paths, diff_snapshots, scan_tree
from .state_store import RevisionConflict, StateStore, StateStoreError
from .task_executor import TaskExecutor
from .verifier import BuildVerifier, VerificationConfigError, validate_target_attestation
from .workflow_registry import WorkflowRegistry, WorkflowRegistryError
from .workspace import ClaimWorkspaceManager, WorkspaceError


class LifecycleError(ValueError):
    pass


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_BLOCKED = {"BLOCKED_HARDWARE", "BLOCKED_REQUIREMENT", "BLOCKED_CONFIG", "BLOCKED_ARCHITECTURE"}
_TERMINAL = {"DONE", "SKIPPED"}


class LifecycleService:
    def __init__(self, product_root: str | Path, *, config_path: str | Path | None = None):
        self.product_root = Path(product_root).resolve()
        self.config_path = Path(config_path).resolve() if config_path else None
        self.store = StateStore(self.product_root)

    def persist_plan(
        self,
        result: Dict[str, Any],
        *,
        project_root: str | Path | None = None,
        session_id: str | None = None,
    ) -> Dict[str, Any]:
        intent = deepcopy(result["intent"])
        intent.setdefault("revision", 1)
        validate_contract(intent, "intent-record.schema.json", label="Intent")
        planned_packages = deepcopy(result.get("work_packages") or [])
        if not planned_packages:
            raise LifecycleError("plan did not return a work package")
        project = self._project_root(project_root)
        capability_snapshot = deepcopy(result.get("capabilities") or [])
        packages = []
        with self.store.lock("lifecycle"):
            intent_path = f"intents/{self._id(intent['intent_id'])}.json"
            existing_intent = self.store.read(intent_path)
            if existing_intent is None:
                self.store.write(intent_path, intent, expected_revision=0, acquire_lock=False)
            else:
                validate_contract(existing_intent, "intent-record.schema.json", label="Intent")
                intent = existing_intent
            for package in planned_packages:
                package_path = f"work-packages/{self._id(package['work_package_id'])}.json"
                existing = self.store.read(package_path)
                if isinstance(existing, dict):
                    self._normalize_package(existing)
                    self._validate_package(existing)
                    existing_project = Path(existing.get("project_root", self.product_root)).resolve()
                    if existing_project != project:
                        raise LifecycleError(
                            f"work package {package['work_package_id']} already belongs to {existing_project}"
                        )
                    packages.append(existing)
                    continue
                package["project_root"] = str(project)
                package["capability_snapshot"] = capability_snapshot
                if result.get("platform") and not package.get("platform"):
                    package["platform"] = result["platform"]
                if session_id:
                    package["session_id"] = session_id
                self._aggregate(package)
                package.setdefault("revision", 1)
                self._validate_package(package)
                self.store.write(
                    package_path,
                    package,
                    expected_revision=0,
                    acquire_lock=False,
                )
                packages.append(package)
        result = deepcopy(result)
        result["intent"] = intent
        result["work_packages"] = packages
        if packages:
            result["capabilities"] = deepcopy(packages[0].get("capability_snapshot") or [])
            result["platform"] = packages[0].get("platform", result.get("platform", ""))
        persisted_tasks = [task for package in packages for task in package.get("tasks", [])]
        result["next_tasks"] = [task["id"] for task in persisted_tasks if task.get("state") == "READY"]
        result["blocked_tasks"] = [
            task["id"] for task in persisted_tasks if str(task.get("state", "")).startswith("BLOCKED")
        ]
        result["state_root"] = str(self.store.root)
        return result

    def get_package(self, work_package_id: str) -> Dict[str, Any]:
        package = self.store.read(f"work-packages/{self._id(work_package_id)}.json")
        if not isinstance(package, dict):
            raise LifecycleError(f"unknown work package: {work_package_id}")
        self._normalize_package(package)
        validate_capabilities(package.get("capability_snapshot") or [])
        self._validate_package(package)
        return package

    def resolve_claim_reference(
        self,
        work_package_id: str,
        task_id: str,
        claim_id: str,
    ) -> Dict[str, Any]:
        """Resolve a durable opaque claim ID to its canonical credentials."""
        package = self.get_package(work_package_id)
        task = self._task(package, task_id)
        claim = task.get("claim")
        if not isinstance(claim, dict) or claim.get("status") != "ACTIVE":
            raise LifecycleError(f"task {task_id} has no active claim")
        expected = claim.get("claim_id") or self._claim_id(
            work_package_id,
            task_id,
            int(claim.get("claim_revision", -1)),
        )
        if claim_id != expected:
            raise LifecycleError("claim ID mismatch")
        self._matched_claim(
            task,
            str(claim.get("session_id", "")),
            int(claim.get("claim_revision", -1)),
            int(package.get("revision", 0)),
        )
        return {
            "claim_id": expected,
            "session_id": claim["session_id"],
            "claim_revision": int(claim["claim_revision"]),
        }

    def claim_task(
        self,
        work_package_id: str,
        task_id: str,
        *,
        session_id: str,
        project_root: str | Path | None = None,
        expected_revision: int | None = None,
        workspace: bool = False,
    ) -> Dict[str, Any]:
        if not session_id:
            raise LifecycleError("claim requires session_id")
        preview = self.get_package(work_package_id)
        planned_project = self._project_root(preview.get("project_root"))
        if project_root is not None and self._project_root(project_root) != planned_project:
            raise LifecycleError(
                f"claim project root does not match planned project root: {planned_project}"
            )
        project = planned_project
        scanned_revision = int(preview.get("revision", 0))
        preview_task = self._task(preview, task_id)
        if preview_task.get("state") not in {"READY", "FAILED"}:
            raise LifecycleError(f"task {task_id} cannot be claimed from {preview_task.get('state')}")
        governance = None
        if "project_state" in preview or preview_task.get("workflow_digest"):
            manager = ProjectContractManager(self.product_root)
            status = manager.evaluate()
            manager.persist_status(status)
            if status["state"] == "INVALID":
                raise LifecycleError("project contract/layout became invalid: " + "; ".join(status["errors"]))
            planned_digest = preview.get("project_contract_digest")
            if planned_digest != status.get("contract_digest"):
                raise LifecycleError("project contract digest changed after plan")
            try:
                descriptor = WorkflowRegistry().verify_task(preview_task)
            except WorkflowRegistryError as exc:
                raise LifecycleError(str(exc)) from exc
            if status["state"] not in descriptor["allowed_project_states"]:
                raise LifecycleError(
                    f"workflow {descriptor['workflow_id']} is not allowed in project state {status['state']}"
                )
            if status.get("project_root") and Path(status["project_root"]).resolve() != project:
                raise LifecycleError("planned project root no longer matches project contract")
            governance = {"status": status, "descriptor": descriptor}
            if preview_task.get("handler") == "project-setup" and workspace:
                raise LifecycleError("PROJECT_SETUP must run in the canonical product without --workspace")
            if (
                preview_task.get("execution_mode") == "read_write"
                and preview_task.get("handler") != "project-setup"
                and not workspace
            ):
                raise LifecycleError("governed read-write Tasks require claim-task --workspace")
        workspace_info = None
        workspace_manager = ClaimWorkspaceManager(self.product_root) if workspace else None
        execution_project = project
        snapshot_root = project
        canonical_baseline = None
        def cleanup_workspace() -> None:
            if workspace_manager is not None and workspace_info is not None:
                workspace_manager.remove(workspace_info["workspace_root"])

        if workspace_manager is not None:
            provisional_claim_id = self._claim_id(work_package_id, task_id, scanned_revision + 1)
            try:
                workspace_info = workspace_manager.create(provisional_claim_id, project)
            except WorkspaceError as exc:
                raise LifecycleError(str(exc)) from exc
            execution_project = Path(workspace_info["workspace_project_root"])
            snapshot_root = Path(workspace_info["workspace_root"])
            canonical_baseline = scan_tree(self.product_root)
        context_task = deepcopy(preview_task)
        context_task["state"] = "IMPLEMENTING"
        # Build all fallible model context before committing IMPLEMENTING. This
        # prevents malformed config/manifest data from orphaning a claim.
        try:
            context = self._prepare_context(preview, context_task, execution_project)
        except BaseException:
            cleanup_workspace()
            raise
        if preview_task.get("workflow_digest") and context.get("input_artifact_errors"):
            cleanup_workspace()
            raise LifecycleError(
                "task input artifacts are unavailable: " + "; ".join(context["input_artifact_errors"])
            )
        if preview_task.get("workflow_digest"):
            unhashed = [
                artifact.get("id", artifact.get("role", "unknown"))
                for artifact in context.get("input_artifacts", [])
                if not artifact.get("sha256")
            ]
            if unhashed:
                cleanup_workspace()
                raise LifecycleError(f"required input artifacts have no SHA-256: {unhashed}")
        # Freeze exclusions at claim time; editing config.yaml later cannot alter
        # what finalize scans. Existing root-escaping symlinks make a reliable
        # project boundary impossible and are rejected before implementation.
        cache_excludes = configured_cache_paths(self.config_path, execution_project)
        baseline = scan_tree(snapshot_root, extra_excludes=cache_excludes)
        escaping = sorted(
            path
            for path, entry in baseline.items()
            if entry.get("kind") == "symlink" and entry.get("escapes_root") is True
        )
        if escaping:
            cleanup_workspace()
            raise LifecycleError(f"project contains symlinks escaping project root: {escaping}")
        snapshot_excludes = [path.relative_to(snapshot_root).as_posix() for path in sorted(cache_excludes)]
        claim_record_warning = None
        with self.store.lock("lifecycle"):
            package = self.get_package(work_package_id)
            revision = int(package.get("revision", 0))
            if revision != scanned_revision:
                cleanup_workspace()
                raise RevisionConflict(
                    f"work package changed while snapshotting: expected {scanned_revision}, found {revision}"
                )
            if expected_revision is not None and revision != expected_revision:
                cleanup_workspace()
                raise RevisionConflict(f"work package revision conflict: expected {expected_revision}, found {revision}")
            if self._project_root(package.get("project_root")) != project:
                cleanup_workspace()
                raise RevisionConflict("planned project root changed while claiming")
            task = self._task(package, task_id)
            if task.get("state") not in {"READY", "FAILED"}:
                cleanup_workspace()
                raise LifecycleError(f"task {task_id} cannot be claimed from {task.get('state')}")
            conflict = self._active_claim_for_project(project, excluding=(work_package_id, task_id))
            if conflict:
                cleanup_workspace()
                raise LifecycleError(
                    f"project already has active claim {conflict['work_package_id']}/{conflict['task_id']}"
                )
            allowed_roots = self._allowed_roots(task, execution_project)
            now = _now()
            claim_revision = revision + 1
            claim = {
                "claim_id": self._claim_id(work_package_id, task_id, claim_revision),
                "claim_revision": claim_revision,
                "package_revision": claim_revision,
                "status": "ACTIVE",
                "work_package_id": work_package_id,
                "task_id": task_id,
                "session_id": session_id,
                "project_root": str(project),
                "execution_project_root": str(execution_project),
                "snapshot_root": str(snapshot_root),
                "workspace_root": workspace_info["workspace_root"] if workspace_info else None,
                "workspace_project_root": workspace_info["workspace_project_root"] if workspace_info else None,
                "canonical_baseline": canonical_baseline,
                "allowed_roots": [str(path) for path in allowed_roots],
                "snapshot_excludes": snapshot_excludes,
                "baseline": baseline,
                "baseline_tree_digest": hashlib.sha256(
                    json.dumps(baseline, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
                "base_commit": workspace_info["base_commit"] if workspace_info else self._git_head(project),
                "project_state": governance["status"]["state"] if governance else None,
                "project_contract_digest": governance["status"]["contract_digest"] if governance else None,
                "workflow": ({
                    "id": preview_task.get("workflow_id"),
                    "version": preview_task.get("workflow_version"),
                    "digest": preview_task.get("workflow_digest"),
                    "handler_reference": preview_task.get("handler_reference"),
                } if preview_task.get("workflow_digest") else None),
                "input_artifacts": [
                    {key: artifact.get(key) for key in ("id", "scope", "role", "path", "sha256", "revision")}
                    for artifact in context.get("input_artifacts", [])
                ],
                "required_outputs": list(preview_task.get("outputs", [])),
                "claimed_at": now,
            }
            previous_claim = self.store.read(self._claim_path(work_package_id, task_id), {})
            if previous_claim:
                claim["previous_claim"] = {
                    key: value for key, value in previous_claim.items() if key not in {"baseline", "previous_claim"}
                }
            task["state"] = "IMPLEMENTING"
            task["claim"] = deepcopy(claim)
            package["active_claim"] = {key: claim[key] for key in ("claim_id", "work_package_id", "task_id", "session_id", "claim_revision", "project_root")}
            self._aggregate(package)
            self._validate_package(package)
            self.store.write(
                f"work-packages/{self._id(work_package_id)}.json",
                package,
                expected_revision=revision,
                acquire_lock=False,
            )
            try:
                self.store.write(
                    self._claim_path(work_package_id, task_id), claim,
                    expected_revision=int(previous_claim.get("revision", 0)),
                    acquire_lock=False,
                )
            except (OSError, StateStoreError, RevisionConflict) as exc:
                # The embedded WP claim is canonical; this file is an audit mirror.
                claim_record_warning = f"claim audit mirror could not be updated: {exc}"
        return {
            "work_package_id": work_package_id,
            "task_id": task_id,
            "session_id": session_id,
            "claim_id": claim["claim_id"],
            "claim_revision": claim_revision,
            "work_package_revision": package["revision"],
            "project_root": str(execution_project),
            "canonical_project_root": str(project),
            "workspace_root": workspace_info["workspace_root"] if workspace_info else None,
            "base_commit": claim.get("base_commit"),
            "allowed_roots": claim["allowed_roots"],
            "baseline_count": len(baseline),
            "task": deepcopy(task),
            "context": context,
            "counts": package["counts"],
            **({"audit_warning": claim_record_warning} if claim_record_warning else {}),
        }

    def finalize_task(
        self,
        work_package_id: str,
        task_id: str,
        *,
        session_id: str,
        claim_revision: int,
        model_result: Dict[str, Any] | None = None,
        verifier: BuildVerifier | None = None,
        manifest: ArtifactResolver | None = None,
    ) -> Dict[str, Any]:
        model = model_result or {}
        # Read the durable claim first so a restart can continue finalization.
        package = self.get_package(work_package_id)
        task = self._task(package, task_id)
        claim = self._matched_claim(
            task, session_id, claim_revision, int(package.get("revision", 0))
        )
        execution_project = Path(claim.get("execution_project_root") or claim["project_root"])
        snapshot_root = Path(claim.get("snapshot_root") or claim["project_root"])
        after = scan_tree(
            snapshot_root,
            extra_excludes=claim.get("snapshot_excludes", []),
        )
        changes = diff_snapshots(claim.get("baseline", {}), after)
        actual = changed_paths(changes)
        boundary_errors = self._boundary_errors(
            actual, changes, snapshot_root, claim.get("allowed_roots", []), after
        )
        boundary_errors.extend(self._canonical_workspace_errors(claim))
        if task.get("execution_mode") == "read_only" and actual:
            boundary_errors.append("read-only Task changed project files")

        verification: Dict[str, Any] | None = None
        target_attestation = claim.get("target_attestation")
        if not boundary_errors:
            if task.get("verification_tier") == "analysis":
                verification = self._analysis_verification(model)
            elif task.get("verification_tier") == "target":
                verification = self._target_verification(
                    task,
                    project=execution_project,
                    verifier=verifier,
                    attestation=target_attestation,
                )
            elif verifier is not None:
                context = {
                    "project_root": str(execution_project),
                    "product_root": str(self.product_root),
                    "verification_tier": task.get("verification_tier", "host"),
                    "verification_profile": task.get("verification_profile", task.get("verification_tier", "host")),
                }
                verification = verifier.verify(context)

        # Verification commands may generate or modify files. Re-scan after all
        # build/HIL work so their changes cannot bypass Task path boundaries.
        after = scan_tree(
            snapshot_root,
            extra_excludes=claim.get("snapshot_excludes", []),
        )
        changes = diff_snapshots(claim.get("baseline", {}), after)
        actual = changed_paths(changes)
        boundary_errors = self._boundary_errors(
            actual,
            changes,
            snapshot_root,
            claim.get("allowed_roots", []),
            after,
        )
        boundary_errors.extend(self._canonical_workspace_errors(claim))
        if task.get("execution_mode") == "read_only" and actual:
            boundary_errors.append("read-only Task changed project files")
        declared_outputs = set(task.get("outputs") or [])
        reported_outputs = {
            item.get("role") for item in (model.get("output_artifacts") or [])
            if isinstance(item, dict) and item.get("role")
        }
        missing_outputs = sorted(declared_outputs - reported_outputs)
        if missing_outputs:
            boundary_errors.append(f"Task did not provide required output roles: {missing_outputs}")
        if task.get("handler") == "project-setup" and task.get("workflow_digest"):
            setup_status = ProjectContractManager(self.product_root).evaluate()
            if setup_status["state"] in {"UNINITIALIZED", "INVALID"}:
                boundary_errors.append(
                    f"PROJECT_SETUP did not produce a valid project contract/layout: {setup_status['state']}"
                )

        frozen_patch = None
        frozen_patch_path = None
        frozen_snapshot_digest = None
        if claim.get("workspace_root") and not boundary_errors:
            try:
                workspaces = ClaimWorkspaceManager(self.product_root)
                frozen_patch = workspaces.patch(claim["workspace_root"])
                patch_paths = workspaces.patch_paths(claim["workspace_root"])
                if patch_paths != sorted(actual):
                    boundary_errors.append(
                        f"workspace snapshot contains changes not representable by the Git patch: "
                        f"snapshot={sorted(actual)}, patch={patch_paths}"
                    )
                else:
                    frozen_patch_path = self.store.path(f"patches/{claim['claim_id']}.patch")
                    StateStore._atomic_replace(frozen_patch_path, frozen_patch)
                    frozen_snapshot_digest = hashlib.sha256(
                        json.dumps(after, sort_keys=True, separators=(",", ":")).encode("utf-8")
                    ).hexdigest()
            except (WorkspaceError, OSError) as exc:
                boundary_errors.append(f"cannot freeze verified workspace patch: {exc}")

        claim_record_warning = None
        with self.store.lock("lifecycle"):
            package = self.get_package(work_package_id)
            package_revision = int(package.get("revision", 0))
            task = self._task(package, task_id)
            claim = self._matched_claim(task, session_id, claim_revision, package_revision)
            if claim.get("workspace_root") and frozen_patch_path is not None and not boundary_errors:
                claim["frozen_patch_path"] = str(frozen_patch_path.relative_to(self.store.root))
                claim["frozen_patch_sha256"] = hashlib.sha256(frozen_patch or b"").hexdigest()
                claim["frozen_changed_files"] = sorted(actual)
                claim["frozen_snapshot_digest"] = frozen_snapshot_digest
            # Read the audit mirror before any canonical write. Corrupt mirrors
            # must not make an already-committed WP transition look like failure.
            claim_record = self.store.read(self._claim_path(work_package_id, task_id), {})
            display_actual = self._display_changed_paths(actual, snapshot_root, execution_project)
            execution = {
                "summary": model.get("summary", ""),
                "analysis": deepcopy(model.get("analysis")),
                "actual_changes": changes,
                "workspace_changed_files": actual if claim.get("workspace_root") else [],
                "changed_files": display_actual,
                "model_changed_files": list(model.get("changed_files") or []),
                "model_change_mismatch": sorted(set(model.get("changed_files") or []) ^ set(display_actual)),
                "output_artifacts": list(model.get("output_artifacts") or []),
                "verification": verification,
                "boundary_errors": boundary_errors,
                "updated_at": _now(),
            }
            task["execution"] = execution
            if boundary_errors:
                task["state"] = "FAILED"
            else:
                task["state"] = "VALIDATING"
                if verification and not verification.get("skipped"):
                    if verification.get("passed") is True and self._verification_satisfies(task, verification):
                        task["state"] = "DONE"
                    elif verification.get("passed") is False:
                        task["state"] = "FAILED"

            def prepare_package_commit() -> None:
                if task["state"] == "VALIDATING":
                    # Keep the durable claim so a later real verifier or target
                    # attestation can complete the same session/revision.
                    claim["status"] = "ACTIVE"
                    claim["validating_since"] = claim.get("validating_since", _now())
                    claim["package_revision"] = package_revision + 1
                    if task.get("verification_tier") == "target":
                        execution["action"] = "USER_ACTION_REQUIRED"
                    task["claim"] = claim
                else:
                    claim["status"] = "FINALIZED"
                    claim["final_state"] = task["state"]
                    claim["finalized_at"] = _now()
                    task["claim_history"] = list(task.get("claim_history") or []) + [{
                        key: value for key, value in claim.items()
                        if key not in {"baseline", "canonical_baseline"}
                    }]
                    task.pop("claim", None)
                    package.pop("active_claim", None)
                self._aggregate(package)
                self._validate_package(package)

            def write_package() -> None:
                prepare_package_commit()
                self.store.write(
                    f"work-packages/{self._id(work_package_id)}.json",
                    package,
                    expected_revision=package_revision,
                    acquire_lock=False,
                )

            committed = False
            outputs = model.get("output_artifacts") or []
            if claim.get("workspace_root") and outputs:
                execution["pending_output_artifacts"] = deepcopy(outputs)
                outputs = []
            if task["state"] == "DONE" and outputs:
                if manifest is None:
                    try:
                        manifest = ArtifactResolver(self.product_root)
                    except ArtifactManifestError as exc:
                        task["state"] = "FAILED"
                        execution["artifact_error"] = str(exc)
                if manifest is not None and task["state"] == "DONE":
                    execution["registered_artifacts"] = [
                        item.get("id") for item in outputs if isinstance(item, dict)
                    ]
                    try:
                        manifest.register_generated_many_transactional(
                            outputs,
                            producer=f"task:{task_id}",
                            allowed_roles=self._output_roles(task),
                            allowed_roots=claim.get("allowed_roots", []),
                            expected_revision=int(manifest.manifest.get("revision", 0)),
                            after_manifest_commit=write_package,
                        )
                        committed = True
                    except ArtifactManifestError as exc:
                        execution.pop("registered_artifacts", None)
                        task["state"] = "FAILED"
                        execution["artifact_error"] = str(exc)
            if not committed:
                write_package()

            claim_record.update({
                key: value for key, value in claim.items()
                if key not in {"baseline", "canonical_baseline"}
            })
            try:
                self.store.write(
                    self._claim_path(work_package_id, task_id),
                    claim_record,
                    expected_revision=int(claim_record.get("revision", 0)),
                    acquire_lock=False,
                )
            except (OSError, StateStoreError, RevisionConflict) as exc:
                # WP state is canonical; a mirror failure must not reverse a verdict.
                claim_record_warning = f"claim audit mirror could not be updated: {exc}"

        memory = ProjectMemoryStore(self.product_root)
        try:
            memory_record = memory.capture(
                summary=model.get("summary", "") or task.get("title", "Task result"),
                problem=package.get("intent_request", ""),
                solution=model.get("summary", ""),
                source_task=task_id,
                work_package=work_package_id,
                source_files=actual,
                verification=verification,
                confidence="verified" if task["state"] == "DONE" else "draft",
                tags=[task.get("handler", "firmware-development"), task.get("verification_tier", "host")],
                task_state=task["state"],
            )
            execution["memory_id"] = memory_record["memory_id"]
        except Exception as exc:  # memory must not hide the lifecycle verdict
            execution["memory_error"] = str(exc)
        if claim_record_warning:
            execution["audit_warning"] = claim_record_warning
        return {
            "work_package_id": work_package_id,
            "task_id": task_id,
            "state": task["state"],
            "work_package_state": package["state"],
            "revision": package["revision"],
            "counts": package["counts"],
            "execution": execution,
        }

    def promote_task(
        self,
        work_package_id: str,
        task_id: str,
        *,
        claim_id: str,
    ) -> Dict[str, Any]:
        """Apply one finalized workspace patch and emit a tracked audit envelope."""
        package = self.get_package(work_package_id)
        task = self._task(package, task_id)
        if task.get("state") != "DONE":
            raise LifecycleError("promotion requires a DONE Task")
        execution = task.get("execution") or {}
        verification = execution.get("verification") or {}
        if verification.get("passed") is not True or execution.get("boundary_errors"):
            raise LifecycleError("promotion requires successful verification and zero boundary errors")
        claim_path = self._claim_path(work_package_id, task_id)
        claim = self.store.read(claim_path, {})
        if claim.get("claim_id") != claim_id:
            raise LifecycleError("promotion claim ID mismatch")
        if claim.get("status") != "FINALIZED" or claim.get("final_state") != "DONE":
            raise LifecycleError("promotion requires a finalized successful claim")
        workspace_root = claim.get("workspace_root")
        if not workspace_root:
            raise LifecycleError("promotion is valid only for a claim workspace")
        workflow = claim.get("workflow") or {}
        if not workflow.get("digest") or not claim.get("project_contract_digest"):
            raise LifecycleError("promotion requires governed contract and workflow digests")
        manager = ProjectContractManager(self.product_root)
        status = manager.evaluate()
        if status["state"] == "INVALID" or status.get("contract_digest") != claim.get("project_contract_digest"):
            raise LifecycleError("project contract changed before promotion")
        try:
            WorkflowRegistry().verify_task(task)
        except WorkflowRegistryError as exc:
            raise LifecycleError(str(exc)) from exc
        if task.get("verification_tier") in {"build", "target"}:
            from .engine import RuntimeEngine
            platform_facts, platform_detection = RuntimeEngine._detect_platform(
                package.get("platform"), claim.get("workspace_project_root"),
            )
            if not platform_facts:
                raise LifecycleError(
                    "platform adapter did not validate the promoted workspace: "
                    + "; ".join((platform_detection or {}).get("errors", ["platform project not detected"]))
                )
        workspaces = ClaimWorkspaceManager(self.product_root)
        if workspaces.current_head() != claim.get("base_commit"):
            raise LifecycleError("product HEAD changed after claim")
        canonical_baseline = claim.get("canonical_baseline")
        if not isinstance(canonical_baseline, dict):
            raise LifecycleError("claim has no canonical baseline for promotion")
        direct_changes = changed_paths(diff_snapshots(canonical_baseline, scan_tree(self.product_root)))
        if direct_changes:
            raise LifecycleError(f"canonical product changed outside promotion: {direct_changes}")
        frozen_relative = claim.get("frozen_patch_path")
        if not frozen_relative or not claim.get("frozen_patch_sha256") or not claim.get("frozen_snapshot_digest"):
            raise LifecycleError("finalized claim has no frozen verified patch")
        frozen_path = self.store.path(frozen_relative)
        if not frozen_path.is_file():
            raise LifecycleError("frozen verified patch is missing")
        patch = frozen_path.read_bytes()
        patch_digest = workspaces.digest(patch)
        if patch_digest != claim.get("frozen_patch_sha256"):
            raise LifecycleError("frozen verified patch hash mismatch")
        current_snapshot = scan_tree(
            claim["snapshot_root"], extra_excludes=claim.get("snapshot_excludes", []),
        )
        current_snapshot_digest = hashlib.sha256(
            json.dumps(current_snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if current_snapshot_digest != claim.get("frozen_snapshot_digest"):
            raise LifecycleError("claim workspace changed after finalization")
        current_patch = workspaces.patch(workspace_root)
        if current_patch != patch or workspaces.patch_paths(workspace_root) != claim.get("frozen_changed_files"):
            raise LifecycleError("claim workspace patch changed after finalization")
        pending_outputs = list(execution.get("pending_output_artifacts") or [])
        registered_ids = [item.get("id") for item in pending_outputs if isinstance(item, dict) and item.get("id")]
        manifest_path = self.product_root / ".mcu-agent" / "artifact-manifest.yaml"
        manifest_before_digest = (
            hashlib.sha256(manifest_path.read_bytes()).hexdigest() if manifest_path.is_file() else None
        )
        audit_id = f"audit-{hashlib.sha256((claim_id + patch_digest).encode('utf-8')).hexdigest()[:16]}"
        trailers = {
            "MCU-Claim": claim_id,
            "MCU-Policy": workflow["digest"],
            "MCU-Verification": audit_id,
        }
        audit = {
            "schema_version": "1.0",
            "audit_id": audit_id,
            "claim_id": claim_id,
            "work_package_id": work_package_id,
            "task_id": task_id,
            "base_commit": claim["base_commit"],
            "patch_sha256": patch_digest,
            "workflow_digest": workflow["digest"],
            "project_contract_digest": claim["project_contract_digest"],
            "verification": deepcopy(verification),
            "verification_tier": task.get("verification_tier", "host"),
            "verification_profile": task.get("verification_profile", task.get("verification_tier", "host")),
            "changed_files": list(execution.get("workspace_changed_files") or []),
            "allowed_paths": list(task.get("allowed_paths") or ["."]),
            "required_output_roles": list(task.get("outputs") or []),
            "output_artifact_ids": registered_ids,
            "artifact_manifest_before_sha256": manifest_before_digest,
            "artifact_manifest_after_sha256": manifest_before_digest,
            "promoted_at": _now(),
            "trailers": trailers,
        }
        audit_path = self.product_root / ".mcu-agent" / "audit" / f"{audit_id}.json"
        if audit_path.exists():
            raise LifecycleError(f"promotion audit already exists: {audit_path}")

        work_package_path = self.store.path(f"work-packages/{self._id(work_package_id)}.json")
        claim_record_path = self.store.path(claim_path)
        with self.store.lock("lifecycle"):
            work_package_bytes = work_package_path.read_bytes()
            claim_record_bytes = claim_record_path.read_bytes()
            package = self.get_package(work_package_id)
            revision = int(package.get("revision", 0))
            task = self._task(package, task_id)
            claim_record = self.store.read(claim_path, {})
            if claim_record.get("status") != "FINALIZED" or claim_record.get("claim_id") != claim_id:
                raise LifecycleError("claim changed while preparing promotion")

            def commit_control_plane() -> None:
                try:
                    audit["artifact_manifest_after_sha256"] = (
                        hashlib.sha256(manifest_path.read_bytes()).hexdigest()
                        if manifest_path.is_file() else None
                    )
                    validate_contract(audit, "promotion-audit.schema.json", label="Promotion audit")
                    StateStore._atomic_replace(
                        audit_path,
                        (json.dumps(audit, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
                    )
                    task.setdefault("execution", {})["promotion"] = {
                        "audit_id": audit_id, "patch_sha256": patch_digest,
                        "promoted_at": audit["promoted_at"], "trailers": trailers,
                    }
                    self._validate_package(package)
                    self.store.write(
                        f"work-packages/{self._id(work_package_id)}.json",
                        package, expected_revision=revision, acquire_lock=False,
                    )
                    claim_record.update({
                        "status": "PROMOTED", "audit_id": audit_id,
                        "patch_sha256": patch_digest, "promoted_at": audit["promoted_at"],
                    })
                    self.store.write(
                        claim_path, claim_record,
                        expected_revision=int(claim_record.get("revision", 0)), acquire_lock=False,
                    )
                except BaseException:
                    StateStore._atomic_replace(work_package_path, work_package_bytes)
                    StateStore._atomic_replace(claim_record_path, claim_record_bytes)
                    try: audit_path.unlink()
                    except FileNotFoundError: pass
                    raise

            try:
                workspaces.apply_patch(patch)
                if pending_outputs:
                    resolver = ArtifactResolver(self.product_root)
                    canonical_project = Path(claim["project_root"])
                    resolver.register_generated_many_transactional(
                        pending_outputs,
                        producer=f"promotion:{claim_id}",
                        allowed_roles=self._output_roles(task),
                        allowed_roots=self._allowed_roots(task, canonical_project),
                        expected_revision=int(resolver.manifest.get("revision", 0)),
                        after_manifest_commit=commit_control_plane,
                    )
                else:
                    commit_control_plane()
            except BaseException:
                workspaces.reverse_patch(patch)
                remaining = changed_paths(diff_snapshots(canonical_baseline, scan_tree(self.product_root)))
                if remaining:
                    raise LifecycleError(
                        f"promotion failed and rollback did not restore canonical product: {remaining}"
                    )
                raise
        workspaces.remove(workspace_root)
        return {
            "work_package_id": work_package_id,
            "task_id": task_id,
            "claim_id": claim_id,
            "audit_id": audit["audit_id"],
            "audit_path": str(audit_path),
            "patch_sha256": patch_digest,
            "changed_files": audit["changed_files"],
            "trailers": trailers,
        }

    def abandon_task(
        self,
        work_package_id: str,
        task_id: str,
        *,
        session_id: str,
        claim_revision: int,
        reason: str,
    ) -> Dict[str, Any]:
        if not reason.strip():
            raise LifecycleError("abandon requires a reason")
        with self.store.lock("lifecycle"):
            package = self.get_package(work_package_id)
            revision = int(package.get("revision", 0))
            task = self._task(package, task_id)
            claim = self._matched_claim(task, session_id, claim_revision, revision)
            claim_record = self.store.read(self._claim_path(work_package_id, task_id), {})
            task["state"] = "FAILED"
            task["execution"] = {"abandoned": True, "reason": reason.strip(), "updated_at": _now()}
            claim.update({"status": "ABANDONED", "reason": reason.strip(), "finalized_at": _now()})
            task["claim_history"] = list(task.get("claim_history") or []) + [{key: value for key, value in claim.items() if key != "baseline"}]
            task.pop("claim", None)
            package.pop("active_claim", None)
            self._aggregate(package)
            self._validate_package(package)
            self.store.write(f"work-packages/{self._id(work_package_id)}.json", package, expected_revision=revision, acquire_lock=False)
            claim_record.update({
                key: value for key, value in claim.items()
                if key not in {"baseline", "canonical_baseline"}
            })
            audit_warning = None
            try:
                self.store.write(self._claim_path(work_package_id, task_id), claim_record, expected_revision=int(claim_record.get("revision", 0)), acquire_lock=False)
            except (OSError, StateStoreError, RevisionConflict) as exc:
                audit_warning = f"claim audit mirror could not be updated: {exc}"
        if claim.get("workspace_root"):
            try:
                ClaimWorkspaceManager(self.product_root).remove(claim["workspace_root"])
            except WorkspaceError as exc:
                audit_warning = (audit_warning + "; " if audit_warning else "") + f"workspace cleanup failed: {exc}"
        return {
            "work_package_id": work_package_id, "task_id": task_id,
            "state": "FAILED", "work_package_state": package["state"],
            "revision": package["revision"], "counts": package["counts"],
            **({"audit_warning": audit_warning} if audit_warning else {}),
        }

    def attest_target(
        self,
        work_package_id: str,
        task_id: str,
        *,
        session_id: str,
        claim_revision: int,
        evidence: Dict[str, Any],
    ) -> Dict[str, Any]:
        validated = validate_target_attestation(evidence, product_root=self.product_root)
        with self.store.lock("lifecycle"):
            package = self.get_package(work_package_id)
            revision = int(package.get("revision", 0))
            task = self._task(package, task_id)
            if task.get("verification_tier") != "target":
                raise LifecycleError("attest-target is valid only for target-tier Tasks")
            claim = self._matched_claim(task, session_id, claim_revision, revision)
            attestation_path = f"attestations/{self._id(work_package_id)}--{self._id(task_id)}--{claim_revision}.json"
            previous_attestation = self.store.read(attestation_path, {})
            claim["target_attestation"] = validated
            claim["package_revision"] = revision + 1
            task["claim"] = claim
            self._validate_package(package)
            self.store.write(f"work-packages/{self._id(work_package_id)}.json", package, expected_revision=revision, acquire_lock=False)
            record = {"work_package_id": work_package_id, "task_id": task_id, "session_id": session_id, "claim_revision": claim_revision, "attestation": validated, "attested_at": _now()}
            try:
                self.store.write(
                    attestation_path, record,
                    expected_revision=int(previous_attestation.get("revision", 0)),
                    acquire_lock=False,
                )
            except (OSError, StateStoreError, RevisionConflict) as exc:
                record["audit_warning"] = f"attestation audit mirror could not be updated: {exc}"
        return record

    def _canonical_workspace_errors(self, claim: Dict[str, Any]) -> list[str]:
        baseline = claim.get("canonical_baseline")
        if not claim.get("workspace_root") or not isinstance(baseline, dict):
            return []
        after = scan_tree(self.product_root)
        changes = diff_snapshots(baseline, after)
        changed = changed_paths(changes)
        return [f"canonical product changed outside claim workspace: {path}" for path in changed]

    @staticmethod
    def _display_changed_paths(actual: Iterable[str], snapshot_root: Path, execution_project: Path) -> list[str]:
        result = []
        for value in actual:
            candidate = (snapshot_root / value).resolve()
            try:
                result.append(candidate.relative_to(execution_project).as_posix())
            except ValueError:
                result.append(value)
        return sorted(result)

    @staticmethod
    def _git_head(project: Path) -> str | None:
        try:
            completed = subprocess.run(
                ["git", "-C", str(project), "rev-parse", "HEAD"],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=10, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        value = completed.stdout.strip()
        return value if completed.returncode == 0 and re.fullmatch(r"[0-9a-fA-F]{40,64}", value) else None

    def _prepare_context(
        self,
        package: Dict[str, Any],
        task: Dict[str, Any],
        project: Path,
    ) -> Dict[str, Any]:
        manifest = None
        manifest_path = self.product_root / ".mcu-agent" / "artifact-manifest.yaml"
        if manifest_path.is_file():
            manifest = ArtifactResolver(self.product_root)
        capabilities = deepcopy(package.get("capability_snapshot") or [])
        if not capabilities:
            capabilities = merge_capabilities(
                load_capabilities(self.config_path),
                load_capability_store(self.store.root / "capabilities.json"),
            )
        return TaskExecutor().prepare(
            task,
            request=package.get("intent_request", package.get("target", "")),
            mode=package.get("mode", "production"),
            project_root=project,
            product_root=self.product_root,
            manifest=manifest,
            platform=package.get("platform"),
            capabilities=capabilities,
        )

    def _target_verification(
        self,
        task: Dict[str, Any],
        *,
        project: Path,
        verifier: BuildVerifier | None,
        attestation: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        base_context = {
            "project_root": str(project),
            "product_root": str(self.product_root),
        }
        build = (
            verifier.verify({
                **base_context,
                "verification_tier": "build",
                "verification_profile": "build",
            })
            if verifier is not None
            else {
                "passed": None, "skipped": True, "tier": "build",
                "reason": "target verification requires a build profile",
            }
        )
        if attestation:
            try:
                target = validate_target_attestation(attestation, product_root=self.product_root)
                target["revalidated_at"] = _now()
            except VerificationConfigError as exc:
                target = {
                    "passed": False, "skipped": False, "tier": "target",
                    "kind": "manual-attestation", "errors": [str(exc)],
                }
        elif verifier is not None:
            target = verifier.verify({
                **base_context,
                "verification_tier": "target",
                "verification_profile": task.get("verification_profile", "target"),
            })
            if target.get("passed") is True:
                target["kind"] = "hil"
                target["hil"] = True
        else:
            target = {
                "passed": None, "skipped": True, "tier": "target",
                "reason": "target evidence or HIL profile is required",
            }

        if build.get("passed") is False or target.get("passed") is False:
            passed = False
        elif build.get("passed") is True and target.get("passed") is True:
            passed = True
        else:
            passed = None
        return {
            "passed": passed,
            "skipped": passed is None,
            "tier": "target",
            "kind": target.get("kind", "hil" if target.get("hil") else "target"),
            "hil": target.get("hil") is True,
            "build": build,
            "target": target,
            "errors": list(build.get("errors") or []) + list(target.get("errors") or []),
        }

    @staticmethod
    def _analysis_verification(model: Dict[str, Any]) -> Dict[str, Any]:
        analysis = model.get("analysis")
        if not isinstance(analysis, dict):
            return {
                "passed": None,
                "skipped": True,
                "tier": "analysis",
                "reason": "analysis Task requires an analysis object",
            }
        conclusion = analysis.get("conclusion")
        sources = analysis.get("sources")
        if not isinstance(conclusion, str) or not conclusion.strip():
            return {
                "passed": None,
                "skipped": True,
                "tier": "analysis",
                "reason": "analysis.conclusion must be a non-empty string",
            }
        if not isinstance(sources, list) or not sources or not all(
            isinstance(item, (str, dict)) and bool(item) for item in sources
        ):
            return {
                "passed": None,
                "skipped": True,
                "tier": "analysis",
                "reason": "analysis.sources must contain at least one citation",
            }
        return {
            "passed": True,
            "skipped": False,
            "tier": "analysis",
            "kind": "structured-analysis",
            "conclusion": conclusion.strip(),
            "sources": deepcopy(sources),
        }

    def _active_claim_for_project(self, project: Path, *, excluding: tuple[str, str]) -> Dict[str, Any] | None:
        directory = self.store.root / "work-packages"
        if not directory.is_dir():
            return None
        for path in directory.glob("*.json"):
            package = self.store.read(f"work-packages/{path.name}", {})
            active = package.get("active_claim") if isinstance(package, dict) else None
            if not isinstance(active, dict) or active.get("project_root") != str(project):
                continue
            if (active.get("work_package_id"), active.get("task_id")) != excluding:
                return active
        return None

    @staticmethod
    def _aggregate(package: Dict[str, Any]) -> Dict[str, int]:
        tasks = package.get("tasks") or []
        counts: Dict[str, int] = {
            state: 0 for state in (
                "READY", "BLOCKED_HARDWARE", "BLOCKED_REQUIREMENT", "BLOCKED_CONFIG",
                "BLOCKED_ARCHITECTURE", "IMPLEMENTING", "VALIDATING", "DONE", "FAILED", "SKIPPED",
            )
        }
        for task in tasks:
            state = str(task.get("state", "UNKNOWN"))
            counts[state] = counts.get(state, 0) + 1
        # Do not emit aliases that differ only by case (READY/ready, DONE/done,
        # ...). Windows PowerShell 5.1 ConvertFrom-Json rejects such objects.
        counts.update({
            "active": counts["IMPLEMENTING"],
            "blocked": sum(counts[state] for state in _BLOCKED),
            "total": len(tasks),
        })
        states = {task.get("state") for task in tasks}
        if tasks and states <= _TERMINAL:
            package["state"] = "DONE"
        elif "IMPLEMENTING" in states:
            package["state"] = "IN_PROGRESS"
        elif "VALIDATING" in states:
            package["state"] = "VALIDATING"
        elif "READY" in states:
            progressed = bool(states & (_TERMINAL | {"FAILED"}))
            package["state"] = "IN_PROGRESS" if progressed else "READY"
        elif states and states <= (_BLOCKED | _TERMINAL | {"FAILED"}):
            package["state"] = "BLOCKED"
        elif states & _BLOCKED:
            package["state"] = "BLOCKED"
        package["counts"] = counts
        return counts

    def _project_root(self, project_root: str | Path | None) -> Path:
        project = Path(project_root).resolve() if project_root else self.product_root
        try:
            project.relative_to(self.product_root)
        except ValueError as exc:
            raise LifecycleError("project root escapes product root") from exc
        if not project.is_dir():
            raise LifecycleError(f"project root is not a directory: {project}")
        return project

    @staticmethod
    def _normalize_package(package: Dict[str, Any]) -> None:
        """Fill additive v0.7 fields on migrated v2 packages before validation."""
        package.setdefault("platform", "")
        package.setdefault("capability_snapshot", [])
        package.setdefault("intent_request", str(package.get("target") or "migrated work package"))
        package.setdefault("debt_ids", [])
        for task in package.get("tasks") or []:
            if not isinstance(task, dict):
                continue
            tier = task.setdefault("verification_tier", "host")
            task.setdefault("verification_profile", tier)
            task.setdefault("execution_mode", "read_write")
            task.setdefault("requirements", [])
            task.setdefault("debt_ids", [])
            task.setdefault("outputs", [])
            task.setdefault("handler", "firmware-development")
            task.setdefault("allowed_paths", ["."])
            task.setdefault("input_roles", [])
            task.setdefault("input_scope", "product")

    @staticmethod
    def _validate_package(package: Dict[str, Any]) -> None:
        validate_contract(package, "work-package.schema.json", label="Work Package")

    @staticmethod
    def _task(package: Dict[str, Any], task_id: str) -> Dict[str, Any]:
        for task in package.get("tasks") or []:
            if task.get("id") == task_id:
                return task
        raise LifecycleError(f"unknown task {task_id} in work package {package.get('work_package_id')}")

    @staticmethod
    def _matched_claim(
        task: Dict[str, Any],
        session_id: str,
        claim_revision: int,
        package_revision: int | None = None,
    ) -> Dict[str, Any]:
        claim = task.get("claim")
        if not isinstance(claim, dict) or claim.get("status") != "ACTIVE":
            raise LifecycleError(f"task {task.get('id')} has no active claim")
        if claim.get("session_id") != session_id or int(claim.get("claim_revision", -1)) != int(claim_revision):
            raise LifecycleError("claim session/revision mismatch")
        if package_revision is not None and int(claim.get("package_revision", -1)) != int(package_revision):
            raise RevisionConflict(
                f"work package changed after claim: expected {claim.get('package_revision')}, found {package_revision}"
            )
        return claim

    @staticmethod
    def _claim_id(work_package_id: str, task_id: str, claim_revision: int) -> str:
        return f"{work_package_id}--{task_id}--{claim_revision}"

    @staticmethod
    def _allowed_roots(task: Dict[str, Any], project: Path) -> list[Path]:
        roots = []
        for value in task.get("allowed_paths") or ["."]:
            path = (project / str(value)).resolve()
            try:
                path.relative_to(project)
            except ValueError as exc:
                raise LifecycleError(f"Task allowed path escapes project root: {value}") from exc
            roots.append(path)
        return roots

    @staticmethod
    def _boundary_errors(actual: Iterable[str], changes: Dict[str, Any], project: Path, roots: Iterable[str], after: Dict[str, Any]) -> list[str]:
        allowed = [Path(root).resolve() for root in roots]
        errors = []
        for relative in actual:
            lexical = project / relative
            resolved = lexical.resolve()
            if not any(_within(resolved, root) for root in allowed):
                errors.append(f"changed path outside Task scope: {relative}")
            entry = after.get(relative)
            if isinstance(entry, dict) and entry.get("kind") == "symlink":
                errors.append(f"changed symlink is not accepted as a Task output: {relative}")
        return sorted(set(errors))

    @staticmethod
    def _verification_satisfies(task: Dict[str, Any], verification: Dict[str, Any]) -> bool:
        tier = task.get("verification_tier", "host")
        if tier == "target":
            return verification.get("tier") == "target" and (
                verification.get("kind") == "manual-attestation"
                or verification.get("hil") is True
                or str(verification.get("kind", "")).lower() == "hil"
                or (verification.get("audit", {}).get("profile") is True and verification.get("passed") is True)
            )
        return verification.get("passed") is True

    @staticmethod
    def _output_roles(task: Dict[str, Any]) -> set[str]:
        result = set()
        for item in task.get("outputs") or []:
            result.add(item.get("role") if isinstance(item, dict) else item)
        return {item for item in result if item}

    @staticmethod
    def _id(value: str) -> str:
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise LifecycleError(f"invalid lifecycle id: {value!r}")
        return value

    def _claim_path(self, work_package_id: str, task_id: str) -> str:
        return f"claims/{self._id(work_package_id)}--{self._id(task_id)}.json"


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
