"""Session-level host handshake for the shared MCU runtime."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


class SessionBootstrapError(ValueError):
    pass


ENTRY_FILES = {
    "claude-code": "CLAUDE.md",
    "codex": "AGENTS.md",
    "opencode": ".opencode/instructions/mcu-agent.md",
    "cursor": ".cursorrules",
}


class SessionBootstrap:
    """Validate and persist a one-session acknowledgement of runtime rules."""

    def start(self, product_root: str | Path, *, agent: str, mode: str = "production") -> Dict[str, Any]:
        root = Path(product_root).resolve()
        runtime = root / ".mcu-agent" / "runtime" / "cli.py"
        if not runtime.is_file():
            raise SessionBootstrapError(f"runtime is not installed: {runtime}")
        entry_relative = ENTRY_FILES.get(agent)
        if entry_relative is None:
            raise SessionBootstrapError(f"unsupported host agent: {agent}")
        entry = root / entry_relative
        if not entry.is_file():
            raise SessionBootstrapError(f"host entry file is missing: {entry}")

        session_id = f"session-{uuid.uuid4().hex[:12]}"
        record = {
            "schema_version": "2.0",
            "session_id": session_id,
            "agent": agent,
            "mode": mode,
            "product_root": str(root),
            "entry_file": str(entry),
            "runtime": str(runtime),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "handshake": "ACCEPTED",
            "directive": (
                "For every MCU request in this session, run runtime plan before replying, "
                "selecting a workflow, or editing files. Read the returned handler and "
                "execute only READY Tasks through TaskExecutor."
            ),
        }
        target = root / ".mcu-agent" / "runtime" / "sessions" / f"{session_id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(target)
        record["record_path"] = str(target)
        return record
