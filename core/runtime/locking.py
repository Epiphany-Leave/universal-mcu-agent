"""Portable, short-lived lock files for runtime state transactions."""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class LockTimeoutError(TimeoutError):
    pass


@contextmanager
def file_lock(path: str | Path, *, timeout: float = 10.0, stale_after: float = 120.0) -> Iterator[None]:
    """Acquire *path* using O_EXCL, which works on Windows and POSIX.

    The lock protects only state read/compare/write transactions. Callers must not
    hold it while invoking tools or scanning a project tree.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    deadline = time.monotonic() + timeout
    payload = json.dumps({"token": token, "pid": os.getpid(), "created": time.time()}).encode("utf-8")
    while True:
        try:
            fd = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                os.write(fd, payload)
                os.fsync(fd)
            finally:
                os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - target.stat().st_mtime > stale_after:
                    target.unlink()
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise LockTimeoutError(f"timed out acquiring state lock: {target}")
            time.sleep(0.02)
    try:
        yield
    finally:
        try:
            current = json.loads(target.read_text(encoding="utf-8"))
            if current.get("token") == token:
                target.unlink()
        except (FileNotFoundError, OSError, ValueError):
            pass
