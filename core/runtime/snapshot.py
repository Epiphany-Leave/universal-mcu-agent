"""Filesystem baselines used to derive real Task changes."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Iterable

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None


_ALWAYS_EXCLUDED_DIRS = {".git", "__pycache__", ".pytest_cache"}


def configured_cache_paths(config_path: str | Path | None, project_root: str | Path) -> set[Path]:
    """Return only explicitly declared generated-cache directories.

    A fuzzy search for keys containing ``cache`` is unsafe: a repository could
    name the project root itself as a cache and make the integrity snapshot
    empty.  Supported declarations are ``snapshot.generated_cache_paths`` and
    the exact directory keys ``cache``, ``build_cache`` and ``generated_cache``.
    """
    if not config_path or yaml is None or not Path(config_path).is_file():
        return set()
    try:
        payload = yaml.safe_load(Path(config_path).read_text(encoding="utf-8-sig")) or {}
    except Exception as exc:
        raise ValueError(f"invalid snapshot configuration: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("snapshot configuration must be a mapping")
    from .contract_validator import validate_contract
    validate_contract(payload, "mcu-config.schema.json", label="Project config")
    values: list[str] = []
    snapshot = payload.get("snapshot") or {}
    if isinstance(snapshot, dict):
        declared = snapshot.get("generated_cache_paths") or []
        if isinstance(declared, list):
            values.extend(item for item in declared if isinstance(item, str))
    directories = payload.get("directories") or {}
    if isinstance(directories, dict):
        for key in ("cache", "build_cache", "generated_cache"):
            value = directories.get(key)
            if isinstance(value, str):
                values.append(value)

    root = Path(project_root).resolve()
    result = set()
    for value in values:
        normalized = value.replace("\\", "/")
        candidate = Path(normalized)
        windows_candidate = PureWindowsPath(value)
        if (
            candidate.is_absolute()
            or windows_candidate.is_absolute()
            or windows_candidate.drive
            or ".." in candidate.parts
            or ".." in windows_candidate.parts
        ):
            continue
        resolved = (root / candidate).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        if resolved == root:
            continue
        result.add(resolved)
    return result


def scan_tree(
    project_root: str | Path,
    *,
    config_path: str | Path | None = None,
    extra_excludes: Iterable[str | Path] = (),
) -> Dict[str, Dict[str, Any]]:
    """Snapshot files without following directory symlinks."""
    root = Path(project_root).resolve()
    excludes = configured_cache_paths(config_path, root)
    excludes.add((root / ".mcu-agent" / "state").resolve())
    excludes.add((root / ".mcu-agent" / "workspaces").resolve())
    for value in extra_excludes:
        path = Path(value)
        excludes.add(path.resolve() if path.is_absolute() else (root / path).resolve())
    result: Dict[str, Dict[str, Any]] = {}

    def excluded(path: Path, name: str, is_dir: bool) -> bool:
        if is_dir and name in _ALWAYS_EXCLUDED_DIRS:
            return True
        if not is_dir and (name.endswith(".pyc") or name == ".DS_Store"):
            return True
        resolved = path.resolve()
        return any(resolved == item or _within(resolved, item) for item in excludes)

    def walk(directory: Path) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            return
        for entry in entries:
            path = Path(entry.path)
            try:
                if entry.is_symlink():
                    if excluded(path, entry.name, False):
                        continue
                    target = os.readlink(path)
                    resolved_target = path.resolve()
                    result[path.relative_to(root).as_posix()] = {
                        "kind": "symlink",
                        "sha256": hashlib.sha256(target.encode("utf-8", errors="surrogatepass")).hexdigest(),
                        "target": target,
                        "escapes_root": not _within(resolved_target, root),
                    }
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if not excluded(path, entry.name, True):
                        walk(path)
                    continue
                if not entry.is_file(follow_symlinks=False) or excluded(path, entry.name, False):
                    continue
                digest = hashlib.sha256()
                size = 0
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        size += len(chunk)
                        digest.update(chunk)
                result[path.relative_to(root).as_posix()] = {
                    "kind": "file",
                    "sha256": digest.hexdigest(),
                    "size": size,
                }
            except (FileNotFoundError, PermissionError, OSError):
                # A concurrently replaced transient file is simply absent from this snapshot.
                continue

    walk(root)
    return result


def diff_snapshots(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, list[str]]:
    before_keys = set(before)
    after_keys = set(after)
    return {
        "added": sorted(after_keys - before_keys),
        "modified": sorted(path for path in before_keys & after_keys if before[path] != after[path]),
        "deleted": sorted(before_keys - after_keys),
    }


def changed_paths(changes: Dict[str, Iterable[str]]) -> list[str]:
    return sorted({path for category in ("added", "modified", "deleted") for path in changes.get(category, ())})


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
