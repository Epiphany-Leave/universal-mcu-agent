#!/usr/bin/env python3
"""Validate MCU Agent lifecycle JSON using only the Python standard library."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set


PHASE_STATUSES = {"PLANNED", "ACTIVE", "WAITING_EXTERNAL", "COMPLETED", "CANCELLED"}
PHASE_RESULTS = {"PASS", "PASS_WITH_DEBT", "BLOCKED", "FAILED"}
MODES = {"production", "prototype", "exploration"}
READINESS = {"READY", "BLOCKED_HARDWARE", "BLOCKED_CONFIG", "BLOCKED_ARCHITECTURE", "DISABLED"}
DEBT_STATES = {"OPEN", "EVIDENCE_SUBMITTED", "UNDER_REVIEW", "WAITING_EXTERNAL", "CONFIRMED", "CONFLICT", "REJECTED", "WAIVED_PROTOTYPE_ONLY", "CLOSED"}
EVIDENCE_CLASSES = {"HARDWARE_FACT", "DEVICE_DOCUMENTATION", "OFFICIAL_BOARD_PROFILE", "OFFICIAL_BOARD_VALIDATION", "IMPLEMENTATION_REFERENCE", "GENERATED_CONFIG", "UNTRUSTED_OR_MODIFIED"}
ARTIFACT_STATUSES = {"CURRENT", "STALE", "SUPERSEDED", "REJECTED"}


def _required(obj: Dict[str, Any], fields: Iterable[str], where: str, errors: List[str]) -> None:
    for field in fields:
        if field not in obj:
            errors.append(f"{where}: missing required field '{field}'")


def _nonempty_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item for item in value)


def validate_phase(data: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["phase: expected an object"]
    _required(data, ["schema_version", "phase_id", "revision", "mode", "status", "result", "entry_criteria", "exit_criteria", "artifacts", "work_items", "requested_external_inputs", "resume_criteria", "successor_phase", "reopen"], "phase", errors)
    if data.get("schema_version") != "1.0":
        errors.append("phase.schema_version: expected '1.0'")
    if not isinstance(data.get("phase_id"), str) or not data.get("phase_id"):
        errors.append("phase.phase_id: expected a non-empty string")
    if not isinstance(data.get("revision"), int) or data.get("revision", 0) < 1:
        errors.append("phase.revision: expected an integer >= 1")
    if data.get("mode") not in MODES:
        errors.append(f"phase.mode: invalid value {data.get('mode')!r}")
    if data.get("status") not in PHASE_STATUSES:
        errors.append(f"phase.status: invalid value {data.get('status')!r}")
    if data.get("result") not in PHASE_RESULTS:
        errors.append(f"phase.result: invalid value {data.get('result')!r}")
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, dict):
        errors.append("phase.artifacts: expected an object")
    else:
        _required(artifacts, ["consumed", "produced"], "phase.artifacts", errors)
        if data.get("status") == "COMPLETED" and not artifacts.get("closure"):
            errors.append("phase.artifacts.closure: required when status is COMPLETED")
    if data.get("status") == "WAITING_EXTERNAL":
        if not _nonempty_list(data.get("requested_external_inputs")):
            errors.append("phase.requested_external_inputs: non-empty list required for WAITING_EXTERNAL")
        if not _nonempty_list(data.get("resume_criteria")):
            errors.append("phase.resume_criteria: non-empty list required for WAITING_EXTERNAL")
    for field in ("entry_criteria", "exit_criteria", "work_items", "requested_external_inputs", "resume_criteria"):
        if not isinstance(data.get(field), list):
            errors.append(f"phase.{field}: expected a list")
    if data.get("reopen") is not None:
        reopen = data.get("reopen")
        if not isinstance(reopen, dict):
            errors.append("phase.reopen: expected null or an object")
        else:
            _required(reopen, ["reason", "authority", "previous_revision"], "phase.reopen", errors)
    work_ids: Set[str] = set()
    work_items = data.get("work_items", []) if isinstance(data.get("work_items", []), list) else []
    for index, item in enumerate(work_items):
        where = f"phase.work_items[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{where}: expected an object")
            continue
        _required(item, ["id", "readiness", "debt_ids"], where, errors)
        if item.get("id") in work_ids:
            errors.append(f"{where}.id: duplicate value {item.get('id')!r}")
        work_ids.add(item.get("id"))
        if item.get("readiness") not in READINESS:
            errors.append(f"{where}.readiness: invalid value {item.get('readiness')!r}")
    return errors


def validate_debts(data: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["debts: expected an object"]
    _required(data, ["schema_version", "revision", "debts"], "debts", errors)
    if data.get("schema_version") != "1.0":
        errors.append("debts.schema_version: expected '1.0'")
    if not isinstance(data.get("debts"), list):
        errors.append("debts.debts: expected a list")
    ids: Set[str] = set()
    debt_items = data.get("debts", []) if isinstance(data.get("debts", []), list) else []
    for index, debt in enumerate(debt_items):
        where = f"debts.debts[{index}]"
        if not isinstance(debt, dict):
            errors.append(f"{where}: expected an object")
            continue
        _required(debt, ["id", "state", "statement", "evidence_required", "dependencies", "sources", "acceptance_record"], where, errors)
        debt_id = debt.get("id")
        if debt_id in ids:
            errors.append(f"{where}.id: duplicate value {debt_id!r}")
        ids.add(debt_id)
        state = debt.get("state")
        if state not in DEBT_STATES:
            errors.append(f"{where}.state: invalid value {state!r}")
        if not isinstance(debt.get("statement"), str) or not debt.get("statement"):
            errors.append(f"{where}.statement: expected a non-empty string")
        if not _nonempty_list(debt.get("evidence_required")):
            errors.append(f"{where}.evidence_required: expected a non-empty string list")
        if state == "CONFIRMED" and (not _nonempty_list(debt.get("sources")) or not debt.get("acceptance_record")):
            errors.append(f"{where}: CONFIRMED requires sources and acceptance_record")
        if state == "WAITING_EXTERNAL" and not _nonempty_list(debt.get("requested_inputs")):
            errors.append(f"{where}.requested_inputs: non-empty list required for WAITING_EXTERNAL")
    return errors


def _cycle_errors(graph: Dict[str, List[str]]) -> List[str]:
    errors: List[str] = []
    visiting: Set[str] = set()
    visited: Set[str] = set()

    def visit(node: str, trail: List[str]) -> None:
        if node in visiting:
            start = trail.index(node)
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
    if data.get("schema_version") != "1.0":
        errors.append("artifacts.schema_version: expected '1.0'")
    if not isinstance(data.get("artifacts"), list):
        errors.append("artifacts.artifacts: expected a list")
    ids: Set[str] = set()
    graph: Dict[str, List[str]] = {}
    artifact_items = data.get("artifacts", []) if isinstance(data.get("artifacts", []), list) else []
    for index, artifact in enumerate(artifact_items):
        where = f"artifacts.artifacts[{index}]"
        if not isinstance(artifact, dict):
            errors.append(f"{where}: expected an object")
            continue
        _required(artifact, ["id", "path", "type", "revision", "sha256", "producer", "dependencies", "supersedes", "evidence_class", "status"], where, errors)
        artifact_id = artifact.get("id")
        if artifact_id in ids:
            errors.append(f"{where}.id: duplicate value {artifact_id!r}")
        ids.add(artifact_id)
        graph[artifact_id] = artifact.get("dependencies", []) if isinstance(artifact.get("dependencies"), list) else []
        if artifact.get("evidence_class") not in EVIDENCE_CLASSES:
            errors.append(f"{where}.evidence_class: invalid value {artifact.get('evidence_class')!r}")
        if artifact.get("status") not in ARTIFACT_STATUSES:
            errors.append(f"{where}.status: invalid value {artifact.get('status')!r}")
        for field in ("id", "path", "type", "producer"):
            if not isinstance(artifact.get(field), str) or not artifact.get(field):
                errors.append(f"{where}.{field}: expected a non-empty string")
        sha256 = artifact.get("sha256")
        if sha256 is not None and (not isinstance(sha256, str) or len(sha256) != 64 or any(char not in "0123456789abcdefABCDEF" for char in sha256)):
            errors.append(f"{where}.sha256: expected null or 64 hexadecimal characters")
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
    fields = ["schema_version", "id", "mode", "scope", "assumption", "source", "rationale", "risks", "validation_plan", "owner", "expires_at", "rollback"]
    _required(data, fields, "engineering_assumption", errors)
    if data.get("schema_version") != "1.0":
        errors.append("engineering_assumption.schema_version: expected '1.0'")
    if data.get("mode") not in {"prototype", "exploration"}:
        errors.append("engineering_assumption.mode: must be prototype or exploration")
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
            errors.append("bundle: Production phase cannot depend on engineering assumptions")
        for debt in debts.get("debts", []) if isinstance(debts, dict) else []:
            if isinstance(debt, dict) and debt.get("state") == "WAIVED_PROTOTYPE_ONLY":
                errors.append(f"bundle: Production phase cannot use prototype-only waiver {debt.get('id')!r}")
    known_debts = {debt.get("id") for debt in debts.get("debts", []) if isinstance(debt, dict)} if isinstance(debts, dict) else set()
    for item in phase.get("work_items", []) if isinstance(phase, dict) else []:
        if isinstance(item, dict):
            for debt_id in item.get("debt_ids", []):
                if debt_id not in known_debts:
                    errors.append(f"bundle: work item {item.get('id')!r} references missing debt {debt_id!r}")
    return errors


VALIDATORS = {"phase": validate_phase, "debt": validate_debts, "artifact": validate_artifacts, "ear": validate_ear, "bundle": validate_bundle}


def _load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _print_result(errors: List[str]) -> int:
    print(json.dumps({"valid": not errors, "errors": errors}, ensure_ascii=False, indent=2))
    return 1 if errors else 0


def self_test() -> int:
    phase = {"schema_version": "1.0", "phase_id": "assessment", "revision": 1, "mode": "production", "status": "COMPLETED", "result": "PASS_WITH_DEBT", "entry_criteria": ["baseline"], "exit_criteria": ["assessed"], "artifacts": {"consumed": ["hardware"], "produced": ["closure"], "closure": "closure.md"}, "work_items": [{"id": "lcd", "readiness": "BLOCKED_HARDWARE", "debt_ids": ["LCD-ID-01"]}], "requested_external_inputs": [], "resume_criteria": [], "successor_phase": "evidence-resolution", "reopen": None}
    debts = {"schema_version": "1.0", "revision": 1, "debts": [{"id": "LCD-ID-01", "state": "WAIVED_PROTOTYPE_ONLY", "statement": "LCD identity unknown", "evidence_required": ["module identity"], "dependencies": [], "sources": [], "acceptance_record": None}]}
    artifacts = {"schema_version": "1.0", "revision": 1, "artifacts": []}
    valid_errors = validate_phase(phase)
    invalid_errors = validate_bundle({"phase": phase, "evidence_debts": debts, "artifact_manifest": artifacts, "engineering_assumptions": []})
    if valid_errors or not any("prototype-only waiver" in error for error in invalid_errors):
        return _print_result(["self-test failed", *valid_errors, *invalid_errors])
    print(json.dumps({"valid": True, "tests": 2}, indent=2))
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
        data = _load_json(args.path)
    except (OSError, json.JSONDecodeError) as exc:
        return _print_result([f"cannot load {args.path}: {exc}"])
    return _print_result(VALIDATORS[args.kind](data))


if __name__ == "__main__":
    sys.exit(main())
