"""Trusted CI verifier that independently recomputes repository governance facts."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path

import yaml
from typing import Any, Dict, Iterable

from .artifact_resolver import ArtifactManifestError, ArtifactResolver
from .contract_validator import ContractValidationError, validate_contract
from .project_contract import ProjectContractError, ProjectContractManager
from .workflow_registry import WorkflowRegistry, WorkflowRegistryError


class RepositoryVerificationError(ValueError):
    pass


class RepositoryVerifier:
    def __init__(self, product_root: str | Path):
        self.root = Path(product_root).resolve()

    def verify(self, *, base: str, head: str, strict: bool = False) -> Dict[str, Any]:
        errors: list[str] = []
        warnings: list[str] = []
        try:
            git_root = Path(self._git_text("rev-parse", "--show-toplevel").strip()).resolve()
            if git_root != self.root:
                return self._result(
                    False,
                    [f"strict repository verification requires product root to equal Git root: {self.root} != {git_root}"],
                    warnings, base, head, [],
                )
            current_head = self._git_text("rev-parse", "HEAD").strip()
            base_sha = self._git_text("rev-parse", f"{base}^{{commit}}").strip()
            head_sha = self._git_text("rev-parse", f"{head}^{{commit}}").strip()
            if current_head != head_sha:
                errors.append(f"checked-out HEAD {current_head} does not match requested head {head_sha}")
            if strict:
                tracked_status = self._git_text("status", "--porcelain=v1", "--untracked-files=no")
                if tracked_status.strip():
                    errors.append("strict verification requires a clean tracked checkout")
            ancestor = self._run("merge-base", "--is-ancestor", base_sha, head_sha)
            if ancestor.returncode:
                errors.append("base is not an ancestor of head")
        except RepositoryVerificationError as exc:
            return self._result(False, [str(exc)], warnings, base, head, [])

        manager = ProjectContractManager(self.root)
        try:
            contract = manager.load(required=True)
            assert contract is not None
            status = manager.evaluate(contract)
            if status["state"] == "INVALID":
                errors.extend(status["errors"])
        except ProjectContractError as exc:
            return self._result(False, [str(exc)], warnings, base_sha, head_sha, [])

        try:
            registry = WorkflowRegistry()
            policy = registry.policy_lock()
        except WorkflowRegistryError as exc:
            errors.append(str(exc))
            policy = {"workflow_digests": {}}

        try:
            resolver = ArtifactResolver(self.root)
            for artifact in resolver.list(current_only=True):
                if not artifact.get("sha256"):
                    errors.append(f"CURRENT artifact has no SHA-256: {artifact.get('id')}")
                    continue
                resolver.by_id(artifact["id"], verify_hash=True)
        except ArtifactManifestError as exc:
            errors.append(str(exc))

        commits = self._git_text("rev-list", "--reverse", f"{base_sha}..{head_sha}").splitlines()
        checked_audits: list[str] = []
        contract_relative = ".mcu-agent/project-contract.json"
        contract_existed = self._path_exists_at(base_sha, contract_relative)
        layout_roots = [
            contract["layout"]["project"].rstrip("/"),
            contract["layout"]["hardware"].rstrip("/"),
            "docs",
        ]
        protected_roots = [value.rstrip("/") for value in contract.get("protected_paths", [])]
        for commit in commits:
            parents = self._git_text("show", "-s", "--format=%P", commit).split()
            if len(parents) != 1:
                errors.append(f"governed range contains a non-linear commit: {commit}")
                continue
            parent = parents[0]
            changed = self._changed_paths(parent, commit)
            initial_setup_commit = False
            protected_changes = [path for path in changed if self._under_any(path, protected_roots)]
            if protected_changes and contract_existed:
                errors.append(
                    f"protected policy paths changed; require an externally approved migration: {protected_changes}"
                )
            if contract_relative in changed:
                if contract_existed:
                    errors.append(f"project contract changed without a migration audit: {commit}")
                else:
                    disallowed_setup = [path for path in changed if not self._setup_path(path, contract)]
                    if disallowed_setup:
                        errors.append(f"initial project contract commit contains non-setup changes: {disallowed_setup}")
                    else:
                        initial_setup_commit = True
                    contract_existed = True
            if initial_setup_commit:
                continue
            manifest_relative = ".mcu-agent/artifact-manifest.yaml"
            if manifest_relative in changed:
                try:
                    registered_path = self._validate_user_manifest_delta(
                        self._file_at(parent, manifest_relative),
                        self._file_at(commit, manifest_relative),
                    )
                    registration_changes = {manifest_relative}
                    if registered_path in changed:
                        registration_changes.add(registered_path)
                    registered_mode = self._tree_mode_at(commit, registered_path)
                    if (
                        set(changed) == registration_changes
                        and not self._requires_build(registered_path, contract)
                        and registered_mode in {"100644", "100755"}
                    ):
                        continue
                except (RepositoryVerificationError, ArtifactManifestError, ValueError):
                    pass
            governed = [path for path in changed if self._under_any(path, layout_roots)]
            if not governed:
                if manifest_relative in changed:
                    errors.append(f"unaudited or invalid Artifact-manifest change: {commit}")
                continue
            trailers = self._trailers(commit)
            missing = [name for name in ("MCU-Claim", "MCU-Policy", "MCU-Verification") if not trailers.get(name)]
            if missing:
                errors.append(f"governed commit {commit} lacks trailers: {missing}")
                continue
            audit_id = trailers["MCU-Verification"]
            audit_relative = f".mcu-agent/audit/{audit_id}.json"
            if audit_relative not in self._added_paths(parent, commit):
                errors.append(f"promotion audit was not created by governed commit {commit}: {audit_relative}")
                continue
            try:
                audit = json.loads(self._file_at(commit, audit_relative).decode("utf-8-sig"))
                validate_contract(audit, "promotion-audit.schema.json", label=f"Promotion audit {audit_id}")
            except (RepositoryVerificationError, UnicodeDecodeError, json.JSONDecodeError, ContractValidationError) as exc:
                errors.append(f"cannot validate promotion audit {audit_id}: {exc}")
                continue
            checked_audits.append(audit_id)
            if audit.get("audit_id") != audit_id:
                errors.append(f"audit ID/path mismatch in {commit}")
            if audit["claim_id"] != trailers["MCU-Claim"]:
                errors.append(f"claim trailer mismatch in {commit}")
            if audit["workflow_digest"] != trailers["MCU-Policy"]:
                errors.append(f"policy trailer mismatch in {commit}")
            if audit.get("trailers") != {
                "MCU-Claim": trailers["MCU-Claim"],
                "MCU-Policy": trailers["MCU-Policy"],
                "MCU-Verification": trailers["MCU-Verification"],
            }:
                errors.append(f"embedded audit trailers mismatch in {commit}")
            if audit["base_commit"] != parent:
                errors.append(f"audit base commit mismatch in {commit}")
            if audit["project_contract_digest"] != manager.digest(contract):
                errors.append(f"audit project contract digest mismatch in {commit}")
            descriptor = None
            workflow_matches = [
                workflow_id for workflow_id, digest in policy.get("workflow_digests", {}).items()
                if digest == audit["workflow_digest"]
            ]
            if len(workflow_matches) != 1:
                errors.append(f"audit workflow digest is not uniquely present in policy.lock: {audit_id}")
            else:
                descriptor = registry.descriptor(workflow_matches[0])
                if audit.get("verification_profile") not in descriptor["verification_profiles"]:
                    errors.append(f"audit verification profile is not allowed by workflow {workflow_matches[0]}")
            covered = set(audit.get("changed_files", []))
            if descriptor is not None:
                task_allowed = list(audit.get("allowed_paths") or [])
                for value in task_allowed:
                    if not any(
                        WorkflowRegistry._path_within(value, policy_root)
                        for policy_root in descriptor["allowed_paths"]
                    ):
                        errors.append(f"audit allowed path exceeds workflow policy for {audit_id}: {value}")
                project_root = contract["layout"]["project"].rstrip("/")
                allowed_commit_roots = [
                    project_root if value.rstrip("/") in {"", "."}
                    else f"{project_root}/{value.strip('/')}"
                    for value in task_allowed
                ]
                out_of_scope = [path for path in covered if not self._under_any(path, allowed_commit_roots)]
                if out_of_scope:
                    errors.append(f"audit changes exceed task/workflow path scope for {audit_id}: {out_of_scope}")
            non_regular = [
                path for path in covered
                if (mode := self._tree_mode_at(commit, path)) is not None
                and mode not in {"100644", "100755"}
            ]
            if non_regular:
                errors.append(f"audit contains symlink/submodule/non-regular outputs: {non_regular}")
            metadata_paths = {audit_relative}
            if audit.get("output_artifact_ids"):
                manifest_relative = ".mcu-agent/artifact-manifest.yaml"
                metadata_paths.add(manifest_relative)
                if manifest_relative not in changed:
                    errors.append(f"audit declares output Artifacts but commit did not change manifest: {audit_id}")
                else:
                    try:
                        before_bytes = self._file_at(parent, manifest_relative)
                        after_bytes = self._file_at(commit, manifest_relative)
                        if hashlib.sha256(before_bytes).hexdigest() != audit.get("artifact_manifest_before_sha256"):
                            errors.append(f"artifact manifest before digest mismatch: {audit_id}")
                        if hashlib.sha256(after_bytes).hexdigest() != audit.get("artifact_manifest_after_sha256"):
                            errors.append(f"artifact manifest after digest mismatch: {audit_id}")
                        self._validate_manifest_delta(
                            before_bytes, after_bytes, set(audit["output_artifact_ids"]), audit["claim_id"]
                        )
                        after_manifest = yaml.safe_load(after_bytes.decode("utf-8-sig"))
                        promoted = {
                            item["id"]: item for item in after_manifest["artifacts"]
                            if item["id"] in set(audit["output_artifact_ids"])
                        }
                        output_roles = [item.get("role", "") for item in promoted.values()]
                        if descriptor is not None:
                            WorkflowRegistry._check_roles(
                                output_roles, descriptor["output_roles"], "output", workflow_matches[0]
                            )
                        required_roles = set(audit.get("required_output_roles") or [])
                        if required_roles and set(output_roles) != required_roles:
                            errors.append(
                                f"promoted Artifact roles do not match task-required outputs for {audit_id}: "
                                f"promoted={sorted(output_roles)}, required={sorted(required_roles)}"
                            )
                    except WorkflowRegistryError as exc:
                        errors.append(f"invalid promoted Artifact role for {audit_id}: {exc}")
                    except (RepositoryVerificationError, ArtifactManifestError, ValueError) as exc:
                        errors.append(f"invalid artifact manifest delta for {audit_id}: {exc}")
            elif ".mcu-agent/artifact-manifest.yaml" in changed:
                errors.append(f"governed commit changed Artifact manifest without declared outputs: {audit_id}")
            patch_changes = set(changed) - metadata_paths
            if covered != patch_changes:
                errors.append(
                    f"audit changed-file coverage is not exact for {audit_id}: "
                    f"audit={sorted(covered)}, commit={sorted(patch_changes)}"
                )
            patch = self._git_bytes("diff", "--binary", parent, commit, "--", *sorted(covered))
            if hashlib.sha256(patch).hexdigest() != audit["patch_sha256"]:
                errors.append(f"audit patch digest mismatch: {audit_id}")
            source_changes = [
                path for path in covered if self._requires_build(path, contract)
            ]
            if source_changes and audit.get("verification_tier") not in {"build", "target"}:
                errors.append(f"source changes require build/target verification: {source_changes}")
            verification = audit.get("verification") or {}
            tier = audit.get("verification_tier")
            profile = audit.get("verification_profile")
            if verification.get("passed") is not True:
                errors.append(f"audit verification did not pass: {audit_id}")
            if verification.get("tier") != tier:
                errors.append(f"embedded verification tier does not match audit tier: {audit_id}")
            if tier in {"host", "build"}:
                rerun = self._rerun_profiles_at_head(
                    head_sha, contract, [(tier, profile)]
                )[0]
                if rerun.get("passed") is not True or rerun.get("tier") != tier:
                    errors.append(f"trusted CI verification rerun did not pass for {audit_id}: {rerun}")
            if tier == "target":
                from .verifier import VerificationConfigError, validate_target_attestation
                target_record = verification.get("target") or {}
                steps = [("build", "build")]
                if target_record.get("kind") != "manual-attestation":
                    steps.append(("target", profile))
                reruns = self._rerun_profiles_at_head(head_sha, contract, steps)
                build_rerun = reruns[0]
                if build_rerun.get("passed") is not True or build_rerun.get("tier") != "build":
                    errors.append(f"trusted CI build rerun did not pass for target audit {audit_id}: {build_rerun}")
                if (verification.get("build") or {}).get("passed") is not True or target_record.get("passed") is not True:
                    errors.append(f"target audit lacks build and target success: {audit_id}")
                if target_record.get("kind") == "manual-attestation":
                    try:
                        validate_target_attestation(target_record, product_root=self.root)
                    except VerificationConfigError as exc:
                        errors.append(f"manual target attestation is invalid for {audit_id}: {exc}")
                else:
                    target_rerun = reruns[1] if len(reruns) > 1 else {
                        "passed": False, "tier": "target", "reason": "target rerun was not reached"
                    }
                    if target_rerun.get("passed") is not True or target_rerun.get("hil") is not True:
                        errors.append(f"trusted CI target/HIL rerun did not pass for {audit_id}: {target_rerun}")

        if strict and not commits:
            errors.append("strict verification range must contain at least one commit")
        return self._result(not errors, errors, warnings, base_sha, head_sha, checked_audits)

    @staticmethod
    def _validate_user_manifest_delta(before_bytes: bytes, after_bytes: bytes) -> str:
        before = yaml.safe_load(before_bytes.decode("utf-8-sig"))
        after = yaml.safe_load(after_bytes.decode("utf-8-sig"))
        if not isinstance(before, dict) or not isinstance(after, dict):
            raise ValueError("artifact manifests must be mappings")
        ArtifactResolver._validate_manifest(before)
        ArtifactResolver._validate_manifest(after)
        if int(after.get("revision", 0)) != int(before.get("revision", 0)) + 1:
            raise ValueError("input registration must advance the manifest exactly once")
        old = {item["id"]: item for item in before["artifacts"]}
        new = {item["id"]: item for item in after["artifacts"]}
        added = set(new) - set(old)
        if len(added) != 1 or set(old) - set(new):
            raise ValueError("input registration must add exactly one Artifact")
        artifact_id = next(iter(added))
        item = new[artifact_id]
        if (
            item.get("producer") != "user:artifact-register"
            or item.get("status") != "CURRENT"
            or item.get("accepted_by") != "user"
            or item.get("evidence_class") != "USER_SUPPLIED"
        ):
            raise ValueError("new Artifact is not an explicit user registration")
        superseded = set(item.get("supersedes") or [])
        if not superseded.issubset(old):
            raise ValueError("input registration references unknown superseded Artifacts")
        for old_id, old_item in old.items():
            current = new[old_id]
            if old_id in superseded:
                expected = dict(old_item); expected["status"] = "SUPERSEDED"
                if current != expected:
                    raise ValueError(f"superseded input Artifact changed beyond status: {old_id}")
                if (
                    old_item.get("scope", "product") != item.get("scope", "product")
                    or old_item.get("role") != item.get("role")
                ):
                    raise ValueError("input registration superseded a different scope/role")
            elif current != old_item:
                raise ValueError(f"unrelated Artifact changed during input registration: {old_id}")
        path = str(item.get("path") or "")
        if not path or path.startswith(".mcu-agent/") or path.startswith(".github/"):
            raise ValueError("input registration path is not allowed")
        return path

    @staticmethod
    def _validate_manifest_delta(before_bytes: bytes, after_bytes: bytes, output_ids: set[str], claim_id: str) -> None:
        before = yaml.safe_load(before_bytes.decode("utf-8-sig"))
        after = yaml.safe_load(after_bytes.decode("utf-8-sig"))
        if not isinstance(before, dict) or not isinstance(after, dict):
            raise ValueError("artifact manifests must be mappings")
        ArtifactResolver._validate_manifest(before)
        ArtifactResolver._validate_manifest(after)
        if int(after.get("revision", 0)) != int(before.get("revision", 0)) + 1:
            raise ValueError("artifact manifest revision must advance exactly once")
        old = {item["id"]: item for item in before["artifacts"]}
        new = {item["id"]: item for item in after["artifacts"]}
        if set(new) - set(old) != output_ids or set(old) - set(new):
            raise ValueError("manifest added/removed IDs do not match promotion outputs")
        superseded = set()
        superseded_by: Dict[str, Dict[str, Any]] = {}
        for artifact_id in output_ids:
            item = new[artifact_id]
            if item.get("producer") != f"promotion:{claim_id}" or item.get("status") != "CURRENT":
                raise ValueError(f"promoted Artifact has invalid producer/status: {artifact_id}")
            if not item.get("sha256"):
                raise ValueError(f"promoted Artifact has no SHA-256: {artifact_id}")
            for old_id in item.get("supersedes") or []:
                if old_id not in old:
                    raise ValueError(f"promoted Artifact supersedes an unknown ID: {old_id}")
                if old_id in superseded_by:
                    raise ValueError(f"multiple promoted Artifacts supersede the same ID: {old_id}")
                old_item = old[old_id]
                if (
                    old_item.get("scope", "product") != item.get("scope", "product")
                    or old_item.get("role") != item.get("role")
                ):
                    raise ValueError(f"promoted Artifact supersedes a different scope/role: {old_id}")
                superseded.add(old_id)
                superseded_by[old_id] = item
        for artifact_id, old_item in old.items():
            new_item = new[artifact_id]
            if artifact_id in superseded:
                expected = dict(old_item); expected["status"] = "SUPERSEDED"
                if new_item != expected:
                    raise ValueError(f"superseded Artifact changed beyond status: {artifact_id}")
            elif new_item != old_item:
                raise ValueError(f"unrelated Artifact changed in promotion: {artifact_id}")

    def _rerun_profiles_at_head(
        self,
        head: str,
        contract: Dict[str, Any],
        steps: list[tuple[str, str]],
    ) -> list[Dict[str, Any]]:
        """Run trusted profiles in a disposable clean worktree and reject tracked drift."""
        from .verifier import BuildVerifier

        results: list[Dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix="mcu-policy-") as temporary:
            worktree = Path(temporary) / "checkout"
            added = self._run("worktree", "add", "--detach", "--force", str(worktree), head)
            if added.returncode:
                return [{
                    "passed": False, "skipped": False, "tier": tier,
                    "reason": added.stderr.strip() or "cannot create trusted verification worktree",
                } for tier, _profile in steps]
            try:
                config = worktree / ".mcu-agent" / "config.yaml"
                verifier = BuildVerifier(config_path=config if config.is_file() else None)
                project = worktree / contract["layout"]["project"]
                for tier, profile in steps:
                    result = verifier.verify({
                        "project_root": str(project),
                        "product_root": str(worktree),
                        "verification_tier": tier,
                        "verification_profile": profile,
                    })
                    if tier == "target" and result.get("passed") is True:
                        result["hil"] = True
                    status = subprocess.run(
                        ["git", "-C", str(worktree), "status", "--porcelain=v1", "--untracked-files=no"],
                        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        check=False, timeout=60,
                    )
                    if status.returncode or status.stdout.strip():
                        result = dict(result)
                        result["passed"] = False
                        result["tracked_drift"] = status.stdout.splitlines()
                        result["reason"] = "verification command modified tracked files"
                    results.append(result)
                    if result.get("passed") is not True:
                        break
            finally:
                self._run("worktree", "remove", "--force", str(worktree))
                self._run("worktree", "prune")
        return results

    def _trailers(self, commit: str) -> Dict[str, str]:
        body = self._git_text("show", "-s", "--format=%B", commit)
        result: Dict[str, str] = {}
        for line in body.splitlines():
            match = re.match(r"^(MCU-Claim|MCU-Policy|MCU-Verification):\s*(\S+)\s*$", line)
            if match:
                result[match.group(1)] = match.group(2)
        return result

    def _changed_paths(self, parent: str, commit: str) -> list[str]:
        raw = self._git_bytes("diff", "--name-only", "-z", parent, commit, "--")
        return sorted(item.decode("utf-8", errors="surrogateescape") for item in raw.split(b"\0") if item)

    def _added_paths(self, parent: str, commit: str) -> set[str]:
        raw = self._git_bytes("diff", "--diff-filter=A", "--name-only", "-z", parent, commit, "--")
        return {
            item.decode("utf-8", errors="surrogateescape")
            for item in raw.split(b"\0") if item
        }

    def _file_at(self, commit: str, path: str) -> bytes:
        return self._git_bytes("show", f"{commit}:{path}")

    def _tree_mode_at(self, commit: str, path: str) -> str | None:
        completed = self._run("ls-tree", commit, "--", path)
        if completed.returncode or not completed.stdout.strip():
            return None
        return completed.stdout.split(None, 1)[0]

    def _path_exists_at(self, commit: str, path: str) -> bool:
        return self._run("cat-file", "-e", f"{commit}:{path}").returncode == 0

    @staticmethod
    def _under_any(path: str, roots: Iterable[str]) -> bool:
        return any(path == root or path.startswith(root + "/") for root in roots if root)

    @staticmethod
    def _setup_path(path: str, contract: Dict[str, Any]) -> bool:
        allowed = {
            ".mcu-agent/project-contract.json", ".mcu-agent/policy.lock",
            ".mcu-agent/artifact-manifest.yaml", ".mcu-agent/config.yaml",
            ".gitignore", "AGENTS.md", "CLAUDE.md",
        }
        if path in allowed or path.startswith(".agents/skills/mcu-agent/"):
            return True
        layout = contract["layout"]
        return path in {f"{layout['main']}/.gitkeep", f"{layout['components']}/.gitkeep"}

    @staticmethod
    def _requires_build(path: str, contract: Dict[str, Any]) -> bool:
        normalized = path.replace("\\", "/")
        layout = contract["layout"]
        for root in (layout["main"].rstrip("/"), layout["components"].rstrip("/")):
            if normalized == root or normalized.startswith(root + "/"):
                return True
        project = layout["project"].rstrip("/")
        if not (normalized == project or normalized.startswith(project + "/")):
            return False
        relative = normalized[len(project):].lstrip("/")
        if relative == "docs" or relative.startswith("docs/"):
            return False
        item = Path(normalized)
        suffixes = {
            ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".s", ".asm", ".ino",
            ".js", ".mjs", ".ts", ".tsx", ".vue", ".wxml", ".wxss",
            ".ld", ".lds", ".cmake", ".mk", ".py", ".sh", ".ps1", ".bat", ".cmd",
            ".rs", ".go", ".zig", ".toml",
        }
        name = item.name
        lowered = name.lower()
        exact_names = {
            "cmakelists.txt", "makefile", "gnumakefile", "platformio.ini", "cargo.lock",
            "build.rs", "west.yml", "west.yaml", "idf_component.yml", "idf_component.yaml",
            "cmakepresets.json", "cmakeuserpresets.json",
        }
        return (
            item.suffix.lower() in suffixes
            or lowered in exact_names
            or lowered.startswith("kconfig")
            or lowered.startswith("sdkconfig")
            or (lowered.startswith("partition") and item.suffix.lower() == ".csv")
        )

    @staticmethod
    def _result(passed: bool, errors: list[str], warnings: list[str], base: str, head: str, audits: list[str]) -> Dict[str, Any]:
        return {
            "schema_version": "1.0", "passed": passed,
            "base": base, "head": head,
            "errors": errors, "warnings": warnings,
            "checked_audits": audits,
        }

    def _git_text(self, *args: str) -> str:
        completed = self._run(*args)
        if completed.returncode:
            raise RepositoryVerificationError(completed.stderr.strip() or f"git {' '.join(args)} failed")
        return completed.stdout

    def _git_bytes(self, *args: str) -> bytes:
        completed = subprocess.run(
            ["git", "-C", str(self.root), *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if completed.returncode:
            raise RepositoryVerificationError(completed.stderr.decode(errors="replace").strip())
        return completed.stdout

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.root), *args], text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=60,
        )
