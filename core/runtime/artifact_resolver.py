"""Resolve and transactionally register canonical artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Dict, Iterable, List

from .contract_validator import ContractValidationError, validate_contract
from .locking import file_lock

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - JSON is valid YAML 1.2 fallback
    yaml = None


class ArtifactManifestError(ValueError):
    pass


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse_flag)


class ArtifactResolver:
    """Read one manifest; generated outputs are committed as one CAS transaction."""

    def __init__(self, product_root: str | Path, manifest_path: str | Path | None = None):
        self.product_root = Path(product_root).resolve()
        if manifest_path is None:
            # The canonical manifest is user-owned product data, intentionally
            # outside the replaceable runtime code and the JSON state store.
            self.manifest_path = self.product_root / ".mcu-agent" / "artifact-manifest.yaml"
        else:
            supplied = Path(manifest_path)
            self.manifest_path = supplied if supplied.is_absolute() else self.product_root / supplied
        self.manifest_path = self.manifest_path.absolute()
        self._ensure_manifest_confined()
        self.manifest = self._load()
        self._validate_manifest(self.manifest)

    def _ensure_manifest_confined(self) -> None:
        try:
            relative = self.manifest_path.relative_to(self.product_root)
        except ValueError as exc:
            raise ArtifactManifestError("artifact manifest escapes product root") from exc
        current = self.product_root
        for component in relative.parts:
            current = current / component
            if _is_link_or_reparse(current):
                raise ArtifactManifestError("artifact manifest path contains a symlink/junction")
        try:
            self.manifest_path.resolve().relative_to(self.product_root)
        except ValueError as exc:
            raise ArtifactManifestError("artifact manifest escapes product root") from exc

    def _load(self) -> Dict[str, Any]:
        return self._load_path(self.manifest_path)

    @staticmethod
    def _load_path(path: Path) -> Dict[str, Any]:
        if not path.is_file():
            raise ArtifactManifestError(f"canonical artifact manifest not found: {path}")
        text = path.read_text(encoding="utf-8-sig")
        try:
            payload = yaml.safe_load(text) if yaml is not None else json.loads(text)
        except Exception as exc:
            raise ArtifactManifestError(f"cannot parse artifact manifest: {exc}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("artifacts"), list):
            raise ArtifactManifestError("artifact manifest must contain an artifacts array")
        return payload

    @classmethod
    def _validate_manifest(cls, manifest: Dict[str, Any]) -> None:
        ids = set()
        current_roles = set()
        current_paths = set()
        enforce_roles = manifest.get("schema_version") == "2.0"
        try:
            int(manifest.get("revision", 0))
        except (TypeError, ValueError) as exc:
            raise ArtifactManifestError("artifact manifest revision must be an integer") from exc
        for artifact in manifest.get("artifacts", []):
            if not isinstance(artifact, dict):
                raise ArtifactManifestError("artifact entries must be objects")
            artifact_id = artifact.get("id")
            if not artifact_id or artifact_id in ids:
                raise ArtifactManifestError(f"duplicate or missing artifact id: {artifact_id!r}")
            ids.add(artifact_id)
            if artifact.get("status") != "CURRENT":
                continue
            role_key = (artifact.get("scope", "product"), artifact.get("role"))
            if enforce_roles:
                if not role_key[1]:
                    raise ArtifactManifestError(f"CURRENT v2 artifact has no role: {artifact_id}")
                if role_key in current_roles:
                    raise ArtifactManifestError(f"multiple CURRENT artifacts for scope/role: {role_key}")
                current_roles.add(role_key)
            path = artifact.get("path")
            if not path or path in current_paths:
                raise ArtifactManifestError(f"multiple CURRENT artifacts use path: {path}")
            current_paths.add(path)
        if enforce_roles:
            try:
                validate_contract(
                    manifest,
                    "artifact-manifest-v2.schema.json",
                    label="Artifact manifest",
                )
            except ContractValidationError as exc:
                raise ArtifactManifestError(str(exc)) from exc

    def _validate_uniqueness(self) -> None:  # compatibility
        self._validate_manifest(self.manifest)

    def resolve(self, role: str, *, scope: str = "product", verify_hash: bool = True) -> Dict[str, Any]:
        matches = [artifact for artifact in self.manifest["artifacts"] if artifact.get("role") == role and artifact.get("scope", "product") == scope and artifact.get("status") == "CURRENT"]
        if not matches:
            raise ArtifactManifestError(f"no CURRENT artifact for {scope}/{role}")
        artifact = dict(matches[0])
        path = self._verified_regular_path(artifact["path"], artifact_id=artifact["id"])
        if verify_hash and artifact.get("sha256"):
            actual = _file_hash(path)
            if actual.lower() != artifact["sha256"].lower():
                raise ArtifactManifestError(f"artifact hash mismatch: {artifact['id']}")
        artifact["resolved_path"] = str(path)
        return artifact

    def resolve_many(self, roles: Iterable[str], *, scope: str = "product") -> List[Dict[str, Any]]:
        return [self.resolve(role, scope=scope) for role in roles]

    def by_id(self, artifact_id: str, *, verify_hash: bool = True) -> Dict[str, Any]:
        matches = [artifact for artifact in self.manifest["artifacts"] if artifact.get("id") == artifact_id]
        if not matches:
            raise ArtifactManifestError(f"unknown artifact id: {artifact_id}")
        artifact = dict(matches[0])
        path = self._verified_regular_path(artifact["path"], artifact_id=artifact_id)
        if verify_hash and artifact.get("sha256") and _file_hash(path).lower() != artifact["sha256"].lower():
            raise ArtifactManifestError(f"artifact hash mismatch: {artifact_id}")
        artifact["resolved_path"] = str(path)
        return artifact

    def list(self, *, scope: str | None = None, current_only: bool = False) -> List[Dict[str, Any]]:
        values = []
        for item in self.manifest["artifacts"]:
            if scope is not None and item.get("scope", "product") != scope:
                continue
            if current_only and item.get("status") != "CURRENT":
                continue
            values.append(deepcopy(item))
        return values

    def register_input(
        self,
        *,
        role: str,
        path: str,
        scope: str = "product",
        artifact_id: str | None = None,
        logical_name: str | None = None,
        media_type: str = "application/octet-stream",
        expected_revision: int | None = None,
    ) -> Dict[str, Any]:
        """Register one explicit user-selected input with lock, CAS and hash proof."""
        if not role or not scope:
            raise ArtifactManifestError("input artifact role and scope are required")
        lexical = self.product_root / Path(path.replace("\\", "/"))
        resolved = self._confined_path(path)
        current = lexical
        while current != self.product_root:
            if _is_link_or_reparse(current):
                raise ArtifactManifestError(f"input artifact path contains a symlink/junction: {path}")
            current = current.parent
        if not resolved.is_file() or resolved.is_symlink():
            raise ArtifactManifestError(f"input artifact path is missing or not a regular file: {resolved}")
        digest = _file_hash(resolved)
        if artifact_id is None:
            identity = hashlib.sha256(f"{scope}\0{role}\0{path}\0{digest}".encode("utf-8")).hexdigest()[:16]
            artifact_id = f"input-{identity}"
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ArtifactManifestError("input artifact id must be a non-empty string")
        lock_path = self.product_root / ".mcu-agent" / "state" / ".locks" / "artifact-manifest.lock"
        with file_lock(lock_path):
            disk = self._load_path(self.manifest_path)
            self._validate_manifest(disk)
            revision = int(disk.get("revision", 0))
            compare = int(self.manifest.get("revision", 0)) if expected_revision is None else int(expected_revision)
            if revision != compare:
                raise ArtifactManifestError(f"artifact manifest revision conflict: expected {compare}, found {revision}")
            if any(item.get("id") == artifact_id for item in disk["artifacts"]):
                existing = next(item for item in disk["artifacts"] if item.get("id") == artifact_id)
                if (
                    existing.get("status") == "CURRENT"
                    and existing.get("scope", "product") == scope
                    and existing.get("role") == role
                    and existing.get("path") == resolved.relative_to(self.product_root).as_posix()
                    and existing.get("sha256") == digest
                ):
                    self.manifest = disk
                    return deepcopy(existing)
                raise ArtifactManifestError(f"duplicate artifact id: {artifact_id}")
            candidate = deepcopy(disk)
            superseded: List[str] = []
            artifact_revision = 1
            for item in candidate["artifacts"]:
                if item.get("scope", "product") == scope and item.get("role") == role and item.get("status") == "CURRENT":
                    item["status"] = "SUPERSEDED"
                    superseded.append(item["id"])
                    artifact_revision = max(artifact_revision, int(item.get("revision", 1)) + 1)
            entry = {
                "id": artifact_id,
                "scope": scope,
                "role": role,
                "logical_name": logical_name or resolved.name,
                "path": resolved.relative_to(self.product_root).as_posix(),
                "media_type": media_type,
                "identity": artifact_id,
                "revision": artifact_revision,
                "sha256": digest,
                "producer": "user:artifact-register",
                "dependencies": [],
                "supersedes": superseded,
                "evidence_class": "USER_SUPPLIED",
                "status": "CURRENT",
                "accepted_by": "user",
                "accepted_at": datetime.now(timezone.utc).isoformat(),
            }
            candidate["artifacts"].append(entry)
            candidate["revision"] = revision + 1
            self._validate_manifest(candidate)
            _atomic_manifest_write(self.manifest_path, candidate)
            self.manifest = candidate
            return deepcopy(entry)

    def register_generated(
        self,
        spec: Dict[str, Any],
        *,
        producer: str,
        allowed_roles: Iterable[str] | None = None,
        allowed_roots: Iterable[str | Path] | None = None,
        expected_revision: int | None = None,
    ) -> Dict[str, Any]:
        return self.register_generated_many(
            [spec], producer=producer, allowed_roles=allowed_roles,
            allowed_roots=allowed_roots, expected_revision=expected_revision,
        )[0]

    def register_generated_many(
        self,
        specs: Iterable[Dict[str, Any]],
        *,
        producer: str,
        allowed_roles: Iterable[str] | None = None,
        allowed_roots: Iterable[str | Path] | None = None,
        expected_revision: int | None = None,
    ) -> List[Dict[str, Any]]:
        return self._register_generated_many(
            specs,
            producer=producer,
            allowed_roles=allowed_roles,
            allowed_roots=allowed_roots,
            expected_revision=expected_revision,
        )

    def register_generated_many_transactional(
        self,
        specs: Iterable[Dict[str, Any]],
        *,
        producer: str,
        allowed_roles: Iterable[str] | None = None,
        allowed_roots: Iterable[str | Path] | None = None,
        expected_revision: int | None = None,
        after_manifest_commit: Callable[[], None],
    ) -> List[Dict[str, Any]]:
        """Commit a manifest and callback as one rollback-capable lifecycle step."""
        return self._register_generated_many(
            specs,
            producer=producer,
            allowed_roles=allowed_roles,
            allowed_roots=allowed_roots,
            expected_revision=expected_revision,
            after_manifest_commit=after_manifest_commit,
        )

    def _register_generated_many(
        self,
        specs: Iterable[Dict[str, Any]],
        *,
        producer: str,
        allowed_roles: Iterable[str] | None = None,
        allowed_roots: Iterable[str | Path] | None = None,
        expected_revision: int | None = None,
        after_manifest_commit: Callable[[], None] | None = None,
    ) -> List[Dict[str, Any]]:
        """Validate every output in memory, then lock+CAS+atomically commit once."""
        incoming = [deepcopy(item) for item in specs]
        if not incoming:
            return []
        roles = set(allowed_roles) if allowed_roles is not None else None
        roots = [Path(root).resolve() for root in allowed_roots] if allowed_roots is not None else [self.product_root]
        prepared: list[tuple[Dict[str, Any], Path, str]] = []
        required = ("id", "role", "logical_name", "path", "media_type", "evidence_class")
        seen_ids: set[str] = set()
        seen_roles: set[tuple[str, str]] = set()
        for spec in incoming:
            if not isinstance(spec, dict):
                raise ArtifactManifestError("generated artifact must be an object")
            missing = [field for field in required if not isinstance(spec.get(field), str) or not spec.get(field)]
            if missing:
                raise ArtifactManifestError(f"generated artifact missing or invalid fields: {', '.join(missing)}")
            for field in ("dependencies", "supersedes"):
                value = spec.get(field, [])
                if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
                    raise ArtifactManifestError(f"generated artifact {field} must be a string array")
            try:
                requested_revision = int(spec.get("revision", 1))
            except (TypeError, ValueError) as exc:
                raise ArtifactManifestError("generated artifact revision must be an integer") from exc
            if requested_revision < 1:
                raise ArtifactManifestError("generated artifact revision must be positive")
            if roles is not None and spec["role"] not in roles:
                raise ArtifactManifestError(f"output role is not declared by Task: {spec['role']}")
            scope = spec.get("scope", "project")
            key = (scope, spec["role"])
            if spec["id"] in seen_ids or key in seen_roles:
                raise ArtifactManifestError("generated output batch contains duplicate id or CURRENT role")
            seen_ids.add(spec["id"])
            seen_roles.add(key)
            path = self._verified_regular_path(spec["path"], artifact_id=spec["id"])
            if not any(_within(path, root) for root in roots):
                raise ArtifactManifestError(f"generated artifact path is outside Task allowed roots: {path}")
            prepared.append((spec, path, _file_hash(path)))

        lock_path = self.product_root / ".mcu-agent" / "state" / ".locks" / "artifact-manifest.lock"
        with file_lock(lock_path):
            disk = self._load_path(self.manifest_path)
            self._validate_manifest(disk)
            revision = int(disk.get("revision", 0))
            compare = int(self.manifest.get("revision", 0)) if expected_revision is None else int(expected_revision)
            if revision != compare:
                raise ArtifactManifestError(f"artifact manifest revision conflict: expected {compare}, found {revision}")
            candidate = deepcopy(disk)
            registered: List[Dict[str, Any]] = []
            existing_ids = {item.get("id") for item in candidate["artifacts"]}
            for spec, path, digest in prepared:
                if spec["id"] in existing_ids:
                    raise ArtifactManifestError(f"duplicate artifact id: {spec['id']}")
                scope = spec.get("scope", "project")
                declared_supersedes = set(spec.get("supersedes", []))
                for old_id in declared_supersedes:
                    matches = [artifact for artifact in candidate["artifacts"] if artifact.get("id") == old_id]
                    if not matches:
                        raise ArtifactManifestError(f"generated artifact supersedes unknown id: {old_id}")
                    old = matches[0]
                    if old.get("scope", "product") != scope or old.get("role") != spec["role"]:
                        raise ArtifactManifestError(
                            f"generated artifact supersedes a different scope/role: {old_id}"
                        )
                superseded = []
                for artifact in candidate["artifacts"]:
                    if artifact.get("scope", "product") == scope and artifact.get("role") == spec["role"] and artifact.get("status") == "CURRENT":
                        artifact["status"] = "SUPERSEDED"
                        superseded.append(artifact["id"])
                entry = {
                    "id": spec["id"], "scope": scope, "role": spec["role"],
                    "logical_name": spec["logical_name"], "path": path.relative_to(self.product_root).as_posix(),
                    "media_type": spec["media_type"], "identity": spec.get("identity", spec["id"]),
                    "revision": int(spec.get("revision", revision + 1)), "sha256": digest,
                    "producer": producer, "dependencies": list(spec.get("dependencies", [])),
                    "supersedes": list(spec.get("supersedes", [])) + superseded,
                    "evidence_class": spec["evidence_class"], "status": "CURRENT",
                    "accepted_by": spec.get("accepted_by"), "accepted_at": spec.get("accepted_at"),
                }
                candidate["artifacts"].append(entry)
                existing_ids.add(entry["id"])
                registered.append(entry)
            candidate["revision"] = revision + 1
            self._validate_manifest(candidate)
            original_bytes = self.manifest_path.read_bytes()
            _atomic_manifest_write(self.manifest_path, candidate)
            try:
                if after_manifest_commit is not None:
                    after_manifest_commit()
            except BaseException:
                try:
                    _atomic_bytes_write(self.manifest_path, original_bytes)
                    self.manifest = disk
                except Exception as rollback_error:
                    raise ArtifactManifestError(
                        f"lifecycle commit failed and manifest rollback also failed: {rollback_error}"
                    ) from rollback_error
                raise
            self.manifest = candidate
            return deepcopy(registered)

    def _verified_regular_path(self, value: str, *, artifact_id: str) -> Path:
        normalized = str(value).replace("\\", "/")
        candidate = Path(normalized)
        path = self._confined_path(normalized)
        current = self.product_root
        for component in candidate.parts:
            current = current / component
            if _is_link_or_reparse(current):
                raise ArtifactManifestError(
                    f"artifact path contains a symlink/junction: {artifact_id}"
                )
        if not path.is_file():
            raise ArtifactManifestError(f"artifact path does not exist: {path}")
        return path

    def _confined_path(self, value: str) -> Path:
        normalized = str(value).replace("\\", "/")
        candidate = Path(normalized)
        windows_candidate = PureWindowsPath(value)
        if candidate.is_absolute() or windows_candidate.is_absolute() or windows_candidate.drive:
            raise ArtifactManifestError(f"artifact path must be product-relative: {value}")
        if ".." in candidate.parts or ".." in windows_candidate.parts:
            raise ArtifactManifestError(f"artifact path contains parent traversal: {value}")
        resolved = (self.product_root / candidate).resolve()
        if not _within(resolved, self.product_root):
            raise ArtifactManifestError(f"artifact path escapes product root: {value}")
        return resolved


def write_manifest(path: str | Path, manifest: Dict[str, Any]) -> Path:
    target = Path(path)
    ArtifactResolver._validate_manifest(manifest)
    _atomic_manifest_write(target, manifest)
    return target


def _atomic_manifest_write(path: Path, manifest: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False) if yaml is not None else json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _atomic_bytes_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.rollback.tmp"
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


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
