#!/usr/bin/env python3
"""Validate MCU Agent lifecycle v2 JSON using the Python standard library."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Iterable, List, Set

VERSION = "2.0"
PHASE_STATUSES = {"PLANNED", "ACTIVE", "WAITING_EXTERNAL", "COMPLETED", "CANCELLED"}
PHASE_RESULTS = {"PASS", "PASS_WITH_DEBT", "BLOCKED", "FAILED"}
MODES = {"production", "prototype", "exploration"}
DEBT_STATES = {"OPEN", "EVIDENCE_SUBMITTED", "UNDER_REVIEW", "WAITING_EXTERNAL", "CONFIRMED", "CONFLICT", "REJECTED", "WAIVED_PROTOTYPE_ONLY", "CLOSED"}
ARTIFACT_STATUSES = {"CURRENT", "STALE", "SUPERSEDED", "REJECTED"}
EAR_STATES = {"PROPOSED", "APPROVED", "EXPIRED", "REVOKED", "VALIDATED"}


def _required(obj: Dict[str, Any], fields: Iterable[str], where: str, errors: List[str]) -> None:
    for field in fields:
        if field not in obj:
            errors.append(f"{where}: missing required field '{field}'")


def _strings(value: Any, *, nonempty: bool = False) -> bool:
    return isinstance(value, list) and (not nonempty or bool(value)) and all(isinstance(item, str) and item for item in value)


def _version(data: Dict[str, Any], where: str, errors: List[str]) -> None:
    if data.get("schema_version") != VERSION:
        errors.append(f"{where}.schema_version: expected {VERSION!r}")


def _safe_relative(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    normalized = value.replace("\\", "/")
    path = Path(normalized)
    return not path.is_absolute() and not PureWindowsPath(value).is_absolute() and ".." not in path.parts


def _validate_phase_v1(data: Dict[str, Any]) -> List[str]:
    """Validate legacy phase records during the one-release migration window."""
    errors: List[str] = []
    required = [
        "schema_version", "phase_id", "revision", "mode", "status", "result",
        "entry_criteria", "exit_criteria", "artifacts", "work_items",
        "requested_external_inputs", "resume_criteria", "successor_phase", "reopen",
    ]
    _required(data, required, "phase", errors)
    if not isinstance(data.get("phase_id"), str) or not data.get("phase_id"):
        errors.append("phase.phase_id: expected a non-empty string")
    if not isinstance(data.get("revision"), int) or isinstance(data.get("revision"), bool) or data.get("revision", 0) < 1:
        errors.append("phase.revision: expected an integer >= 1")
    if data.get("mode") not in MODES:
        errors.append(f"phase.mode: invalid value {data.get('mode')!r}")
    if data.get("status") not in PHASE_STATUSES:
        errors.append(f"phase.status: invalid value {data.get('status')!r}")
    if data.get("result") not in PHASE_RESULTS:
        errors.append(f"phase.result: invalid value {data.get('result')!r}")
    for field in ("entry_criteria", "exit_criteria", "requested_external_inputs", "resume_criteria"):
        if not _strings(data.get(field)):
            errors.append(f"phase.{field}: expected a string list")
    if not isinstance(data.get("artifacts"), dict):
        errors.append("phase.artifacts: expected an object")
    if not isinstance(data.get("work_items"), list):
        errors.append("phase.work_items: expected a list")
    if data.get("status") == "WAITING_EXTERNAL":
        if not _strings(data.get("requested_external_inputs"), nonempty=True):
            errors.append("phase.requested_external_inputs: required for WAITING_EXTERNAL")
        if not _strings(data.get("resume_criteria"), nonempty=True):
            errors.append("phase.resume_criteria: required for WAITING_EXTERNAL")
    return errors


def validate_phase(data: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["phase: expected an object"]
    if data.get("schema_version") == "1.0":
        return _validate_phase_v1(data)
    required = ["schema_version", "phase_id", "revision", "mode", "status", "result", "entry_criteria", "completion_criteria", "work_package_ids", "artifacts", "review_budget", "reopen"]
    _required(data, required, "phase", errors)
    _version(data, "phase", errors)
    if not isinstance(data.get("phase_id"), str) or not data.get("phase_id"):
        errors.append("phase.phase_id: expected a non-empty string")
    if not isinstance(data.get("revision"), int) or isinstance(data.get("revision"), bool) or data.get("revision", 0) < 1:
        errors.append("phase.revision: expected an integer >= 1")
    if data.get("mode") not in MODES:
        errors.append(f"phase.mode: invalid value {data.get('mode')!r}")
    if data.get("status") not in PHASE_STATUSES:
        errors.append(f"phase.status: invalid value {data.get('status')!r}")
    if data.get("result") not in PHASE_RESULTS:
        errors.append(f"phase.result: invalid value {data.get('result')!r}")
    if not _strings(data.get("entry_criteria")):
        errors.append("phase.entry_criteria: expected a string list")
    if not _strings(data.get("completion_criteria"), nonempty=True):
        errors.append("phase.completion_criteria: expected a non-empty string list")
    if not _strings(data.get("work_package_ids")):
        errors.append("phase.work_package_ids: expected a string list")
    if not isinstance(data.get("artifacts"), dict):
        errors.append("phase.artifacts: expected an object")
    budget = data.get("review_budget")
    if not isinstance(budget, dict):
        errors.append("phase.review_budget: expected an object")
    else:
        _required(budget, ["max_rounds", "exhausted_action"], "phase.review_budget", errors)
        if not isinstance(budget.get("max_rounds"), int) or budget.get("max_rounds", 0) < 1:
            errors.append("phase.review_budget.max_rounds: expected an integer >= 1")
        if budget.get("exhausted_action") != "CLOSE_WITH_DEBT":
            errors.append("phase.review_budget.exhausted_action: expected 'CLOSE_WITH_DEBT'")
    reopen = data.get("reopen")
    if reopen is not None and not isinstance(reopen, dict):
        errors.append("phase.reopen: expected null or an object")
    elif isinstance(reopen, dict):
        _required(reopen, ["reason", "authority", "previous_revision"], "phase.reopen", errors)
    return errors


def validate_debts(data: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["debts: expected an object"]
    _required(data, ["schema_version", "revision", "debts"], "debts", errors)
    _version(data, "debts", errors)
    if not isinstance(data.get("revision"), int) or data.get("revision", 0) < 1:
        errors.append("debts.revision: expected an integer >= 1")
    debts = data.get("debts")
    if not isinstance(debts, list):
        return errors + ["debts.debts: expected a list"]
    ids: Set[str] = set()
    for index, debt in enumerate(debts):
        where = f"debts.debts[{index}]"
        if not isinstance(debt, dict):
            errors.append(f"{where}: expected an object")
            continue
        fields = ["id", "state", "statement", "capability_id", "fact_names", "evidence_required", "blocked_task_ids", "dependencies", "sources", "acceptance_record", "owner"]
        _required(debt, fields, where, errors)
        debt_id = debt.get("id")
        if not isinstance(debt_id, str) or not debt_id:
            errors.append(f"{where}.id: expected a non-empty string")
        elif debt_id in ids:
            errors.append(f"{where}.id: duplicate value {debt_id!r}")
        ids.add(debt_id)
        state = debt.get("state")
        if state not in DEBT_STATES:
            errors.append(f"{where}.state: invalid value {state!r}")
        for field in ("fact_names", "evidence_required"):
            if not _strings(debt.get(field), nonempty=True):
                errors.append(f"{where}.{field}: expected a non-empty string list")
        for field in ("blocked_task_ids", "dependencies", "sources"):
            if not _strings(debt.get(field)):
                errors.append(f"{where}.{field}: expected a string list")
        if state == "CONFIRMED" and (not debt.get("sources") or not debt.get("acceptance_record")):
            errors.append(f"{where}: CONFIRMED requires sources and acceptance_record")
        if state == "WAITING_EXTERNAL" and not _strings(debt.get("requested_inputs"), nonempty=True):
            errors.append(f"{where}.requested_inputs: required for WAITING_EXTERNAL")
    return errors


def _cycle_errors(graph: Dict[str, List[str]]) -> List[str]:
    errors: List[str] = []
    visiting: Set[str] = set()
    visited: Set[str] = set()

    def visit(node: str, trail: List[str]) -> None:
        if node in visiting:
            start = trail.index(node) if node in trail else 0
            errors.append("artifacts: dependency cycle: " + " -> ".join(trail[start:] + [node]))
            return
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph.get(node, []):
            if dependency in graph:
                visit(dependency, trail + [node])
        visiting.remove(node)
        visited.add(node)

    for artifact_id in graph:
        visit(artifact_id, [])
    return errors


def validate_artifacts(data: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["artifacts: expected an object"]
    _required(data, ["schema_version", "revision", "artifacts"], "artifacts", errors)
    _version(data, "artifacts", errors)
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list):
        return errors + ["artifacts.artifacts: expected a list"]
    ids: Set[str] = set()
    graph: Dict[str, List[str]] = {}
    current_roles: Set[tuple[str, str]] = set()
    for index, artifact in enumerate(artifacts):
        where = f"artifacts.artifacts[{index}]"
        if not isinstance(artifact, dict):
            errors.append(f"{where}: expected an object")
            continue
        fields = ["id", "scope", "role", "logical_name", "path", "media_type", "identity", "revision", "sha256", "producer", "dependencies", "supersedes", "evidence_class", "status"]
        _required(artifact, fields, where, errors)
        artifact_id = artifact.get("id")
        if artifact_id in ids:
            errors.append(f"{where}.id: duplicate value {artifact_id!r}")
        ids.add(artifact_id)
        dependencies = artifact.get("dependencies", [])
        graph[artifact_id] = dependencies if isinstance(dependencies, list) else []
        if artifact.get("status") not in ARTIFACT_STATUSES:
            errors.append(f"{where}.status: invalid value {artifact.get('status')!r}")
        if not _safe_relative(artifact.get("path")):
            errors.append(f"{where}.path: expected a confined relative path")
        digest = artifact.get("sha256")
        if digest is not None and (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)):
            errors.append(f"{where}.sha256: expected null or 64 hexadecimal characters")
        if artifact.get("status") == "CURRENT":
            role_key = (artifact.get("scope"), artifact.get("role"))
            if role_key in current_roles:
                errors.append(f"{where}: duplicate CURRENT artifact for scope/role {role_key!r}")
            current_roles.add(role_key)
    for artifact_id, dependencies in graph.items():
        for dependency in dependencies:
            if dependency not in graph:
                errors.append(f"artifacts: {artifact_id!r} depends on missing artifact {dependency!r}")
    errors.extend(_cycle_errors(graph))
    return errors


def validate_ear(data: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["engineering_assumption: expected an object"]
    fields = ["schema_version", "id", "mode", "state", "task_ids", "scope", "assumption", "risks", "validation_method", "owner", "expires_at", "rollback", "approved_by"]
    _required(data, fields, "engineering_assumption", errors)
    _version(data, "engineering_assumption", errors)
    if data.get("mode") not in {"prototype", "exploration"}:
        errors.append("engineering_assumption.mode: must be prototype or exploration")
    if data.get("state") not in EAR_STATES:
        errors.append(f"engineering_assumption.state: invalid value {data.get('state')!r}")
    if not _strings(data.get("task_ids"), nonempty=True):
        errors.append("engineering_assumption.task_ids: expected a non-empty string list")
    try:
        expires = data.get("expires_at")
        if not isinstance(expires, str) or not expires:
            raise ValueError
        datetime.fromisoformat(expires.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        errors.append("engineering_assumption.expires_at: expected an ISO-8601 timestamp")
    if data.get("state") == "APPROVED" and not data.get("approved_by"):
        errors.append("engineering_assumption.approved_by: required when APPROVED")
    return errors


def validate_bundle(data: Any) -> List[str]:
    if not isinstance(data, dict):
        return ["bundle: expected an object"]
    errors: List[str] = []
    _required(data, ["phase", "evidence_debts", "artifact_manifest", "engineering_assumptions"], "bundle", errors)
    phase = data.get("phase", {})
    debts = data.get("evidence_debts", {})
    errors.extend(validate_phase(phase))
    errors.extend(validate_debts(debts))
    errors.extend(validate_artifacts(data.get("artifact_manifest", {})))
    ears = data.get("engineering_assumptions", [])
    if not isinstance(ears, list):
        errors.append("bundle.engineering_assumptions: expected a list")
        ears = []
    for index, ear in enumerate(ears):
        errors.extend(f"bundle.engineering_assumptions[{index}]: {error}" for error in validate_ear(ear))
    if isinstance(phase, dict) and phase.get("mode") == "production":
        if ears:
            errors.append("bundle: production phase cannot depend on engineering assumptions")
        for debt in debts.get("debts", []) if isinstance(debts, dict) else []:
            if isinstance(debt, dict) and debt.get("state") == "WAIVED_PROTOTYPE_ONLY":
                errors.append(f"bundle: production phase cannot use prototype-only waiver {debt.get('id')!r}")
    return errors


VALIDATORS = {"phase": validate_phase, "debt": validate_debts, "artifact": validate_artifacts, "ear": validate_ear, "bundle": validate_bundle}


def _print_result(errors: List[str]) -> int:
    print(json.dumps({"valid": not errors, "errors": errors}, ensure_ascii=False, indent=2))
    return 1 if errors else 0


def self_test() -> int:
    phase = {
        "schema_version": "2.0", "phase_id": "assessment", "revision": 1,
        "mode": "prototype", "status": "COMPLETED", "result": "PASS_WITH_DEBT",
        "entry_criteria": [], "completion_criteria": ["assessed"], "work_package_ids": [],
        "artifacts": {}, "review_budget": {"max_rounds": 2, "exhausted_action": "CLOSE_WITH_DEBT"}, "reopen": None,
    }
    valid = validate_phase(phase)
    invalid = validate_artifacts({"schema_version": "2.0", "revision": 1, "artifacts": [{"id": "x", "path": "../escape"}]})
    if valid or not any("confined relative path" in error for error in invalid):
        return _print_result(["self-test failed", *valid, *invalid])
    print(json.dumps({"valid": True, "version": VERSION, "tests": 2}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("self-test")
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("kind", choices=sorted(VALIDATORS))
    validate_parser.add_argument("path")
    args = parser.parse_args()
    if args.command == "self-test":
        return self_test()
    try:
        data = json.loads(Path(args.path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return _print_result([f"cannot load {args.path}: {exc}"])
    return _print_result(VALIDATORS[args.kind](data))


if __name__ == "__main__":
    sys.exit(main())
