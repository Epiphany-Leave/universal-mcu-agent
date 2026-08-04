"""Evaluate capability facts at task granularity."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Tuple


SATISFIED_FACT_STATES = {"CONFIRMED", "AVAILABLE"}


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
        index = _capability_index(capabilities)
        assumption_records = list(assumptions)
        ready_count = 0
        blocked_count = 0

        for task in result.get("tasks", []):
            blockers: List[Dict[str, Any]] = []
            block_state = "BLOCKED_REQUIREMENT"
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
                    block_state = requirement.get("on_missing", "BLOCKED_REQUIREMENT")
                    blockers.append({"capability": requirement.get("capability"), "facts": missing})

            if blockers:
                task["state"] = block_state
                task["blockers"] = blockers
                task["waiting"] = "WAITING_EXTERNAL" if block_state in {"BLOCKED_HARDWARE", "BLOCKED_REQUIREMENT"} else None
                blocked_count += 1
            else:
                task["state"] = "READY"
                task.pop("blockers", None)
                task.pop("waiting", None)
                ready_count += 1

        if ready_count:
            result["state"] = "READY"
        elif blocked_count:
            result["state"] = "BLOCKED"
        else:
            result["state"] = "DONE"
        result["summary"] = {"ready": ready_count, "blocked": blocked_count}
        return result

    @staticmethod
    def _missing_facts(
        capability: Dict[str, Any] | None,
        required_facts: Iterable[str],
        allowed_assumptions: set[str] | None = None,
    ) -> List[str]:
        if not capability:
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
                except ValueError:
                    pass
            result.add(assumption["id"])
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
