"""Evaluate capability facts at task granularity."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Tuple

from .config_loader import validate_assumptions, validate_capabilities


SATISFIED_FACT_STATES = {"CONFIRMED", "AVAILABLE"}
BLOCK_PRIORITY = {"BLOCKED_REQUIREMENT": 1, "BLOCKED_HARDWARE": 2, "BLOCKED_ARCHITECTURE": 3, "BLOCKED_CONFIG": 4}


def _capability_index(capabilities: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {item["id"]: item for item in capabilities if isinstance(item, dict) and item.get("id")}


class CapabilityGate:
    """Block only tasks whose declared requirements are unsatisfied."""

    def evaluate(
        self,
        work_package: Dict[str, Any],
        capabilities: Iterable[Dict[str, Any]],
        assumptions: Iterable[Dict[str, Any]] = (),
    ) -> Dict[str, Any]:
        result = deepcopy(work_package)
        capability_records = list(capabilities)
        validate_capabilities(capability_records)
        index = _capability_index(capability_records)
        assumption_records = list(assumptions)
        validate_assumptions(assumption_records)
        for task in result.get("tasks", []):
            # Regating is not a lifecycle transition.  Active and terminal task
            # states are immutable here, even when their capabilities change.
            current_state = task.get("state", "")
            if current_state != "READY" and not current_state.startswith("BLOCKED_"):
                continue
            blockers: List[Dict[str, Any]] = []
            block_states: List[str] = []
            allowed_assumptions = self._allowed_assumptions(result.get("mode"), task["id"], assumption_records)
            for requirement in task.get("requirements", []):
                if requirement.get("gate") == "OPTIONAL":
                    continue
                missing = self._missing_facts(
                    index.get(requirement.get("capability")),
                    requirement.get("facts", []),
                    allowed_assumptions,
                )
                if missing:
                    state = requirement.get("on_missing", "BLOCKED_REQUIREMENT")
                    block_states.append(state)
                    blockers.append({"capability": requirement.get("capability"), "facts": missing})

            if blockers:
                block_state = max(block_states, key=lambda state: BLOCK_PRIORITY.get(state, 0))
                task["state"] = block_state
                task["blockers"] = blockers
                if block_state in {"BLOCKED_HARDWARE", "BLOCKED_REQUIREMENT"}:
                    task["waiting"] = "WAITING_EXTERNAL"
                else:
                    task.pop("waiting", None)
            else:
                task["state"] = "READY"
                task.pop("blockers", None)
                task.pop("waiting", None)

        counts = {
            "ready": 0,
            "blocked": 0,
            "implementing": 0,
            "validating": 0,
            "done": 0,
            "failed": 0,
            "skipped": 0,
        }
        for task in result.get("tasks", []):
            state = task.get("state", "")
            if state == "READY":
                counts["ready"] += 1
            elif state.startswith("BLOCKED"):
                counts["blocked"] += 1
            elif state in {"IMPLEMENTING", "VALIDATING", "DONE", "FAILED", "SKIPPED"}:
                counts[state.lower()] += 1
        counts["active"] = counts["implementing"]
        counts["total"] = len(result.get("tasks", []))

        states = {task.get("state") for task in result.get("tasks", [])}
        terminal = {"DONE", "SKIPPED"}
        blocked = {state for state in states if isinstance(state, str) and state.startswith("BLOCKED_")}
        if states and states <= terminal:
            result["state"] = "DONE"
        elif "IMPLEMENTING" in states:
            result["state"] = "IN_PROGRESS"
        elif "VALIDATING" in states:
            result["state"] = "VALIDATING"
        elif "READY" in states:
            result["state"] = "IN_PROGRESS" if states & (terminal | {"FAILED"}) else "READY"
        elif states and states <= (blocked | terminal | {"FAILED"}):
            result["state"] = "BLOCKED"
        elif blocked:
            result["state"] = "BLOCKED"
        else:
            result["state"] = "DONE"
        result["summary"] = counts
        return result

    @staticmethod
    def _missing_facts(
        capability: Dict[str, Any] | None,
        required_facts: Iterable[str],
        allowed_assumptions: set[str] | None = None,
    ) -> List[str]:
        if not capability or capability.get("state") == "BLOCKED":
            return list(required_facts)
        facts = capability.get("facts", {})
        missing = []
        for name in required_facts:
            fact = facts.get(name, {})
            assumed = (
                isinstance(fact, dict)
                and fact.get("state") == "ASSUMED"
                and fact.get("ear_id") in (allowed_assumptions or set())
            )
            if not isinstance(fact, dict) or (fact.get("state") not in SATISFIED_FACT_STATES and not assumed):
                missing.append(name)
        return missing

    @staticmethod
    def _allowed_assumptions(mode: str | None, task_id: str, assumptions: Iterable[Dict[str, Any]]) -> set[str]:
        if mode not in {"prototype", "exploration"}:
            return set()
        now = datetime.now(timezone.utc)
        result = set()
        for assumption in assumptions:
            if assumption.get("state") != "APPROVED" or task_id not in assumption.get("task_ids", []):
                continue
            expires_at = assumption.get("expires_at")
            if expires_at:
                try:
                    expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                    if expires.tzinfo is None:
                        expires = expires.replace(tzinfo=timezone.utc)
                    if expires <= now:
                        continue
                except (TypeError, ValueError):
                    # Invalid expiry timestamps can never authorize an assumed
                    # hardware fact.  EAR handling is deliberately fail closed.
                    continue
            assumption_id = assumption.get("id")
            if assumption_id:
                result.add(assumption_id)
        return result

    def impact_view(self, work_packages: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, List[str]]]:
        """Derive blocks/allows without storing two mutable sources of truth."""
        impacts: Dict[str, Dict[str, List[str]]] = {}
        for package in work_packages:
            for task in package.get("tasks", []):
                target = impacts.setdefault(task["id"], {"blocks": [], "allows": []})
                if task.get("state", "").startswith("BLOCKED"):
                    target["blocks"].append(package["work_package_id"])
                else:
                    target["allows"].append(package["work_package_id"])
        return impacts
