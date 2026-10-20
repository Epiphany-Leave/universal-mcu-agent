"""v0.8 fail-closed project, artifact and workflow governance contracts."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

CORE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CORE))

from runtime.artifact_resolver import ArtifactManifestError, ArtifactResolver, write_manifest
from runtime.engine import RuntimeEngine
from runtime.intent_manager import IntentManager
from runtime.lifecycle_service import LifecycleError, LifecycleService
from runtime.project_contract import ProjectContractError, ProjectContractManager
from runtime.project_layout import create_project_layout
from runtime.project_migration import ProjectMigrationError, ProjectMigrationService
from runtime.repository_verifier import RepositoryVerifier
from runtime.state_store import StateStore
from runtime.task_executor import TaskExecutor
from runtime.verifier import BuildVerifier
from runtime.workflow_registry import WorkflowRegistry, WorkflowRegistryError


class GovernanceV080Tests(unittest.TestCase):
    def _symlink_or_skip(self, link: Path, target: Path, *, directory: bool = False) -> None:
        try:
            link.symlink_to(target, target_is_directory=directory)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                self.skipTest("Windows symlink privilege is unavailable")
            raise

    def _empty_product(self, root: Path) -> None:
        agent = root / ".mcu-agent"
        agent.mkdir(parents=True)
        (agent / "artifact-manifest.yaml").write_text(
            'schema_version: "2.0"\nrevision: 1\nartifacts: []\n', encoding="utf-8"
        )

    def _initialized(self, root: Path, *, platform: str = "stm32") -> ProjectContractManager:
        self._empty_product(root)
        paths = create_project_layout(root, "Tea")
        manager = ProjectContractManager(root)
        manager.create(project_name="Tea", paths=paths, platform=platform, chip="STM32G431CBU6")
        return manager

    @staticmethod
    def _write_and_register(root: Path, role: str, relative: str, *, scope: str) -> dict:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(role, encoding="utf-8")
        return ArtifactResolver(root).register_input(role=role, path=relative, scope=scope)

    def _implementation_ready(self, root: Path) -> dict:
        for role, name in (
            ("requirements.structured", "docs/requirements.json"),
            ("device.project.capabilities", "docs/device.json"),
            ("hardware.peripheral_connections", "docs/connections.json"),
        ):
            self._write_and_register(root, role, name, scope="project")
        return RuntimeEngine().plan(
            "implement feature", product_root=str(root), project_root=str(root),
            capability_store=str(StateStore(root).root / "capabilities.json"),
        )

    def test_setup_intent_precedes_negated_implementation(self):
        intent = IntentManager().classify("初始化项目，但不实现 firmware")
        self.assertEqual("project_setup", intent["type"])

    def test_uninitialized_any_request_only_returns_project_setup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._empty_product(root)
            result = RuntimeEngine().plan("implement firmware", product_root=str(root), project_root=str(root))
            package = result["work_packages"][0]
            self.assertEqual("UNINITIALIZED", result["project_state"])
            self.assertEqual("project_setup", result["intent"]["type"])
            self.assertEqual("implementation", result["intent"]["requested_type"])
            self.assertEqual("PROJECT_SETUP", package["type"])
            self.assertEqual(["project-setup"], [task["handler"] for task in package["tasks"]])
            self.assertEqual(["project_setup"], result["next_tasks"])

    def test_contract_create_is_idempotent_but_conflict_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = self._initialized(root)
            original = manager.load(required=True)
            paths = create_project_layout(root, "Tea")
            repeated = manager.create(project_name="Tea", paths=paths, platform="stm32", chip="STM32G431CBU6")
            self.assertEqual(original, repeated)
            with self.assertRaises(ProjectContractError):
                manager.create(project_name="Tea", paths=paths, platform="esp-idf", chip="ESP32")

    def test_migration_failure_restores_exact_filesystem(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._empty_product(root)
            legacy = root / "legacy/firmware"
            legacy.mkdir(parents=True)
            (legacy / "demo.ioc").write_text("ProjectManager.DeviceId=STM32G431CBU6\n", encoding="utf-8")
            (legacy / "main.c").write_text("int legacy;\n", encoding="utf-8")
            service = ProjectMigrationService(root)
            proposal = service.propose("Tea")
            proposal["selected_project_root"] = "legacy/firmware"
            before = service._tree_snapshot()
            with patch.object(ProjectContractManager, "create", side_effect=RuntimeError("injected")):
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    service.apply(proposal, approved=True)
            self.assertEqual(before, service._tree_snapshot())
            self.assertTrue((legacy / "main.c").is_file())
            self.assertFalse((root / "software").exists())
            self.assertFalse((root / "hardware").exists())

    def test_existing_project_migration_requires_explicit_selection_and_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._empty_product(root)
            legacy = root / "legacy/firmware"
            legacy.mkdir(parents=True)
            (legacy / "demo.ioc").write_text("ProjectManager.DeviceId=STM32G431CBU6\n", encoding="utf-8")
            (legacy / "main.c").write_text("int legacy;\n", encoding="utf-8")
            service = ProjectMigrationService(root)
            proposal = service.propose("Tea")
            self.assertTrue(any(item["relative_root"] == "legacy/firmware" for item in proposal["candidates"]))
            with self.assertRaises(ProjectMigrationError):
                service.apply(proposal, approved=True)
            proposal["selected_project_root"] = "legacy/firmware"
            applied = service.apply(proposal, approved=True)
            self.assertTrue(Path(applied["project_root"], "main.c").is_file())
            self.assertFalse(legacy.exists())
            self.assertIsNotNone(ProjectContractManager(root).load(required=True))

    def test_contract_rejects_symlinked_governance_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actual = root / "actual-agent"; actual.mkdir(parents=True)
            (actual / "artifact-manifest.yaml").write_text(
                'schema_version: "2.0"\nrevision: 1\nartifacts: []\n', encoding="utf-8"
            )
            self._symlink_or_skip(root / ".mcu-agent", actual, directory=True)
            with self.assertRaisesRegex(ProjectContractError, "governance path contains"):
                ProjectContractManager(root).load()
            with self.assertRaisesRegex(ArtifactManifestError, "manifest path contains"):
                ArtifactResolver(root)

    def test_contract_rejects_noncanonical_and_symlinked_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = self._initialized(root)
            contract = manager.load(required=True)
            contract["layout"]["project"] = "software//tea"
            manager.contract_path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(ProjectContractError, "canonically normalized"):
                manager.load(required=True)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._empty_product(root)
            outside = root / "real-software"; outside.mkdir()
            self._symlink_or_skip(root / "software", outside, directory=True)
            paths = {
                "product_root": root,
                "hardware_root": root / "hardware",
                "software_root": root / "software",
                "project_root": root / "software/tea",
                "main_root": root / "software/tea/main",
                "components_root": root / "software/tea/components",
            }
            for key in ("hardware_root", "project_root", "main_root", "components_root"):
                paths[key].mkdir(parents=True, exist_ok=True)
            with self.assertRaisesRegex(ProjectContractError, "symlink/junction component"):
                ProjectContractManager(root).create(project_name="Tea", paths=paths)

    def test_invalid_contract_fails_plan_without_firmware_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._empty_product(root)
            (root / ".mcu-agent/project-contract.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(ProjectContractError):
                RuntimeEngine().plan("implement firmware", product_root=str(root), project_root=str(root))

    def test_corrupt_manifest_invalidates_project_and_hash_drift_blocks_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = self._initialized(root)
            manifest = root / ".mcu-agent/artifact-manifest.yaml"
            manifest.write_text("not: a-manifest\n", encoding="utf-8")
            status = manager.evaluate()
            self.assertEqual("INVALID", status["state"])
            with self.assertRaises(ProjectContractError):
                RuntimeEngine().plan("implement", product_root=str(root), project_root=str(root))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._initialized(root)
            self._write_and_register(root, "requirements.primary", "inputs/req.md", scope="product")
            (root / "inputs/req.md").write_text("drift", encoding="utf-8")
            result = RuntimeEngine().plan(
                "需求分析", product_root=str(root), project_root=str(root)
            )
            task = result["work_packages"][0]["tasks"][0]
            self.assertEqual("BLOCKED_REQUIREMENT", task["state"])
            self.assertTrue(any("hash mismatch" in item.get("error", "") for item in task["blockers"]))

    def test_empty_manifest_blocks_evidence_at_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._initialized(root)
            result = RuntimeEngine().plan(
                "提取需求、数据手册和原理图证据", product_root=str(root), project_root=str(root)
            )
            self.assertEqual("EVIDENCE_PENDING", result["project_state"])
            self.assertFalse(result["next_tasks"])
            self.assertEqual(
                {"BLOCKED_REQUIREMENT"},
                {task["state"] for task in result["work_packages"][0]["tasks"]},
            )

    def test_artifact_register_confines_hashes_cas_and_supersedes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._empty_product(root)
            source = root / "inputs/req.md"
            source.parent.mkdir(); source.write_text("v1", encoding="utf-8")
            resolver = ArtifactResolver(root)
            first = resolver.register_input(role="requirements.primary", path="inputs/req.md")
            self.assertEqual(64, len(first["sha256"]))
            self.assertEqual(first, resolver.register_input(role="requirements.primary", path="inputs/req.md"))
            with self.assertRaises(ArtifactManifestError):
                ArtifactResolver(root).register_input(
                    role="requirements.primary", path="inputs/req.md", expected_revision=1
                )
            second_path = root / "inputs/req2.md"; second_path.write_text("v2", encoding="utf-8")
            second = ArtifactResolver(root).register_input(role="requirements.primary", path="inputs/req2.md")
            self.assertEqual([first["id"]], second["supersedes"])
            self.assertEqual("SUPERSEDED", ArtifactResolver(root).by_id(first["id"])["status"])
            second_path.unlink()
            with self.assertRaises(ArtifactManifestError):
                ArtifactResolver(root).by_id(second["id"])
            for invalid in ("../outside", "/outside", r"C:\outside"):
                with self.subTest(invalid=invalid), self.assertRaises(ArtifactManifestError):
                    ArtifactResolver(root).register_input(role="x", path=invalid)
            target = root / "inputs/real.md"; target.write_text("real", encoding="utf-8")
            link = root / "inputs/link.md"; self._symlink_or_skip(link, target)
            with self.assertRaises(ArtifactManifestError):
                ArtifactResolver(root).register_input(role="x", path="inputs/link.md")

            current = root / "inputs/current.md"; current.write_text("same", encoding="utf-8")
            registered = ArtifactResolver(root).register_input(role="current.role", path="inputs/current.md")
            replacement = root / "inputs/replacement.md"; replacement.write_text("same", encoding="utf-8")
            current.unlink(); self._symlink_or_skip(current, replacement)
            with self.assertRaisesRegex(ArtifactManifestError, "symlink/junction"):
                ArtifactResolver(root).resolve("current.role")
            with self.assertRaisesRegex(ArtifactManifestError, "symlink/junction"):
                ArtifactResolver(root).by_id(registered["id"])

    def test_device_fallback_never_hides_hash_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._empty_product(root)
            self._write_and_register(root, "device.actual.datasheet", "inputs/device.pdf", scope="product")
            (root / "inputs/device.pdf").write_text("drift", encoding="utf-8")
            project = root / "project"; project.mkdir()
            task = {
                "id": "datasheet", "title": "datasheet", "state": "READY",
                "requirements": [], "debt_ids": [], "verification_tier": "analysis",
                "verification_profile": "analysis", "outputs": [],
                "handler": "datasheet-extraction", "allowed_paths": ["."],
                "input_roles": [{"role": "device.project.datasheet", "scope": "product"}],
                "input_scope": "product", "execution_mode": "read_write",
            }
            context = TaskExecutor().prepare(
                task, request="extract", mode="production", project_root=project,
                product_root=root, manifest=ArtifactResolver(root),
            )
            self.assertFalse(context["input_artifacts"])
            self.assertTrue(any("hash mismatch" in error for error in context["input_artifact_errors"]))

    def test_platform_is_derived_and_conflicting_cli_value_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._initialized(root)
            result = self._implementation_ready(root)
            self.assertEqual("stm32", result["platform"])
            task = result["work_packages"][0]["tasks"][0]
            self.assertEqual("READY", task["state"])
            self.assertTrue(any(item["capability"] == "platform.project" for item in task["requirements"]))
            with self.assertRaises(ProjectContractError):
                RuntimeEngine().plan(
                    "implement feature", product_root=str(root), project_root=str(root), platform="esp-idf"
                )

    def test_claim_rechecks_artifact_contract_and_workflow_without_orphaning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = self._initialized(root)
            plan = self._implementation_ready(root)
            package = plan["work_packages"][0]; task = package["tasks"][0]
            service = LifecycleService(root)
            service.persist_plan(plan, project_root=manager.project_root(manager.load(required=True)))
            (root / "docs/requirements.json").write_text("drift", encoding="utf-8")
            with self.assertRaises(LifecycleError):
                service.claim_task(package["work_package_id"], task["id"], session_id="s")
            persisted = service.get_package(package["work_package_id"])
            self.assertEqual("READY", persisted["tasks"][0]["state"])
            self.assertNotIn("active_claim", persisted)

            # Restore the artifact and create another plan, then drift the tracked contract.
            ArtifactResolver(root).register_input(
                role="requirements.structured", path="docs/requirements.json", scope="project"
            )
            plan2 = RuntimeEngine().plan("implement another", product_root=str(root), project_root=str(root))
            package2 = plan2["work_packages"][0]; task2 = package2["tasks"][0]
            service.persist_plan(plan2, project_root=manager.project_root(manager.load(required=True)))
            contract = manager.load(required=True); contract["updated_at"] = "2030-01-01T00:00:00+00:00"
            manager.contract_path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(LifecycleError, "contract digest changed"):
                service.claim_task(package2["work_package_id"], task2["id"], session_id="s2")

            # Workflow digest is also revalidated for a fresh governed plan.
            manager.contract_path.write_text(json.dumps({**contract, "updated_at": plan2["intent"]["created_at"]}), encoding="utf-8")
            # Re-plan to bind the restored contract digest.
            plan3 = RuntimeEngine().plan("implement third", product_root=str(root), project_root=str(root))
            package3 = plan3["work_packages"][0]; task3 = package3["tasks"][0]
            service.persist_plan(plan3, project_root=manager.project_root(manager.load(required=True)))
            with patch.object(WorkflowRegistry, "verify_task", side_effect=WorkflowRegistryError("workflow drift")):
                with self.assertRaisesRegex(LifecycleError, "workflow drift"):
                    service.claim_task(package3["work_package_id"], task3["id"], session_id="s3")

    def test_claimed_setup_can_create_contract_and_finalize_with_bounded_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._empty_product(root)
            plan = RuntimeEngine().plan("初始化项目", product_root=str(root), project_root=str(root))
            package = plan["work_packages"][0]; task = package["tasks"][0]
            service = LifecycleService(root)
            service.persist_plan(plan, project_root=root)
            with self.assertRaisesRegex(LifecycleError, "without --workspace"):
                service.claim_task(package["work_package_id"], task["id"], session_id="bad", workspace=True)
            claim = service.claim_task(package["work_package_id"], task["id"], session_id="setup")
            paths = create_project_layout(root, "Tea")
            ProjectContractManager(root).create(project_name="Tea", paths=paths, platform="stm32")
            final = service.finalize_task(
                package["work_package_id"], task["id"],
                session_id="setup", claim_revision=claim["claim_revision"],
                model_result={
                    "summary": "initialized",
                    "analysis": {"conclusion": "canonical layout created", "sources": [".mcu-agent/project-contract.json"]},
                },
            )
            self.assertEqual("DONE", final["state"])
            self.assertFalse(final["execution"]["boundary_errors"])

    def test_stale_setup_task_cannot_be_claimed_after_contract_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._empty_product(root)
            plan = RuntimeEngine().plan("implement firmware", product_root=str(root), project_root=str(root))
            package = plan["work_packages"][0]; task = package["tasks"][0]
            service = LifecycleService(root)
            service.persist_plan(plan, project_root=root)
            paths = create_project_layout(root, "Tea")
            ProjectContractManager(root).create(project_name="Tea", paths=paths, platform="stm32")
            with self.assertRaises(LifecycleError):
                service.claim_task(package["work_package_id"], task["id"], session_id="s")

    def test_workspace_finalize_and_promotion_is_the_only_canonical_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = self._initialized(root)
            (root / ".gitignore").write_text(
                ".mcu-agent/state/\n.mcu-agent/workspaces/\n", encoding="utf-8"
            )
            manager.project_root(manager.load(required=True)).joinpath("demo.ioc").write_text(
                "ProjectManager.DeviceId=STM32G431CBU6\n", encoding="utf-8"
            )
            argv = json.dumps([sys.executable, "-c", "print('verified')"])
            (root / ".mcu-agent/config.yaml").write_text(
                "verification:\n  profiles:\n    build:\n      tier: build\n"
                f"      argv_posix: {argv}\n      argv_windows: {argv}\n      cwd: .\n      timeout: 30\n",
                encoding="utf-8",
            )
            plan = self._implementation_ready(root)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=root, check=True)
            package = plan["work_packages"][0]; task = package["tasks"][0]
            service = LifecycleService(root)
            service.persist_plan(plan, project_root=manager.project_root(manager.load(required=True)))
            claim = service.claim_task(
                package["work_package_id"], task["id"], session_id="workspace-session", workspace=True
            )
            workspace_project = Path(claim["project_root"])
            (workspace_project / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
            self.assertFalse((manager.project_root(manager.load(required=True)) / "main.c").exists())
            final = service.finalize_task(
                package["work_package_id"], task["id"],
                session_id="workspace-session", claim_revision=claim["claim_revision"],
                model_result={"summary": "implemented", "changed_files": ["main.c"]},
                verifier=BuildVerifier(config_path=root / ".mcu-agent/config.yaml"),
            )
            self.assertEqual("DONE", final["state"])
            self.assertFalse((manager.project_root(manager.load(required=True)) / "main.c").exists())
            original_workspace_bytes = (workspace_project / "main.c").read_bytes()
            (workspace_project / "main.c").write_text("int tampered;\n", encoding="utf-8")
            with self.assertRaisesRegex(LifecycleError, "changed after finalization"):
                service.promote_task(package["work_package_id"], task["id"], claim_id=claim["claim_id"])
            (workspace_project / "main.c").write_bytes(original_workspace_bytes)
            promoted = service.promote_task(
                package["work_package_id"], task["id"], claim_id=claim["claim_id"]
            )
            self.assertTrue((manager.project_root(manager.load(required=True)) / "main.c").is_file())
            self.assertTrue(Path(promoted["audit_path"]).is_file())
            self.assertFalse(Path(claim["workspace_root"]).exists())
            self.assertEqual(claim["claim_id"], promoted["trailers"]["MCU-Claim"])
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            message = "promote task\n\n" + "\n".join(
                f"{key}: {value}" for key, value in promoted["trailers"].items()
            )
            subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True,
                stdout=subprocess.PIPE, check=True,
            ).stdout.strip()
            verified = RepositoryVerifier(root).verify(
                base=claim["task"]["claim"]["base_commit"], head=head, strict=True
            )
            self.assertTrue(verified["passed"], verified["errors"])

    def test_workspace_evidence_outputs_register_only_during_promotion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = self._initialized(root)
            (root / ".gitignore").write_text(
                ".mcu-agent/state/\n.mcu-agent/workspaces/\n", encoding="utf-8"
            )
            self._write_and_register(root, "requirements.primary", "inputs/requirements.md", scope="product")
            plan = RuntimeEngine().plan("需求分析", product_root=str(root), project_root=str(root))
            package = plan["work_packages"][0]; task = package["tasks"][0]
            self.assertEqual("READY", task["state"])
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=root, check=True)
            service = LifecycleService(root)
            service.persist_plan(plan, project_root=manager.project_root(manager.load(required=True)))
            claim = service.claim_task(
                package["work_package_id"], task["id"], session_id="evidence", workspace=True
            )
            workspace_project = Path(claim["project_root"])
            output_dir = workspace_project / "docs/requirements"; output_dir.mkdir(parents=True)
            (output_dir / "structured.json").write_text("{}\n", encoding="utf-8")
            (output_dir / "acceptance.md").write_text("# acceptance\n", encoding="utf-8")
            prefix = Path(manager.load(required=True)["layout"]["project"]) / "docs/requirements"
            outputs = [
                {
                    "id": "requirements-structured-v1", "scope": "project",
                    "role": "requirements.structured", "logical_name": "Structured requirements",
                    "path": (prefix / "structured.json").as_posix(), "media_type": "application/json",
                    "identity": "requirements-structured-v1", "revision": 1,
                    "dependencies": [ArtifactResolver(root).resolve("requirements.primary")["id"]],
                    "supersedes": [], "evidence_class": "REQUIREMENTS_EXTRACTION",
                },
                {
                    "id": "requirements-acceptance-v1", "scope": "project",
                    "role": "requirements.acceptance_tests", "logical_name": "Acceptance tests",
                    "path": (prefix / "acceptance.md").as_posix(), "media_type": "text/markdown",
                    "identity": "requirements-acceptance-v1", "revision": 1,
                    "dependencies": [ArtifactResolver(root).resolve("requirements.primary")["id"]],
                    "supersedes": [], "evidence_class": "REQUIREMENTS_EXTRACTION",
                },
            ]
            final = service.finalize_task(
                package["work_package_id"], task["id"], session_id="evidence",
                claim_revision=claim["claim_revision"],
                model_result={
                    "summary": "extracted", "changed_files": ["docs/requirements/structured.json", "docs/requirements/acceptance.md"],
                    "analysis": {"conclusion": "requirements extracted", "sources": ["inputs/requirements.md"]},
                    "output_artifacts": outputs,
                },
            )
            self.assertEqual("DONE", final["state"])
            with self.assertRaises(ArtifactManifestError):
                ArtifactResolver(root).resolve("requirements.structured", scope="project")
            original_write = service.store.write
            def fail_claim_write(relative_path, *args, **kwargs):
                if str(relative_path).startswith("claims/"):
                    raise OSError("simulated claim state failure")
                return original_write(relative_path, *args, **kwargs)
            with patch.object(service.store, "write", side_effect=fail_claim_write):
                with self.assertRaises(OSError):
                    service.promote_task(package["work_package_id"], task["id"], claim_id=claim["claim_id"])
            self.assertFalse((root / prefix / "structured.json").exists())
            with self.assertRaises(ArtifactManifestError):
                ArtifactResolver(root).resolve("requirements.structured", scope="project")
            self.assertEqual("DONE", service.get_package(package["work_package_id"])["tasks"][0]["state"])
            promoted = service.promote_task(
                package["work_package_id"], task["id"], claim_id=claim["claim_id"]
            )
            self.assertTrue(ArtifactResolver(root).resolve("requirements.structured", scope="project")["sha256"])
            self.assertTrue(ArtifactResolver(root).resolve("requirements.acceptance_tests", scope="project")["sha256"])
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            message = "promote evidence\n\n" + "\n".join(
                f"{key}: {value}" for key, value in promoted["trailers"].items()
            )
            subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True,
                stdout=subprocess.PIPE, check=True,
            ).stdout.strip()
            verified = RepositoryVerifier(root).verify(
                base=claim["task"]["claim"]["base_commit"], head=head, strict=True
            )
            self.assertTrue(verified["passed"], verified["errors"])

    def test_repository_manifest_delta_and_build_critical_classification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._empty_product(root)
            self._write_and_register(root, "requirements.primary", "inputs/req.md", scope="product")
            before = (root / ".mcu-agent/artifact-manifest.yaml").read_bytes()
            output = root / "software/tea/docs/out.json"; output.parent.mkdir(parents=True)
            output.write_text("{}\n", encoding="utf-8")
            resolver = ArtifactResolver(root)
            resolver.register_generated(
                {
                    "id": "generated-v1", "scope": "project", "role": "requirements.structured",
                    "logical_name": "generated", "path": "software/tea/docs/out.json",
                    "media_type": "application/json", "evidence_class": "TEST",
                    "dependencies": [], "supersedes": [],
                },
                producer="promotion:claim-test",
            )
            after = (root / ".mcu-agent/artifact-manifest.yaml").read_bytes()
            RepositoryVerifier._validate_manifest_delta(before, after, {"generated-v1"}, "claim-test")
            tampered = after.replace(b"logical_name: req.md", b"logical_name: changed")
            with self.assertRaisesRegex(ValueError, "unrelated Artifact changed"):
                RepositoryVerifier._validate_manifest_delta(before, tampered, {"generated-v1"}, "claim-test")
            unrelated = yaml.safe_load(after.decode("utf-8"))
            old_id = yaml.safe_load(before.decode("utf-8"))["artifacts"][0]["id"]
            next(item for item in unrelated["artifacts"] if item["id"] == old_id)["status"] = "SUPERSEDED"
            next(item for item in unrelated["artifacts"] if item["id"] == "generated-v1")["supersedes"] = [old_id]
            with self.assertRaisesRegex(ValueError, "different scope/role"):
                RepositoryVerifier._validate_manifest_delta(
                    before, yaml.safe_dump(unrelated).encode("utf-8"), {"generated-v1"}, "claim-test"
                )

        contract = {
            "layout": {
                "project": "software/tea", "main": "software/tea/main",
                "components": "software/tea/components",
            }
        }
        for path in (
            "software/tea/main/unknown.generated", "software/tea/linker.ld",
            "software/tea/tool.cmake", "software/tea/Kconfig.projbuild",
            "software/tea/sdkconfig.defaults", "software/tea/partitions.csv",
            "software/tea/scripts/build.py",
        ):
            self.assertTrue(RepositoryVerifier._requires_build(path, contract), path)
        self.assertFalse(RepositoryVerifier._requires_build("software/tea/docs/requirements.json", contract))

    def test_repository_verifier_allows_exact_input_registration_but_rejects_manifest_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._initialized(root)
            (root / ".gitignore").write_text(
                ".mcu-agent/state/\n.mcu-agent/workspaces/\n", encoding="utf-8"
            )
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=root, check=True)
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True,
                stdout=subprocess.PIPE, check=True,
            ).stdout.strip()
            self._write_and_register(root, "requirements.primary", "inputs/req.md", scope="product")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "register input"], cwd=root, check=True)
            registered_head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True,
                stdout=subprocess.PIPE, check=True,
            ).stdout.strip()
            accepted = RepositoryVerifier(root).verify(base=base, head=registered_head, strict=True)
            self.assertTrue(accepted["passed"], accepted["errors"])

            resolver = ArtifactResolver(root)
            tampered = dict(resolver.manifest)
            tampered["artifacts"] = []
            tampered["revision"] = int(tampered["revision"]) + 1
            write_manifest(root / ".mcu-agent/artifact-manifest.yaml", tampered)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "tamper manifest"], cwd=root, check=True)
            tampered_head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True,
                stdout=subprocess.PIPE, check=True,
            ).stdout.strip()
            rejected = RepositoryVerifier(root).verify(
                base=registered_head, head=tampered_head, strict=True
            )
            self.assertFalse(rejected["passed"])
            self.assertTrue(any("Artifact-manifest change" in error for error in rejected["errors"]))

    def test_strict_verifier_rejects_empty_range_and_nested_product_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._initialized(root)
            (root / ".gitignore").write_text(".mcu-agent/state/\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=root, check=True)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True,
                stdout=subprocess.PIPE, check=True,
            ).stdout.strip()
            empty = RepositoryVerifier(root).verify(base=head, head=head, strict=True)
            self.assertFalse(empty["passed"])
            self.assertTrue(any("at least one commit" in error for error in empty["errors"]))

        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp) / "repository"; product = repository / "product"
            repository.mkdir(); self._initialized(product)
            (product / ".gitignore").write_text(".mcu-agent/state/\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repository, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repository, check=True)
            subprocess.run(["git", "add", "."], cwd=repository, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=repository, check=True)
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repository, text=True,
                stdout=subprocess.PIPE, check=True,
            ).stdout.strip()
            project = ProjectContractManager(product).project_root(
                ProjectContractManager(product).load(required=True)
            )
            (project / "bypass.c").write_text("int bypass;\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repository, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "nested bypass"], cwd=repository, check=True)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repository, text=True,
                stdout=subprocess.PIPE, check=True,
            ).stdout.strip()
            nested = RepositoryVerifier(product).verify(base=base, head=head, strict=True)
            self.assertFalse(nested["passed"])
            self.assertTrue(any("product root to equal Git root" in error for error in nested["errors"]))

    def test_trusted_verifier_worktree_rejects_tracked_build_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = self._initialized(root)
            (root / ".gitignore").write_text(".mcu-agent/state/\n", encoding="utf-8")
            argv = json.dumps([
                sys.executable, "-c",
                "from pathlib import Path; Path('main/.gitkeep').write_text('mutated')",
            ])
            (root / ".mcu-agent/config.yaml").write_text(
                "verification:\n  profiles:\n    build:\n      tier: build\n"
                f"      argv_posix: {argv}\n      argv_windows: {argv}\n      cwd: .\n      timeout: 30\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=root, check=True)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True,
                stdout=subprocess.PIPE, check=True,
            ).stdout.strip()
            result = RepositoryVerifier(root)._rerun_profiles_at_head(
                head, manager.load(required=True), [("build", "build")]
            )[0]
            self.assertFalse(result["passed"])
            self.assertIn("tracked_drift", result)
            self.assertEqual("", subprocess.run(
                ["git", "status", "--porcelain=v1", "--untracked-files=no"],
                cwd=root, text=True, stdout=subprocess.PIPE, check=True,
            ).stdout)

    def test_strict_repository_verifier_rejects_direct_unaudited_source_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = self._initialized(root)
            (root / ".gitignore").write_text(
                ".mcu-agent/state/\n.mcu-agent/workspaces/\n", encoding="utf-8"
            )
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=root, check=True)
            base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, text=True, stdout=subprocess.PIPE, check=True).stdout.strip()
            project = manager.project_root(manager.load(required=True))
            (project / "direct.c").write_text("int direct;\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "bypass"], cwd=root, check=True)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, text=True, stdout=subprocess.PIPE, check=True).stdout.strip()
            result = RepositoryVerifier(root).verify(base=base, head=head, strict=True)
            self.assertFalse(result["passed"])
            self.assertTrue(any("lacks trailers" in error for error in result["errors"]))

    def test_workflow_digest_is_line_ending_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lf = root / "lf.md"; crlf = root / "crlf.md"
            lf.write_bytes(b"---\nname: x\n---\nbody\n")
            crlf.write_bytes(b"---\r\nname: x\r\n---\r\nbody\r\n")
            descriptor = {"version": "1.0.0", "path": "x"}
            self.assertEqual(
                WorkflowRegistry._digest("x", descriptor, lf),
                WorkflowRegistry._digest("x", descriptor, crlf),
            )

    def test_workflow_registry_binds_digest_and_rejects_scope_expansion(self):
        registry = WorkflowRegistry()
        task = {
            "handler": "requirements-analysis", "verification_profile": "analysis",
            "input_roles": ["requirements.primary"],
            "outputs": ["requirements.structured"], "allowed_paths": ["docs/requirements"],
        }
        registry.bind_task(task)
        self.assertEqual(64, len(task["workflow_digest"]))
        registry.verify_task(task)
        expanded = dict(task, allowed_paths=["."])
        with self.assertRaises(WorkflowRegistryError):
            registry.bind_task(expanded)


if __name__ == "__main__":
    unittest.main()
