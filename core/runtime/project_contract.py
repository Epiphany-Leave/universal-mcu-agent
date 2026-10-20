"""Tracked project contract and derived fail-closed project status."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Iterable

from .artifact_resolver import ArtifactManifestError, ArtifactResolver
from .contract_validator import ContractValidationError, validate_contract
from .locking import file_lock
from .state_store import StateStore


PROJECT_STATES = {
    "UNINITIALIZED",
    "PROJECT_INITIALIZED",
    "EVIDENCE_PENDING",
    "IMPLEMENTATION_ALLOWED",
    "INVALID",
}
DEFAULT_IMPLEMENTATION_ROLES = [
    "requirements.structured",
    "device.project.capabilities",
    "hardware.peripheral_connections",
]
DEFAULT_PROTECTED_PATHS = [
    ".mcu-agent/project-contract.json",
    ".mcu-agent/policy.lock",
    ".mcu-agent/config.yaml",
    ".github/workflows",
]


class ProjectContractError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_digest(value: Dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _safe_relative(value: str, *, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ProjectContractError(f"{label} must be a non-empty product-relative path")
    if "\\" in value:
        raise ProjectContractError(f"{label} must use canonical forward slashes: {value}")
    rendered = value
    candidate = Path(rendered)
    windows = PureWindowsPath(value)
    if candidate.is_absolute() or windows.is_absolute() or windows.drive or ".." in candidate.parts or ".." in windows.parts:
        raise ProjectContractError(f"{label} must be a confined product-relative path: {value}")
    if candidate.as_posix() != rendered or "/./" in f"/{rendered}/" or "//" in rendered:
        raise ProjectContractError(f"{label} is not canonically normalized: {value}")
    return candidate


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse_flag)


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class ProjectContractManager:
    """Own the tracked contract while deriving mutable status from real files."""

    def __init__(self, product_root: str | Path):
        self.product_root = Path(product_root).resolve()
        self.agent_root = self.product_root / ".mcu-agent"
        self.contract_path = self.agent_root / "project-contract.json"
        self.policy_path = self.agent_root / "policy.lock"
        self.state = StateStore(self.product_root)

    def load(self, *, required: bool = False) -> Dict[str, Any] | None:
        self._validate_control_paths()
        if not self.contract_path.exists():
            if required:
                raise ProjectContractError(f"project contract not found: {self.contract_path}")
            return None
        if self.contract_path.is_symlink() or not self.contract_path.is_file():
            raise ProjectContractError("project contract must be a regular file, not a symlink")
        try:
            value = json.loads(self.contract_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectContractError(f"cannot parse project contract: {exc}") from exc
        if not isinstance(value, dict):
            raise ProjectContractError("project contract must be a JSON object")
        try:
            validate_contract(value, "project-contract.schema.json", label="Project contract")
        except ContractValidationError as exc:
            raise ProjectContractError(str(exc)) from exc
        self._resolved_layout(value)
        protected = set(value.get("protected_paths", []))
        missing_protected = sorted(set(DEFAULT_PROTECTED_PATHS) - protected)
        required_roles = set(value.get("gates", {}).get("implementation_required_roles", []))
        missing_roles = sorted(set(DEFAULT_IMPLEMENTATION_ROLES) - required_roles)
        if missing_protected:
            raise ProjectContractError(f"project contract omits mandatory protected paths: {missing_protected}")
        if missing_roles:
            raise ProjectContractError(f"project contract omits mandatory implementation Artifact roles: {missing_roles}")
        self._validate_policy_lock()
        return value

    def digest(self, contract: Dict[str, Any] | None = None) -> str | None:
        value = contract if contract is not None else self.load()
        return _canonical_digest(value) if value is not None else None

    def create(
        self,
        *,
        project_name: str,
        paths: Dict[str, Path],
        platform: str | None = None,
        chip: str | None = None,
        implementation_required_roles: Iterable[str] = DEFAULT_IMPLEMENTATION_ROLES,
    ) -> Dict[str, Any]:
        self._validate_control_paths()
        root = self.product_root
        normalized_name = project_name.strip()
        project_id = re.sub(r"[^a-z0-9._-]+", "-", normalized_name.lower().replace("_", "-")).strip("-._")
        if not project_id:
            raise ProjectContractError("project name must produce a stable project_id")
        layout = {
            "hardware": self._layout_relative("hardware", paths["hardware_root"]),
            "software": self._layout_relative("software", paths["software_root"]),
            "project": self._layout_relative("project", paths["project_root"]),
            "main": self._layout_relative("main", paths["main_root"]),
            "components": self._layout_relative("components", paths["components_root"]),
        }
        now = _now()
        contract = {
            "schema_version": "1.0",
            "project_id": project_id,
            "project_name": normalized_name,
            "revision": 1,
            "layout": layout,
            "platform": {"family": platform or None, "chip": chip or None},
            "protected_paths": list(DEFAULT_PROTECTED_PATHS),
            "gates": {"implementation_required_roles": list(dict.fromkeys(implementation_required_roles))},
            "created_at": now,
            "updated_at": now,
        }
        validate_contract(contract, "project-contract.schema.json", label="Project contract")
        missing_roles = sorted(
            set(DEFAULT_IMPLEMENTATION_ROLES)
            - set(contract["gates"]["implementation_required_roles"])
        )
        if missing_roles:
            raise ProjectContractError(
                f"project contract omits mandatory implementation Artifact roles: {missing_roles}"
            )
        self._resolved_layout(contract)
        from .workflow_registry import WorkflowRegistry
        policy_lock = WorkflowRegistry().policy_lock()
        validate_contract(policy_lock, "policy-lock.schema.json", label="Policy lock")
        lock_path = self.state.path(".locks/project-contract.lock")
        with file_lock(lock_path):
            existing = self.load()
            if existing is not None:
                comparable_existing = deepcopy(existing)
                comparable_new = deepcopy(contract)
                for value in (comparable_existing, comparable_new):
                    value.pop("created_at", None)
                    value.pop("updated_at", None)
                    value.pop("revision", None)
                if comparable_existing != comparable_new:
                    raise ProjectContractError(
                        "project contract already exists with different values; use an explicit migration proposal/apply flow"
                    )
                evaluation = self.evaluate(existing)
                if evaluation["state"] == "INVALID":
                    raise ProjectContractError("existing project contract/layout is invalid: " + "; ".join(evaluation["errors"]))
                self.persist_status(evaluation)
                return existing
            self.contract_path.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(self.policy_path, policy_lock)
            try:
                self._atomic_write(self.contract_path, contract)
            except BaseException:
                try: self.policy_path.unlink()
                except FileNotFoundError: pass
                raise
        try:
            evaluation = self.evaluate(contract)
            if evaluation["state"] == "INVALID":
                raise ProjectContractError("new project contract/layout is invalid: " + "; ".join(evaluation["errors"]))
            self.persist_status(evaluation)
        except BaseException:
            for path in (self.contract_path, self.policy_path):
                try: path.unlink()
                except FileNotFoundError: pass
            raise
        return contract

    def evaluate(self, contract: Dict[str, Any] | None = None) -> Dict[str, Any]:
        try:
            value = contract if contract is not None else self.load()
        except ProjectContractError as exc:
            return self._status("INVALID", None, None, [], [str(exc)])
        if value is None:
            return self._status("UNINITIALIZED", None, None, [], [])
        digest = self.digest(value)
        try:
            layout = self._resolved_layout(value)
        except ProjectContractError as exc:
            return self._status("INVALID", digest, None, [], [str(exc)])
        missing_directories = [
            key for key in ("hardware", "software", "project", "main", "components")
            if not layout[key].is_dir()
        ]
        errors = [f"contract layout directory is missing: {key}={layout[key]}" for key in missing_directories]
        if (self.product_root / "firmware").exists():
            errors.append("root-level firmware/ is forbidden")
        if (self.product_root / "drivers").exists():
            errors.append("root-level drivers/ is forbidden")
        if errors:
            return self._status("INVALID", digest, str(layout["project"]), [], errors)
        required_roles = list(value["gates"]["implementation_required_roles"])
        missing_roles: list[str] = []
        manifest_exists = (self.agent_root / "artifact-manifest.yaml").is_file()
        resolver = None
        if manifest_exists:
            try:
                resolver = ArtifactResolver(self.product_root)
            except ArtifactManifestError as exc:
                return self._status(
                    "INVALID", digest, str(layout["project"]), [],
                    [f"canonical artifact manifest is invalid: {exc}"],
                )
        for role in required_roles:
            try:
                if resolver is None:
                    raise ArtifactManifestError("manifest unavailable")
                artifact = resolver.resolve(role, scope="project")
                if not artifact.get("sha256"):
                    raise ArtifactManifestError(f"required artifact has no SHA-256: {artifact.get('id')}")
            except ArtifactManifestError:
                missing_roles.append(role)
        if not manifest_exists:
            state = "PROJECT_INITIALIZED"
        elif missing_roles:
            state = "EVIDENCE_PENDING"
        else:
            state = "IMPLEMENTATION_ALLOWED"
        return self._status(state, digest, str(layout["project"]), missing_roles, [])

    def persist_status(self, status: Dict[str, Any]) -> Dict[str, Any]:
        validate_contract(status, "project-status.schema.json", label="Project status")

        def update(_existing: Any) -> Dict[str, Any]:
            value = deepcopy(status)
            value.pop("revision", None)
            return value

        committed = self.state.update("project-status.json", update, default={})
        validate_contract(committed, "project-status.schema.json", label="Project status")
        return committed

    def platform_capability(self, contract: Dict[str, Any]) -> Dict[str, Any]:
        layout = self._resolved_layout(contract)
        family = contract["platform"].get("family")
        facts: Dict[str, Any] = {
            "root": {
                "state": "CONFIRMED",
                "value": str(layout["project"]),
                "evidence_ids": [f"project-contract:{self.digest(contract)}"],
            }
        }
        if family:
            facts["family"] = {
                "state": "CONFIRMED", "value": family,
                "evidence_ids": [f"project-contract:{self.digest(contract)}"],
            }
        return {"schema_version": "2.0", "id": "platform.project", "state": "AVAILABLE", "facts": facts}

    def project_root(self, contract: Dict[str, Any]) -> Path:
        return self._resolved_layout(contract)["project"]

    def _resolved_layout(self, contract: Dict[str, Any]) -> Dict[str, Path]:
        result: Dict[str, Path] = {}
        for key, raw in contract["layout"].items():
            relative = _safe_relative(raw, label=f"layout.{key}")
            lexical = self.product_root / relative
            current = self.product_root
            for component in relative.parts:
                current = current / component
                if _is_link_or_reparse(current):
                    raise ProjectContractError(f"layout.{key} contains a symlink/junction component: {raw}")
            resolved = lexical.resolve()
            try:
                resolved.relative_to(self.product_root)
            except ValueError as exc:
                raise ProjectContractError(f"layout.{key} escapes product root: {raw}") from exc
            result[key] = resolved
        try:
            result["project"].relative_to(result["software"])
            result["main"].relative_to(result["project"])
            result["components"].relative_to(result["project"])
        except ValueError as exc:
            raise ProjectContractError(
                "canonical layout requires project below software and main/components below project"
            ) from exc
        if result["main"] == result["components"]:
            raise ProjectContractError("main and components layout roots must be distinct")
        if result["main"].parent != result["project"] or result["components"].parent != result["project"]:
            raise ProjectContractError("main and components must be direct children of the project root")
        if (
            result["hardware"] == result["software"]
            or result["hardware"] == result["project"]
            or _path_within(result["hardware"], result["software"])
            or _path_within(result["software"], result["hardware"])
        ):
            raise ProjectContractError("hardware layout must be disjoint from software/project roots")
        return result

    @staticmethod
    def _status(
        state: str,
        digest: str | None,
        project_root: str | None,
        missing_roles: list[str],
        errors: list[str],
    ) -> Dict[str, Any]:
        return {
            "schema_version": "1.0", "state": state, "contract_digest": digest,
            "project_root": project_root, "missing_artifact_roles": sorted(set(missing_roles)),
            "errors": errors, "evaluated_at": _now(),
        }

    def _layout_relative(self, key: str, value: Path) -> str:
        """Canonical posix path of ``value`` below the product root.

        Matches the caller-supplied prefix against the resolved product root via
        ``samefile`` so Windows 8.3 short names (TEMP) do not break containment;
        then rejects symlink/junction components and paths escaping the root,
        mirroring ``_resolved_layout`` validation for stored contracts.
        """
        path = Path(value)
        if not path.is_absolute():
            path = self.product_root / path
        components: tuple[str, ...] | None = None
        for index in range(1, len(path.parts) + 1):
            prefix = Path(*path.parts[:index])
            try:
                if prefix.samefile(self.product_root):
                    components = path.parts[index:]
                    break
            except OSError:
                continue
        if not components:
            raise ProjectContractError(f"layout.{key} must be a path inside the product root: {value}")
        candidate = self.product_root
        for component in components:
            candidate = candidate / component
            if _is_link_or_reparse(candidate):
                raise ProjectContractError(
                    f"layout.{key} contains a symlink/junction component: {'/'.join(components)}"
                )
        relative = "/".join(components)
        try:
            (self.product_root / relative).resolve().relative_to(self.product_root)
        except ValueError as exc:
            raise ProjectContractError(f"layout.{key} escapes product root: {value}") from exc
        return relative

    def _validate_control_paths(self) -> None:
        """Keep tracked governance files at their canonical lexical location."""
        for path in (self.agent_root, self.contract_path, self.policy_path):
            current = self.product_root
            try:
                relative = path.relative_to(self.product_root)
            except ValueError as exc:
                raise ProjectContractError(f"governance path escapes product root: {path}") from exc
            for component in relative.parts:
                current = current / component
                if _is_link_or_reparse(current):
                    raise ProjectContractError(f"governance path contains a symlink/junction component: {path}")
            try:
                path.resolve().relative_to(self.product_root)
            except ValueError as exc:
                raise ProjectContractError(f"governance path escapes product root: {path}") from exc

    def _validate_policy_lock(self) -> Dict[str, Any]:
        if not self.policy_path.is_file() or self.policy_path.is_symlink():
            raise ProjectContractError(f"workflow policy lock not found or invalid: {self.policy_path}")
        try:
            value = json.loads(self.policy_path.read_text(encoding="utf-8-sig"))
            validate_contract(value, "policy-lock.schema.json", label="Policy lock")
        except (OSError, json.JSONDecodeError, ContractValidationError) as exc:
            raise ProjectContractError(f"cannot validate workflow policy lock: {exc}") from exc
        from .workflow_registry import WorkflowRegistry
        current = WorkflowRegistry().policy_lock()
        if value.get("policy_digest") != current.get("policy_digest") or value.get("workflow_digests") != current.get("workflow_digests"):
            raise ProjectContractError("workflow policy lock does not match the installed runtime")
        return value

    @staticmethod
    def _atomic_write(path: Path, value: Dict[str, Any]) -> None:
        data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
