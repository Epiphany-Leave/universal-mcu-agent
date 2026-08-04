"""Resolve workflow inputs exclusively through the canonical artifact manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - JSON is valid YAML 1.2 fallback
    yaml = None


class ArtifactManifestError(ValueError):
    pass


class ArtifactResolver:
    """Read a single manifest; this class intentionally has no discovery scan."""

    def __init__(self, product_root: str | Path, manifest_path: str | Path | None = None):
        self.product_root = Path(product_root).resolve()
        self.manifest_path = Path(manifest_path) if manifest_path else self.product_root / ".mcu-agent" / "artifact-manifest.yaml"
        if not self.manifest_path.is_absolute():
            self.manifest_path = self.product_root / self.manifest_path
        self.manifest = self._load()
        self._validate_uniqueness()

    def _load(self) -> Dict[str, Any]:
        if not self.manifest_path.is_file():
            raise ArtifactManifestError(f"canonical artifact manifest not found: {self.manifest_path}")
        text = self.manifest_path.read_text(encoding="utf-8-sig")
        try:
            payload = yaml.safe_load(text) if yaml is not None else json.loads(text)
        except Exception as exc:
            raise ArtifactManifestError(f"cannot parse artifact manifest: {exc}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("artifacts"), list):
            raise ArtifactManifestError("artifact manifest must contain an artifacts array")
        return payload

    def _validate_uniqueness(self) -> None:
        ids = set()
        current_roles = set()
        current_paths = set()
        enforce_roles = self.manifest.get("schema_version") == "2.0"
        for artifact in self.manifest["artifacts"]:
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
            if path in current_paths:
                raise ArtifactManifestError(f"multiple CURRENT artifacts use path: {path}")
            current_paths.add(path)

    def resolve(self, role: str, *, scope: str = "product", verify_hash: bool = True) -> Dict[str, Any]:
        matches = [
            artifact for artifact in self.manifest["artifacts"]
            if artifact.get("role") == role
            and artifact.get("scope", "product") == scope
            and artifact.get("status") == "CURRENT"
        ]
        if not matches:
            raise ArtifactManifestError(f"no CURRENT artifact for {scope}/{role}")
        artifact = dict(matches[0])
        path = self._confined_path(artifact["path"])
        if not path.is_file():
            raise ArtifactManifestError(f"artifact path does not exist: {path}")
        if verify_hash and artifact.get("sha256"):
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
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
        path = self._confined_path(artifact["path"])
        if verify_hash and artifact.get("sha256") and path.is_file():
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual.lower() != artifact["sha256"].lower():
                raise ArtifactManifestError(f"artifact hash mismatch: {artifact_id}")
        artifact["resolved_path"] = str(path)
        return artifact

    def register_generated(self, spec: Dict[str, Any], *, producer: str) -> Dict[str, Any]:
        """Register one model output without allowing duplicate CURRENT roles."""
        if self.manifest.get("schema_version") != "2.0":
            raise ArtifactManifestError("generated outputs require a v2 canonical manifest")
        required = ("id", "role", "logical_name", "path", "media_type", "evidence_class")
        missing = [field for field in required if not spec.get(field)]
        if missing:
            raise ArtifactManifestError(f"generated artifact missing fields: {', '.join(missing)}")
        path = self._confined_path(spec["path"])
        if not path.is_file():
            raise ArtifactManifestError(f"generated artifact path does not exist: {path}")
        scope = spec.get("scope", "project")
        superseded = []
        for artifact in self.manifest["artifacts"]:
            if artifact.get("scope", "product") == scope and artifact.get("role") == spec["role"] and artifact.get("status") == "CURRENT":
                artifact["status"] = "SUPERSEDED"
                superseded.append(artifact["id"])
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entry = {
            "id": spec["id"],
            "scope": scope,
            "role": spec["role"],
            "logical_name": spec["logical_name"],
            "path": str(path.relative_to(self.product_root)),
            "media_type": spec["media_type"],
            "identity": spec.get("identity", spec["id"]),
            "revision": int(spec.get("revision", self.manifest["revision"] + 1)),
            "sha256": digest,
            "producer": producer,
            "dependencies": list(spec.get("dependencies", [])),
            "supersedes": list(spec.get("supersedes", [])) + superseded,
            "evidence_class": spec["evidence_class"],
            "status": "CURRENT",
            "accepted_by": spec.get("accepted_by"),
            "accepted_at": spec.get("accepted_at"),
        }
        self.manifest["artifacts"].append(entry)
        self.manifest["revision"] = int(self.manifest.get("revision", 0)) + 1
        write_manifest(self.manifest_path, self.manifest)
        self._validate_uniqueness()
        return dict(entry)

    def _confined_path(self, value: str) -> Path:
        candidate = Path(value)
        resolved = candidate.resolve() if candidate.is_absolute() else (self.product_root / candidate).resolve()
        try:
            resolved.relative_to(self.product_root)
        except ValueError as exc:
            raise ArtifactManifestError(f"artifact path escapes product root: {value}") from exc
        return resolved


def write_manifest(path: str | Path, manifest: Dict[str, Any]) -> Path:
    """Write YAML when PyYAML is available; JSON remains valid YAML 1.2 otherwise."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if yaml is not None:
        content = yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False)
    else:
        content = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    target.write_text(content, encoding="utf-8")
    return target
