"""Session-level host handshake for the shared MCU runtime."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from .state_store import StateStore


class SessionBootstrapError(ValueError):
    pass


ENTRY_FILES = {
    "claude-code": "CLAUDE.md",
    "codex": "AGENTS.md",
    "opencode": ".opencode/instructions/mcu-agent.md",
    "cursor": ".cursorrules",
    "pi": "AGENTS.md",
}


class SessionBootstrap:
    """Validate and persist a one-session acknowledgement of runtime rules."""

    def start(
        self,
        product_root: str | Path,
        *,
        agent: str = "auto",
        mode: str = "production",
        project_root: str | Path | None = None,
    ) -> Dict[str, Any]:
        root = Path(product_root).resolve()
        project = Path(project_root).resolve() if project_root else root
        try:
            project.relative_to(root)
        except ValueError as exc:
            raise SessionBootstrapError("project root escapes product root") from exc
        runtime = root / ".mcu-agent" / "runtime" / "cli.py"
        if not runtime.is_file():
            raise SessionBootstrapError(f"runtime is not installed: {runtime}")
        detected_by = "argument"
        if agent == "auto":
            agent, detected_by = self.detect_agent()
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
            "agent_detected_by": detected_by,
            "mode": mode,
            "product_root": str(root),
            "project_root": str(project),
            "entry_file": str(entry),
            "runtime": str(runtime),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "handshake": "ACCEPTED",
            "directive": (
                "For every MCU request in this session, run runtime plan before replying, "
                "selecting a workflow, or editing files. Claim READY Tasks by ID and "
                "finalize them through the lifecycle service."
            ),
        }
        target = StateStore(root).write(f"sessions/{session_id}.json", record)
        record["record_path"] = str(target)
        return record

    @staticmethod
    def detect_agent() -> tuple[str, str]:
        if os.environ.get("PI_CODING_AGENT"):
            return "pi", "PI_CODING_AGENT"
        value = os.environ.get("AI_AGENT", "").strip().lower().replace("_", "-")
        aliases = {
            "claude": "claude-code", "claude-code": "claude-code",
            "codex": "codex", "opencode": "opencode", "open-code": "opencode",
            "cursor": "cursor", "pi": "pi",
        }
        if value in aliases:
            return aliases[value], "AI_AGENT"
        # Codex does not guarantee a stable host environment variable.  The
        # unified AGENTS.md entry therefore uses Codex as the documented
        # fallback while pi remains positively identified by its variables.
        return "codex", "unified-entry-default"
