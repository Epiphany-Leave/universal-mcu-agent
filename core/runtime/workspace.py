"""Git worktree isolation and verified patch promotion for governed claims."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict


class WorkspaceError(ValueError):
    pass


class ClaimWorkspaceManager:
    def __init__(self, product_root: str | Path):
        self.product_root = Path(product_root).resolve()
        self.workspace_root = self.product_root / ".mcu-agent" / "workspaces"

    def create(self, claim_id: str, canonical_project_root: str | Path) -> Dict[str, Any]:
        repo = self._repo_root()
        if repo != self.product_root:
            raise WorkspaceError("workspace mode requires product_root to equal the Git repository root")
        self._require_clean(repo)
        base_commit = self._git(repo, "rev-parse", "HEAD").strip()
        destination = self.workspace_root / claim_id
        if destination.exists():
            raise WorkspaceError(f"claim workspace already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        completed = self._run(repo, "worktree", "add", "--detach", str(destination), base_commit)
        if completed.returncode:
            raise WorkspaceError(f"cannot create claim worktree: {completed.stderr.strip()}")
        canonical_project = Path(canonical_project_root).resolve()
        try:
            relative_project = canonical_project.relative_to(self.product_root)
        except ValueError as exc:
            self.remove(destination)
            raise WorkspaceError("canonical project root is outside the product repository") from exc
        workspace_project = (destination / relative_project).resolve()
        if not workspace_project.is_dir():
            self.remove(destination)
            raise WorkspaceError(f"project root is absent from claim worktree: {workspace_project}")
        return {
            "workspace_root": str(destination.resolve()),
            "workspace_project_root": str(workspace_project),
            "canonical_project_root": str(canonical_project),
            "base_commit": base_commit,
        }

    def patch(self, workspace: str | Path) -> bytes:
        root = Path(workspace).resolve()
        self._ensure_workspace(root)
        # Intent-to-add makes untracked files visible in a binary git diff while
        # retaining their content solely in the worktree.
        added = self._run(root, "add", "-N", "--all")
        if added.returncode:
            raise WorkspaceError(f"cannot enumerate workspace changes: {added.stderr.strip()}")
        completed = subprocess.run(
            ["git", "-C", str(root), "diff", "--binary", "--no-ext-diff", "HEAD", "--"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if completed.returncode:
            raise WorkspaceError(f"cannot create workspace patch: {completed.stderr.decode(errors='replace').strip()}")
        return completed.stdout

    def patch_paths(self, workspace: str | Path) -> list[str]:
        root = Path(workspace).resolve()
        self._ensure_workspace(root)
        added = self._run(root, "add", "-N", "--all")
        if added.returncode:
            raise WorkspaceError(f"cannot enumerate workspace changes: {added.stderr.strip()}")
        completed = subprocess.run(
            ["git", "-C", str(root), "diff", "--name-only", "-z", "HEAD", "--"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if completed.returncode:
            raise WorkspaceError(completed.stderr.decode(errors="replace").strip())
        return sorted(
            item.decode("utf-8", errors="surrogateescape")
            for item in completed.stdout.split(b"\0") if item
        )

    def apply_patch(self, patch: bytes) -> None:
        if not patch:
            return
        self._require_clean(self.product_root)
        checked = subprocess.run(
            ["git", "-C", str(self.product_root), "apply", "--check", "--binary", "-"],
            input=patch, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if checked.returncode:
            raise WorkspaceError(f"workspace patch no longer applies: {checked.stderr.decode(errors='replace').strip()}")
        applied = subprocess.run(
            ["git", "-C", str(self.product_root), "apply", "--binary", "-"],
            input=patch, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if applied.returncode:
            raise WorkspaceError(f"cannot promote workspace patch: {applied.stderr.decode(errors='replace').strip()}")

    def reverse_patch(self, patch: bytes) -> None:
        if not patch:
            return
        subprocess.run(
            ["git", "-C", str(self.product_root), "apply", "--reverse", "--binary", "-"],
            input=patch, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )

    def current_head(self) -> str:
        return self._git(self._repo_root(), "rev-parse", "HEAD").strip()

    def remove(self, workspace: str | Path) -> None:
        root = Path(workspace).resolve()
        try:
            root.relative_to(self.workspace_root.resolve())
        except ValueError as exc:
            raise WorkspaceError("refusing to remove a path outside the managed workspace root") from exc
        if root.exists():
            completed = self._run(self.product_root, "worktree", "remove", "--force", str(root))
            if completed.returncode:
                shutil.rmtree(root, ignore_errors=True)
                self._run(self.product_root, "worktree", "prune")

    @staticmethod
    def digest(patch: bytes) -> str:
        return hashlib.sha256(patch).hexdigest()

    def _repo_root(self) -> Path:
        completed = self._run(self.product_root, "rev-parse", "--show-toplevel")
        if completed.returncode:
            raise WorkspaceError("workspace mode requires a Git repository")
        return Path(completed.stdout.strip()).resolve()

    def _require_clean(self, repo: Path) -> None:
        completed = self._run(repo, "status", "--porcelain=v1", "--untracked-files=all")
        if completed.returncode:
            raise WorkspaceError("cannot inspect Git worktree status")
        unsafe = []
        for line in completed.stdout.splitlines():
            value = line[3:].replace("\\", "/") if len(line) > 3 else line
            parts = value.replace("\\", "/").split("/")
            if value.startswith(".mcu-agent/state/") or value.startswith(".mcu-agent/workspaces/"):
                continue
            if "__pycache__" in parts or ".pytest_cache" in parts or value.endswith((".pyc", ".pyo")):
                continue
            unsafe.append(line)
        if unsafe:
            raise WorkspaceError(f"workspace mode requires a clean product repository: {unsafe[:10]}")

    def _ensure_workspace(self, root: Path) -> None:
        try:
            root.relative_to(self.workspace_root.resolve())
        except ValueError as exc:
            raise WorkspaceError("claim workspace escapes managed workspace root") from exc
        if not root.is_dir():
            raise WorkspaceError(f"claim workspace does not exist: {root}")

    @staticmethod
    def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(cwd), *args], text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=60,
        )

    @classmethod
    def _git(cls, cwd: Path, *args: str) -> str:
        completed = cls._run(cwd, *args)
        if completed.returncode:
            raise WorkspaceError(completed.stderr.strip() or "Git command failed")
        return completed.stdout
