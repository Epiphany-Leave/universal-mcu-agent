"""Validate lifecycle transitions instead of relying on prompt compliance."""

from __future__ import annotations

from typing import Dict, Set


TRANSITIONS: Dict[str, Dict[str, Set[str]]] = {
    "phase": {
        "PLANNED": {"ACTIVE", "CANCELLED"},
        "ACTIVE": {"WAITING_EXTERNAL", "COMPLETED", "CANCELLED"},
        "WAITING_EXTERNAL": {"ACTIVE", "COMPLETED", "CANCELLED"},
        "COMPLETED": set(),
        "CANCELLED": set(),
    },
    "work_package": {
        "PROPOSED": {"READY", "BLOCKED", "CANCELLED"},
        "READY": {"IN_PROGRESS", "BLOCKED", "CANCELLED"},
        "BLOCKED": {"READY", "CANCELLED"},
        "IN_PROGRESS": {"BLOCKED", "VALIDATING", "DONE", "CANCELLED"},
        "VALIDATING": {"IN_PROGRESS", "BLOCKED", "DONE", "CANCELLED"},
        "DONE": set(),
        "CANCELLED": set(),
    },
    "task": {
        "READY": {"IMPLEMENTING", "BLOCKED_HARDWARE", "BLOCKED_REQUIREMENT", "BLOCKED_CONFIG", "SKIPPED"},
        "BLOCKED_HARDWARE": {"READY", "SKIPPED"},
        "BLOCKED_REQUIREMENT": {"READY", "SKIPPED"},
        "BLOCKED_CONFIG": {"READY", "SKIPPED"},
        "IMPLEMENTING": {"VALIDATING", "FAILED", "BLOCKED_HARDWARE", "BLOCKED_CONFIG"},
        "VALIDATING": {"DONE", "FAILED", "IMPLEMENTING", "BLOCKED_HARDWARE"},
        "FAILED": {"IMPLEMENTING", "SKIPPED"},
        "DONE": set(),
        "SKIPPED": set(),
    },
}


class TransitionError(ValueError):
    pass


class TransitionEngine:
    def transition(self, kind: str, record: dict, target: str, *, reopen: dict | None = None) -> dict:
        if kind not in TRANSITIONS:
            raise TransitionError(f"unknown state machine: {kind}")
        current = record.get("state" if kind != "phase" else "status")
        if current == "COMPLETED" and kind == "phase" and reopen:
            required = {"reason", "authority", "previous_revision"}
            if not required.issubset(reopen) or record.get("revision", 0) <= reopen["previous_revision"]:
                raise TransitionError("phase reopen requires reason, authority, and an incremented revision")
            if target != "ACTIVE":
                raise TransitionError("a reopened phase must transition to ACTIVE")
        elif target not in TRANSITIONS[kind].get(current, set()):
            raise TransitionError(f"invalid {kind} transition: {current} -> {target}")

        field = "status" if kind == "phase" else "state"
        record[field] = target
        if reopen:
            record["reopen"] = reopen
        return record
