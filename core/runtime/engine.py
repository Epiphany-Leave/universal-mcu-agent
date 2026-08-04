"""High-level runtime entry point."""

from __future__ import annotations

from typing import Any, Dict, Iterable

from .capability_gate import CapabilityGate
from .intent_manager import IntentManager
from .work_package_factory import WorkPackageFactory


class RuntimeEngine:
    def __init__(self) -> None:
        self.intents = IntentManager()
        self.packages = WorkPackageFactory()
        self.gates = CapabilityGate()

    def plan(
        self,
        user_input: str,
        *,
        mode: str = "production",
        platform: str | None = None,
        capabilities: Iterable[Dict[str, Any]] = (),
        assumptions: Iterable[Dict[str, Any]] = (),
    ) -> Dict[str, Any]:
        intent = self.intents.classify(user_input, mode=mode)
        work_package = self.packages.create(intent, platform=platform)
        gated = self.gates.evaluate(work_package, capabilities, assumptions)
        return {
            "schema_version": "2.0",
            "intent": intent,
            "work_packages": [gated],
            "next_tasks": [task["id"] for task in gated["tasks"] if task["state"] == "READY"],
            "blocked_tasks": [task["id"] for task in gated["tasks"] if task["state"].startswith("BLOCKED")],
        }

    def regate(
        self,
        work_package: Dict[str, Any],
        capabilities: Iterable[Dict[str, Any]],
        assumptions: Iterable[Dict[str, Any]] = (),
    ) -> Dict[str, Any]:
        """Recompute an existing package after capability evidence changes."""
        return self.gates.evaluate(work_package, capabilities, assumptions)
