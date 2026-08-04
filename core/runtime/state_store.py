"""Atomic JSON persistence for runtime-owned state."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class StateStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def read(self, relative_path: str, default: Any = None) -> Any:
        path = self.root / relative_path
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8-sig"))

    def write(self, relative_path: str, value: Any) -> Path:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
        return path
