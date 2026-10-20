from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

CORE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CORE))

from runtime.artifact_resolver import ArtifactManifestError, ArtifactResolver, write_manifest
from runtime.lifecycle_service import LifecycleError, LifecycleService
from runtime.session_bootstrap import SessionBootstrap
from runtime.snapshot import diff_snapshots, scan_tree
from runtime.state_store import RevisionConflict, StateStore, StateStoreError
from runtime.verifier import BuildVerifier, VerificationConfigError


class LifecycleV070Tests(unittest.TestCase):
    def _plan(self, task: dict, *, wp: str = "wp-1") -> dict:
        return {
            "platform": "",
            "capabilities": [],
            "intent": {
                "schema_version": "2.0", "intent_id": "intent-1",
                "type": "implementation", "request": "implement", "scope": ["project"],
                "priority": "user_request", "expansion": "register_only",
                "mode": "production", "authority": "user",
                "created_at": "2025-01-01T00:00:00+00:00",
            },
            "work_packages": [{
                "schema_version": "2.0", "work_package_id": wp,
                "intent_id": "intent-1", "intent_request": "implement",
                "type": "IMPLEMENTATION", "target": "project", "mode": "production",
                "platform": "", "state": "READY", "priority": "user_request",
                "expansion": "register_only", "tasks": [task], "debt_ids": [],
            }],
        }

    @staticmethod
    def _task(task_id: str = "task-1", *, tier: str = "build", allowed=None, outputs=None) -> dict:
        return {
            "id": task_id, "title": "Task", "state": "READY",
            "requirements": [], "debt_ids": [],
            "allowed_paths": allowed or ["src"], "outputs": outputs or [],
            "verification_tier": tier, "verification_profile": tier,
            "handler": "firmware-development", "input_roles": [],
            "input_scope": "product", "execution_mode": "read_write",
        }

    def test_work_package_aggregation_matches_persistent_contract(self):
        package = {"tasks": [{"state": "FAILED"}, {"state": "BLOCKED_ARCHITECTURE"}]}
        counts = LifecycleService._aggregate(package)
        self.assertEqual("BLOCKED", package["state"])
        self.assertEqual(1, counts["FAILED"])
        self.assertEqual(1, counts["blocked"])
        self.assertEqual(0, counts["active"])

        package = {"tasks": [{"state": "READY"}, {"state": "BLOCKED_HARDWARE"}]}
        LifecycleService._aggregate(package)
        self.assertEqual("READY", package["state"])
        package = {"tasks": [{"state": "READY"}, {"state": "DONE"}]}
        LifecycleService._aggregate(package)
        self.assertEqual("IN_PROGRESS", package["state"])

    def test_state_store_is_confined_atomic_and_revisioned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = StateStore(root)
            value = {"name": "first"}
            path = store.write("work-packages/wp.json", value)
            self.assertEqual(root.resolve() / ".mcu-agent" / "state" / "work-packages" / "wp.json", path)
            self.assertEqual(1, value["revision"])
            with self.assertRaises(RevisionConflict):
                store.write("work-packages/wp.json", {"name": "stale"}, expected_revision=0)
            self.assertEqual("first", store.read("work-packages/wp.json")["name"])
            self.assertFalse(list(store.root.rglob("*.tmp")))
            for invalid in ("../escape.json", "/absolute.json", r"C:\\outside.json"):
                with self.subTest(invalid=invalid), self.assertRaises(StateStoreError):
                    store.write(invalid, {})

    def test_claim_restart_recovery_real_diff_and_out_of_scope_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            (project / "docs").mkdir()
            service = LifecycleService(root)
            service.persist_plan(self._plan(self._task()), project_root=project)
            claim = service.claim_task("wp-1", "task-1", session_id="session-a", project_root=project)
            # A fresh service instance recovers the embedded durable claim.
            restarted = LifecycleService(root)
            with self.assertRaises(LifecycleError):
                restarted.finalize_task("wp-1", "task-1", session_id="other", claim_revision=claim["claim_revision"])
            (project / "src" / "created.c").write_text("ok\n", encoding="utf-8")
            (project / "docs" / "escape.md").write_text("outside\n", encoding="utf-8")
            result = restarted.finalize_task(
                "wp-1", "task-1", session_id="session-a", claim_revision=claim["claim_revision"],
                model_result={"changed_files": ["src/not-real.c"], "verification": {"passed": True}},
            )
            self.assertEqual("FAILED", result["state"])
            self.assertEqual(["docs/escape.md", "src/created.c"], result["execution"]["changed_files"])
            self.assertTrue(result["execution"]["model_change_mismatch"])
            self.assertEqual(1, result["counts"]["FAILED"])

    def test_only_one_active_claim_per_project_and_abandon_is_reclaimable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            service = LifecycleService(root)
            first = self._task("a")
            second = self._task("b")
            plan = self._plan(first)
            plan["work_packages"][0]["tasks"].append(second)
            service.persist_plan(plan, project_root=project)
            claim = service.claim_task("wp-1", "a", session_id="s", project_root=project)
            with self.assertRaises(LifecycleError):
                service.claim_task("wp-1", "b", session_id="s", project_root=project)
            abandoned = service.abandon_task(
                "wp-1", "a", session_id="s", claim_revision=claim["claim_revision"], reason="interrupted"
            )
            self.assertEqual("FAILED", abandoned["state"])
            retried = service.claim_task("wp-1", "a", session_id="s2", project_root=project)
            self.assertGreater(retried["claim_revision"], claim["claim_revision"])

    def test_snapshot_excludes_runtime_caches_and_does_not_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "cache-output").mkdir()
            (root / ".git").mkdir()
            (root / "__pycache__").mkdir()
            (root / ".mcu-agent" / "state").mkdir(parents=True)
            (root / "src" / "a.c").write_text("a", encoding="utf-8")
            (root / "cache-output" / "generated.bin").write_bytes(b"cache")
            (root / ".git" / "index").write_bytes(b"git")
            (root / "__pycache__" / "x.pyc").write_bytes(b"pyc")
            (root / ".mcu-agent" / "state" / "x.json").write_text("{}", encoding="utf-8")
            config = root / "config.yaml"
            config.write_text("directories:\n  cache: cache-output\n", encoding="utf-8")
            outside = root.parent / (root.name + "-outside")
            outside.mkdir()
            try:
                (outside / "secret").write_text("secret", encoding="utf-8")
                try:
                    (root / "linked").symlink_to(outside, target_is_directory=True)
                except OSError:
                    self.skipTest("symlinks unavailable")
                snapshot = scan_tree(root, config_path=config)
                self.assertIn("src/a.c", snapshot)
                self.assertIn("linked", snapshot)
                self.assertEqual("symlink", snapshot["linked"]["kind"])
                self.assertNotIn("linked/secret", snapshot)
                self.assertNotIn("cache-output/generated.bin", snapshot)
                self.assertFalse(any(path.startswith(".git/") or path.startswith(".mcu-agent/state/") for path in snapshot))
            finally:
                for child in outside.iterdir():
                    child.unlink()
                outside.rmdir()

    def test_structured_profile_runs_without_shell_and_legacy_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            argv = [sys.executable, "-c", "print('verified')"]
            config = root / "config.yaml"
            config.write_text(
                "verification:\n  profiles:\n    build:\n"
                f"      argv_posix: {json.dumps(argv)}\n      argv_windows: {json.dumps(argv)}\n"
                "      cwd: .\n      timeout: 10\n",
                encoding="utf-8",
            )
            verdict = BuildVerifier(config).verify({"project_root": str(root), "verification_tier": "build"})
            self.assertTrue(verdict["passed"])
            self.assertFalse(verdict["audit"]["shell"])
            legacy = root / "legacy.yaml"
            legacy.write_text("toolchain:\n  build_command: 'exit 0'\n", encoding="utf-8")
            rejected = BuildVerifier(legacy).verify({"project_root": str(root), "verification_tier": "build"})
            self.assertFalse(rejected["passed"])
            self.assertFalse(rejected["audit"]["allowed"])
            target = BuildVerifier(config).verify({"project_root": str(root), "verification_tier": "target"})
            self.assertTrue(target["skipped"])

    def test_no_verifier_ignores_model_pass_and_writes_only_draft_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            service = LifecycleService(root)
            service.persist_plan(self._plan(self._task()), project_root=project)
            claim = service.claim_task("wp-1", "task-1", session_id="s", project_root=project)
            (project / "src" / "a.c").write_text("a", encoding="utf-8")
            result = service.finalize_task(
                "wp-1", "task-1", session_id="s", claim_revision=claim["claim_revision"],
                model_result={"summary": "claimed pass", "verification": {"passed": True}},
            )
            self.assertEqual("VALIDATING", result["state"])
            memory_path = next((root / ".mcu-agent" / "state" / "memories").glob("*.json"))
            memory = json.loads(memory_path.read_text(encoding="utf-8"))
            self.assertEqual("draft", memory["confidence"])
            self.assertEqual("ACTIVE", service.get_package("wp-1")["tasks"][0]["claim"]["status"])

            argv = [sys.executable, "-c", "raise SystemExit(0)"]
            config = root / "config.yaml"
            config.write_text(
                "verification:\n  profiles:\n    build:\n"
                f"      argv_posix: {json.dumps(argv)}\n      argv_windows: {json.dumps(argv)}\n",
                encoding="utf-8",
            )
            completed = service.finalize_task(
                "wp-1", "task-1", session_id="s", claim_revision=claim["claim_revision"],
                model_result={"summary": "claimed pass"}, verifier=BuildVerifier(config),
            )
            self.assertEqual("DONE", completed["state"])
            upgraded = json.loads(memory_path.read_text(encoding="utf-8"))
            self.assertEqual("verified", upgraded["confidence"])

    def test_verifier_generated_changes_are_rescanned_and_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            task = self._task()
            task["allowed_paths"] = ["src"]
            service = LifecycleService(root)
            service.persist_plan(self._plan(task), project_root=project)
            claim = service.claim_task("wp-1", "task-1", session_id="s")
            command = [
                sys.executable,
                "-c",
                "from pathlib import Path; Path('docs').mkdir(); Path('docs/verifier-write.txt').write_text('x')",
            ]
            config = root / "config.yaml"
            config.write_text(
                "verification:\n  profiles:\n    build:\n"
                f"      argv_posix: {json.dumps(command)}\n      argv_windows: {json.dumps(command)}\n",
                encoding="utf-8",
            )
            result = service.finalize_task(
                "wp-1", "task-1", session_id="s", claim_revision=claim["claim_revision"],
                model_result={"summary": "verify"}, verifier=BuildVerifier(config),
            )
            self.assertEqual("FAILED", result["state"])
            self.assertEqual(["docs/verifier-write.txt"], result["execution"]["changed_files"])
            self.assertIn(
                "changed path outside Task scope: docs/verifier-write.txt",
                result["execution"]["boundary_errors"],
            )

    def test_target_attestation_requires_hash_and_completes_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            evidence_file = root / "hil.log"
            evidence_file.write_text("device passed", encoding="utf-8")
            digest = hashlib.sha256(evidence_file.read_bytes()).hexdigest()
            argv = [sys.executable, "-c", "raise SystemExit(0)"]
            config = root / "config.yaml"
            config.write_text(
                "verification:\n  profiles:\n    build:\n"
                f"      argv_posix: {json.dumps(argv)}\n      argv_windows: {json.dumps(argv)}\n",
                encoding="utf-8",
            )
            service = LifecycleService(root)
            service.persist_plan(self._plan(self._task(tier="target")), project_root=project)
            claim = service.claim_task("wp-1", "task-1", session_id="s", project_root=project)
            build_only = service.finalize_task(
                "wp-1", "task-1", session_id="s", claim_revision=claim["claim_revision"],
                model_result={"summary": "build passed"}, verifier=BuildVerifier(config),
            )
            self.assertEqual("VALIDATING", build_only["state"])
            self.assertTrue(build_only["execution"]["verification"]["build"]["passed"])
            evidence = {
                "operator": "operator-1", "device": "board-serial-1",
                "timestamp": "2025-01-01T12:00:00+00:00", "steps": ["flash", "observe"],
                "result": "PASS", "evidence": {"path": "hil.log", "sha256": digest},
            }
            service.attest_target(
                "wp-1", "task-1", session_id="s", claim_revision=claim["claim_revision"], evidence=evidence
            )
            result = service.finalize_task(
                "wp-1", "task-1", session_id="s", claim_revision=claim["claim_revision"],
                model_result={"summary": "HIL"}, verifier=BuildVerifier(config),
            )
            self.assertEqual("DONE", result["state"])
            self.assertEqual("manual-attestation", result["execution"]["verification"]["kind"])
            self.assertTrue(result["execution"]["verification"]["build"]["passed"])

    def test_automatic_hil_requires_both_build_and_target_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            argv = [sys.executable, "-c", "raise SystemExit(0)"]
            config = root / "config.yaml"
            config.write_text(
                "verification:\n  profiles:\n"
                "    build:\n      tier: build\n"
                f"      argv_posix: {json.dumps(argv)}\n      argv_windows: {json.dumps(argv)}\n"
                "    target:\n      tier: target\n      kind: hil\n"
                f"      argv_posix: {json.dumps(argv)}\n      argv_windows: {json.dumps(argv)}\n",
                encoding="utf-8",
            )
            service = LifecycleService(root)
            service.persist_plan(self._plan(self._task(tier="target")), project_root=project)
            claim = service.claim_task("wp-1", "task-1", session_id="s")
            result = service.finalize_task(
                "wp-1", "task-1", session_id="s", claim_revision=claim["claim_revision"],
                model_result={"summary": "automated HIL"}, verifier=BuildVerifier(config),
            )
            self.assertEqual("DONE", result["state"])
            self.assertTrue(result["execution"]["verification"]["build"]["passed"])
            self.assertTrue(result["execution"]["verification"]["target"]["passed"])
            self.assertEqual("hil", result["execution"]["verification"]["kind"])

    def test_artifact_batch_failure_leaves_manifest_bytes_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / ".mcu-agent" / "artifact-manifest.yaml"
            write_manifest(manifest, {"schema_version": "2.0", "revision": 1, "artifacts": []})
            allowed = root / "project" / "out"
            allowed.mkdir(parents=True)
            (allowed / "one.bin").write_bytes(b"one")
            (allowed / "two.bin").write_bytes(b"two")
            resolver = ArtifactResolver(root)
            before = manifest.read_bytes()
            specs = [
                {"id": "a1", "role": "firmware.bin", "logical_name": "one", "path": "project/out/one.bin", "media_type": "application/octet-stream", "evidence_class": "BUILD_OUTPUT"},
                {"id": "a2", "role": "undeclared.role", "logical_name": "two", "path": "project/out/two.bin", "media_type": "application/octet-stream", "evidence_class": "BUILD_OUTPUT"},
            ]
            with self.assertRaises(ArtifactManifestError):
                resolver.register_generated_many(
                    specs, producer="task:t", allowed_roles={"firmware.bin"}, allowed_roots=[allowed]
                )
            absolute = dict(specs[0], id="absolute", path=str((allowed / "one.bin").resolve()))
            with self.assertRaises(ArtifactManifestError):
                resolver.register_generated_many(
                    [absolute], producer="task:t", allowed_roles={"firmware.bin"}, allowed_roots=[allowed]
                )
            self.assertEqual(before, manifest.read_bytes())
            registered = resolver.register_generated_many(
                specs[:1], producer="task:t", allowed_roles={"firmware.bin"}, allowed_roots=[allowed]
            )
            self.assertEqual("a1", registered[0]["id"])

    def test_replanning_preserves_claim_and_claim_root_is_frozen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project-a"
            other = root / "project-b"
            (project / "src").mkdir(parents=True)
            (other / "src").mkdir(parents=True)
            service = LifecycleService(root)
            plan = self._plan(self._task())
            service.persist_plan(plan, project_root=project)
            with self.assertRaises(LifecycleError):
                service.claim_task("wp-1", "task-1", session_id="wrong", project_root=other)
            self.assertEqual("READY", service.get_package("wp-1")["tasks"][0]["state"])

            claim = service.claim_task("wp-1", "task-1", session_id="s", project_root=project)
            resumed = service.persist_plan(plan, project_root=project)
            resumed_task = resumed["work_packages"][0]["tasks"][0]
            self.assertEqual("IMPLEMENTING", resumed_task["state"])
            self.assertEqual(claim["claim_id"], resumed_task["claim"]["claim_id"])
            self.assertEqual([], resumed["next_tasks"])

    def test_corrupt_claim_mirror_cannot_leave_task_implementing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            service = LifecycleService(root)
            service.persist_plan(self._plan(self._task()), project_root=project)
            mirror = StateStore(root).path("claims/wp-1--task-1.json")
            mirror.parent.mkdir(parents=True, exist_ok=True)
            mirror.write_text("{broken", encoding="utf-8")
            with self.assertRaises(StateStoreError):
                service.claim_task("wp-1", "task-1", session_id="s")
            package = service.get_package("wp-1")
            self.assertEqual("READY", package["tasks"][0]["state"])
            self.assertNotIn("active_claim", package)

            mirror.unlink()
            claim = service.claim_task("wp-1", "task-1", session_id="s")
            mirror.write_text("{broken", encoding="utf-8")
            with self.assertRaises(StateStoreError):
                service.finalize_task(
                    "wp-1", "task-1", session_id="s",
                    claim_revision=claim["claim_revision"], model_result={"summary": "x"},
                )
            self.assertEqual("IMPLEMENTING", service.get_package("wp-1")["tasks"][0]["state"])

    def test_manifest_rolls_back_if_work_package_commit_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            output = project / "out" / "firmware.bin"
            output.parent.mkdir(parents=True)
            manifest_path = root / ".mcu-agent" / "artifact-manifest.yaml"
            write_manifest(manifest_path, {"schema_version": "2.0", "revision": 1, "artifacts": []})
            task = self._task()
            task["allowed_paths"] = ["out"]
            task["outputs"] = ["firmware.bin"]
            service = LifecycleService(root)
            service.persist_plan(self._plan(task), project_root=project)
            claim = service.claim_task("wp-1", "task-1", session_id="s")
            output.write_bytes(b"firmware")
            argv = [sys.executable, "-c", "raise SystemExit(0)"]
            config = root / "config.yaml"
            config.write_text(
                "verification:\n  profiles:\n    build:\n"
                f"      argv_posix: {json.dumps(argv)}\n      argv_windows: {json.dumps(argv)}\n",
                encoding="utf-8",
            )
            model = {"summary": "built", "output_artifacts": [{
                "id": "fw-1", "role": "firmware.bin", "logical_name": "firmware",
                "path": "project/out/firmware.bin", "media_type": "application/octet-stream",
                "evidence_class": "BUILD_OUTPUT",
            }]}
            before = manifest_path.read_bytes()
            original_write = service.store.write

            def fail_package(relative_path, *args, **kwargs):
                if str(relative_path).startswith("work-packages/"):
                    raise OSError("injected package write failure")
                return original_write(relative_path, *args, **kwargs)

            with patch.object(service.store, "write", side_effect=fail_package):
                with self.assertRaises(OSError):
                    service.finalize_task(
                        "wp-1", "task-1", session_id="s",
                        claim_revision=claim["claim_revision"], model_result=model,
                        verifier=BuildVerifier(config), manifest=ArtifactResolver(root),
                    )
            self.assertEqual(before, manifest_path.read_bytes())
            self.assertEqual("IMPLEMENTING", service.get_package("wp-1")["tasks"][0]["state"])

    def test_target_attestation_is_revalidated_at_finalize(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            evidence_file = root / "hil.log"
            evidence_file.write_text("passed", encoding="utf-8")
            digest = hashlib.sha256(evidence_file.read_bytes()).hexdigest()
            service = LifecycleService(root)
            service.persist_plan(self._plan(self._task(tier="target")), project_root=project)
            claim = service.claim_task("wp-1", "task-1", session_id="s")
            evidence = {
                "operator": "operator", "device": "board-1",
                "timestamp": "2025-01-01T12:00:00+00:00", "steps": ["run"],
                "result": "PASS", "evidence": {"path": "hil.log", "sha256": digest},
            }
            service.attest_target(
                "wp-1", "task-1", session_id="s",
                claim_revision=claim["claim_revision"], evidence=evidence,
            )
            evidence_file.write_text("changed", encoding="utf-8")
            result = service.finalize_task(
                "wp-1", "task-1", session_id="s",
                claim_revision=claim["claim_revision"], model_result={"summary": "HIL"},
            )
            self.assertEqual("FAILED", result["state"])
            self.assertIn("hash mismatch", result["execution"]["verification"]["errors"][0])

    def test_cache_root_cannot_disable_snapshot_and_escaping_symlink_blocks_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            (project / "src/a.c").write_text("a", encoding="utf-8")
            config = root / "config.yaml"
            config.write_text("directories:\n  cache: .\n", encoding="utf-8")
            self.assertIn("src/a.c", scan_tree(project, config_path=config))

            outside = root.parent / (root.name + "-outside")
            outside.mkdir()
            try:
                try:
                    (project / "external").symlink_to(outside, target_is_directory=True)
                except OSError:
                    self.skipTest("symlinks unavailable")
                service = LifecycleService(root, config_path=config)
                service.persist_plan(self._plan(self._task()), project_root=project)
                with self.assertRaises(LifecycleError):
                    service.claim_task("wp-1", "task-1", session_id="s", project_root=project)
            finally:
                (project / "external").unlink(missing_ok=True)
                outside.rmdir()

    def test_invalid_context_config_does_not_orphan_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            config = root / "config.yaml"
            config.write_text("hardware: []\n", encoding="utf-8")
            service = LifecycleService(root, config_path=config)
            service.persist_plan(self._plan(self._task()), project_root=project)
            with self.assertRaises(ValueError):
                service.claim_task("wp-1", "task-1", session_id="s")
            package = service.get_package("wp-1")
            self.assertEqual("READY", package["tasks"][0]["state"])
            self.assertNotIn("active_claim", package)

    def test_read_only_analysis_requires_citations_and_rejects_real_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            (project / "src/a.c").write_text("int a;\n", encoding="utf-8")
            task = self._task(tier="analysis")
            task["execution_mode"] = "read_only"
            service = LifecycleService(root)
            service.persist_plan(self._plan(task), project_root=project)
            claim = service.claim_task("wp-1", "task-1", session_id="s", project_root=project)
            incomplete = service.finalize_task(
                "wp-1", "task-1", session_id="s", claim_revision=claim["claim_revision"],
                model_result={"analysis": {"conclusion": "reviewed", "sources": []}},
            )
            self.assertEqual("VALIDATING", incomplete["state"])
            complete = service.finalize_task(
                "wp-1", "task-1", session_id="s", claim_revision=claim["claim_revision"],
                model_result={"analysis": {"conclusion": "reviewed", "sources": ["src/a.c:1"]}},
            )
            self.assertEqual("DONE", complete["state"])

            second = self._plan(dict(task, id="task-2"), wp="wp-2")
            service.persist_plan(second, project_root=project)
            claim2 = service.claim_task("wp-2", "task-2", session_id="s2", project_root=project)
            (project / "src/changed.c").write_text("changed", encoding="utf-8")
            failed = service.finalize_task(
                "wp-2", "task-2", session_id="s2", claim_revision=claim2["claim_revision"],
                model_result={"analysis": {"conclusion": "x", "sources": ["src/changed.c:1"]}},
            )
            self.assertEqual("FAILED", failed["state"])
            self.assertIn("read-only Task changed project files", failed["execution"]["boundary_errors"])

    def test_migrated_v2_package_gets_additive_v07_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            package = self._plan(self._task())["work_packages"][0]
            package.pop("platform")
            for key in ("verification_profile", "execution_mode"):
                package["tasks"][0].pop(key)
            package["project_root"] = str(project)
            StateStore(root).write("work-packages/wp-1.json", package)
            migrated = LifecycleService(root).get_package("wp-1")
            self.assertEqual("", migrated["platform"])
            self.assertEqual("build", migrated["tasks"][0]["verification_profile"])
            self.assertEqual("read_write", migrated["tasks"][0]["execution_mode"])

    def test_session_auto_detection_writes_canonical_session_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".mcu-agent" / "runtime").mkdir(parents=True)
            (root / ".mcu-agent" / "runtime" / "cli.py").write_text("# runtime", encoding="utf-8")
            (root / "AGENTS.md").write_text("entry", encoding="utf-8")
            with patch.dict(os.environ, {"PI_CODING_AGENT": "1"}, clear=False):
                record = SessionBootstrap().start(root, agent="auto")
            self.assertEqual("pi", record["agent"])
            self.assertIn(".mcu-agent/state/sessions", Path(record["record_path"]).as_posix())


if __name__ == "__main__":
    unittest.main()
