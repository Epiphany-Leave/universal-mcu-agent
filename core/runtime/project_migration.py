"""Explicit, reviewable existing-project migration proposals."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Dict

from .contract_validator import validate_contract
from .project_contract import ProjectContractManager
from .project_layout import create_project_layout, project_paths


class ProjectMigrationError(ValueError):
    pass


class ProjectMigrationService:
    def __init__(self, product_root: str | Path):
        self.product_root = Path(product_root).resolve()

    def propose(self, project_name: str) -> Dict[str, Any]:
        from tools.project_detector import ProjectDetector
        detected = ProjectDetector(str(self.product_root)).detect()
        paths = project_paths(self.product_root, project_name)
        candidates = []
        seen: set[str] = set()
        for item in detected.get("projects", []):
            relative = item.get("relative_root")
            if not isinstance(relative, str):
                continue
            relative = relative.replace("\\", "/")
            seen.add(relative)
            candidates.append({
                "relative_root": relative,
                "platform": item.get("platform"),
                "kind": item.get("kind"),
                "evidence": list(item.get("evidence", [])),
            })
        marker_patterns = ("*.ioc", "*.uvprojx", "sdkconfig", "platformio.ini", ".cproject")
        for pattern in marker_patterns:
            for marker in self.product_root.rglob(pattern):
                if any(part in {".git", "build", ".mcu-agent"} for part in marker.parts):
                    continue
                relative = marker.parent.resolve().relative_to(self.product_root).as_posix() or "."
                if relative in seen:
                    continue
                seen.add(relative)
                candidates.append({
                    "relative_root": relative,
                    "platform": detected.get("mcu_family") or None,
                    "kind": "detected-marker",
                    "evidence": [marker.relative_to(self.product_root).as_posix()],
                })
        body = {
            "schema_version": "1.0",
            "product_root": str(self.product_root),
            "project_name": project_name,
            "canonical_layout": {
                key: value.relative_to(self.product_root).as_posix()
                for key, value in paths.items() if key != "product_root"
            },
            "candidates": candidates,
            # Duplicate candidates are never selected automatically.  The user
            # edits this field to one listed relative_root before apply.
            "selected_project_root": None,
            "platform": detected.get("mcu_family") or None,
            "chip": detected.get("chip") or None,
            "requires_user_approval": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        proposal_id = hashlib.sha256(
            json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        proposal = {"proposal_id": f"migration-{proposal_id}", **body}
        validate_contract(proposal, "project-migration-proposal.schema.json", label="Migration proposal")
        return proposal

    def apply(self, proposal: Dict[str, Any], *, approved: bool) -> Dict[str, Any]:
        if not approved:
            raise ProjectMigrationError("migration apply requires explicit --approve")
        validate_contract(proposal, "project-migration-proposal.schema.json", label="Migration proposal")
        if Path(proposal["product_root"]).resolve() != self.product_root:
            raise ProjectMigrationError("migration proposal belongs to another product root")
        selected = proposal.get("selected_project_root")
        candidate_roots = {item.get("relative_root") for item in proposal.get("candidates", [])}
        if candidate_roots and selected not in candidate_roots:
            raise ProjectMigrationError("select exactly one listed project root before apply")
        canonical = project_paths(self.product_root, proposal["project_name"])
        destination = canonical["project_root"]
        before_snapshot = self._tree_snapshot()
        control_paths = [
            self.product_root / ".mcu-agent" / "project-contract.json",
            self.product_root / ".mcu-agent" / "policy.lock",
            self.product_root / ".mcu-agent" / "state" / "project-status.json",
        ]
        control_backups = {
            path: path.read_bytes() for path in control_paths if path.is_file() and not path.is_symlink()
        }
        moved_from = None
        manager = ProjectContractManager(self.product_root)
        had_contract = manager.contract_path.exists()
        had_policy = manager.policy_path.exists()
        if selected:
            source = self._confined(selected)
            if source != destination:
                if not source.is_dir():
                    raise ProjectMigrationError(f"selected project root is missing: {source}")
                if destination.exists():
                    raise ProjectMigrationError(f"canonical migration destination already exists: {destination}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                self._move(source, destination)
                moved_from = source
        try:
            paths = create_project_layout(self.product_root, proposal["project_name"])
            contract = manager.create(
                project_name=proposal["project_name"], paths=paths,
                platform=proposal.get("platform"), chip=proposal.get("chip"),
            )
        except BaseException as original_error:
            rollback_errors: list[str] = []
            try:
                if moved_from is not None and destination.exists() and not moved_from.exists():
                    moved_from.parent.mkdir(parents=True, exist_ok=True)
                    self._move(destination, moved_from)
                if not had_contract:
                    try: manager.contract_path.unlink()
                    except FileNotFoundError: pass
                if not had_policy:
                    try: manager.policy_path.unlink()
                    except FileNotFoundError: pass
                for path, data in control_backups.items():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                self._remove_entries_not_in(before_snapshot)
                after_snapshot = self._tree_snapshot()
                if after_snapshot != before_snapshot:
                    rollback_errors.append("filesystem snapshot differs after rollback")
            except BaseException as rollback_error:
                rollback_errors.append(str(rollback_error))
            if rollback_errors:
                raise ProjectMigrationError(
                    "migration failed and exact rollback failed: " + "; ".join(rollback_errors)
                ) from original_error
            raise
        return {
            "proposal_id": proposal["proposal_id"],
            "applied": True,
            "moved_from": str(moved_from) if moved_from else None,
            "project_root": str(paths["project_root"]),
            "contract": contract,
        }

    def _tree_snapshot(self) -> Dict[str, tuple[str, str]]:
        snapshot: Dict[str, tuple[str, str]] = {}
        for path in sorted(self.product_root.rglob("*"), key=lambda item: item.as_posix()):
            relative = path.relative_to(self.product_root)
            if relative.parts and relative.parts[0] == ".git":
                continue
            key = relative.as_posix()
            if path.is_symlink():
                snapshot[key] = ("symlink", os.readlink(path))
            elif path.is_file():
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                snapshot[key] = ("file", digest.hexdigest())
            elif path.is_dir():
                snapshot[key] = ("directory", "")
        return snapshot

    def _remove_entries_not_in(self, snapshot: Dict[str, tuple[str, str]]) -> None:
        current = list(self.product_root.rglob("*"))
        current.sort(key=lambda item: len(item.relative_to(self.product_root).parts), reverse=True)
        for path in current:
            relative = path.relative_to(self.product_root)
            if relative.parts and relative.parts[0] == ".git":
                continue
            if relative.as_posix() in snapshot:
                continue
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()

    def _confined(self, value: str) -> Path:
        candidate = Path(value.replace("\\", "/"))
        windows = PureWindowsPath(value)
        if candidate.is_absolute() or windows.is_absolute() or windows.drive or ".." in candidate.parts:
            raise ProjectMigrationError(f"migration path is not product-relative: {value}")
        resolved = (self.product_root / candidate).resolve()
        try: resolved.relative_to(self.product_root)
        except ValueError as exc: raise ProjectMigrationError(f"migration path escapes product root: {value}") from exc
        return resolved

    def _move(self, source: Path, destination: Path) -> None:
        try:
            git = subprocess.run(
                ["git", "-C", str(self.product_root), "rev-parse", "--show-toplevel"],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False, timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            git = None
        if git is not None and git.returncode == 0 and Path(git.stdout.strip()).resolve() == self.product_root:
            try:
                moved = subprocess.run(
                    ["git", "-C", str(self.product_root), "mv", str(source), str(destination)],
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=60,
                )
                if moved.returncode == 0:
                    return
            except (OSError, subprocess.SubprocessError):
                pass
        shutil.move(str(source), str(destination))
