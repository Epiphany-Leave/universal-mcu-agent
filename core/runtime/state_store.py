"""Confined, revisioned, atomic JSON persistence for runtime-owned state."""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Iterator

from .locking import file_lock


class StateStoreError(ValueError):
    pass


class RevisionConflict(StateStoreError):
    pass


class StateStore:
    """Store mutable runtime state below ``product/.mcu-agent/state``.

    Legacy relative names beginning with ``.mcu-agent/runtime`` or
    ``.mcu-agent/state`` are accepted, but are normalized into the canonical
    state directory rather than recreating the old mutable runtime tree.
    """

    def __init__(self, product_root: str | Path):
        self.product_root = Path(product_root).resolve()
        self.root = self.product_root / ".mcu-agent" / "state"
        self.lock_root = self.root / ".locks"
        self._ensure_root_confined()

    def path(self, relative_path: str | Path) -> Path:
        value = str(relative_path).replace("\\", "/")
        for prefix in (".mcu-agent/state/", ".mcu-agent/runtime/"):
            if value.startswith(prefix):
                value = value[len(prefix):]
                break
        candidate = Path(value)
        windows_candidate = PureWindowsPath(value)
        if (
            candidate.is_absolute()
            or windows_candidate.is_absolute()
            or bool(windows_candidate.drive)
            or not value
            or ".." in candidate.parts
            or ".." in windows_candidate.parts
        ):
            raise StateStoreError(f"state path escapes canonical root: {relative_path}")
        target = self.root.joinpath(*candidate.parts)
        self._ensure_root_confined()
        # Existing symlink parents must not provide an escape hatch.
        parent = target.parent.resolve()
        try:
            parent.relative_to(self.root.resolve())
        except ValueError as exc:
            raise StateStoreError(f"state path escapes canonical root: {relative_path}") from exc
        return target

    def read(self, relative_path: str | Path, default: Any = None) -> Any:
        path = self.path(relative_path)
        if not path.exists():
            return deepcopy(default)
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateStoreError(f"cannot read state {path}: {exc}") from exc

    @contextmanager
    def lock(self, name: str = "transaction", *, timeout: float = 10.0) -> Iterator[None]:
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)
        lock_path = self.path(f".locks/{safe}.lock")
        with file_lock(lock_path, timeout=timeout):
            yield

    def write(
        self,
        relative_path: str | Path,
        value: Any,
        *,
        expected_revision: int | None = None,
        bump_revision: bool = True,
        acquire_lock: bool = True,
    ) -> Path:
        path = self.path(relative_path)
        if acquire_lock:
            with self.lock(f"path-{self._lock_key(relative_path)}"):
                return self._write_transaction(path, value, expected_revision, bump_revision)
        return self._write_transaction(path, value, expected_revision, bump_revision)

    def update(
        self,
        relative_path: str | Path,
        mutator: Callable[[Any], Any],
        *,
        default: Any = None,
        expected_revision: int | None = None,
    ) -> Any:
        with self.lock(f"path-{self._lock_key(relative_path)}"):
            current = self.read(relative_path, default)
            current_revision = self._revision(current)
            if expected_revision is not None and current_revision != expected_revision:
                raise RevisionConflict(
                    f"revision conflict for {relative_path}: expected {expected_revision}, found {current_revision}"
                )
            result = mutator(deepcopy(current))
            self.write(
                relative_path,
                result,
                expected_revision=current_revision,
                acquire_lock=False,
            )
            return result

    def _write_transaction(
        self, path: Path, value: Any, expected_revision: int | None, bump_revision: bool
    ) -> Path:
        current = None
        if path.exists():
            try:
                current = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise StateStoreError(f"cannot read state before write {path}: {exc}") from exc
        current_revision = self._revision(current)
        if expected_revision is not None and current_revision != expected_revision:
            raise RevisionConflict(
                f"revision conflict for {path}: expected {expected_revision}, found {current_revision}"
            )
        output = deepcopy(value)
        if bump_revision and isinstance(output, dict):
            supplied = output.get("revision")
            next_revision = current_revision + 1
            if supplied is not None and expected_revision is None and int(supplied) > next_revision:
                next_revision = int(supplied)
            output["revision"] = next_revision
            # Preserve the useful behavior that callers see the committed revision.
            if isinstance(value, dict):
                value["revision"] = next_revision
        data = (json.dumps(output, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self._atomic_replace(path, data)
        return path

    @staticmethod
    def _atomic_replace(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            # Directory fsync is unavailable on Windows; best effort on POSIX.
            try:
                descriptor = os.open(str(path.parent), os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            except (OSError, AttributeError):
                pass
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _ensure_root_confined(self) -> None:
        try:
            self.root.resolve().relative_to(self.product_root)
        except ValueError as exc:
            raise StateStoreError("canonical state root escapes product root through a symlink") from exc

    @staticmethod
    def _revision(value: Any) -> int:
        if not isinstance(value, dict):
            return 0
        try:
            return int(value.get("revision", 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _lock_key(value: str | Path) -> str:
        import hashlib
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:20]
